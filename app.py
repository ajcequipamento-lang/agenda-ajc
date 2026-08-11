
from flask import Flask, render_template, request, jsonify, send_from_directory
import sqlite3, re, os, smtplib, ssl, threading, time
from datetime import datetime, timedelta
from email.message import EmailMessage

app = Flask(__name__)
DB = "agenda_ajc_mobile.db"

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    con.execute("""CREATE TABLE IF NOT EXISTS clients(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        phone TEXT DEFAULT ''
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS tasks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        action_type TEXT NOT NULL,
        recipient TEXT DEFAULT '',
        subject TEXT DEFAULT '',
        message TEXT DEFAULT '',
        scheduled_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'AGENDADO'
    )""")
    con.commit()
    con.close()

def next_weekday(target_wd):
    now = datetime.now()
    days = (target_wd - now.weekday()) % 7
    if days == 0: days = 7
    return now + timedelta(days=days)

def parse_datetime_pt(text):
    now = datetime.now()
    lower = text.lower()
    target = now

    if "amanhã" in lower or "amanha" in lower:
        target = now + timedelta(days=1)
    elif "hoje" in lower:
        target = now

    weekdays = {
        "segunda":0, "terça":1, "terca":1, "quarta":2,
        "quinta":3, "sexta":4, "sábado":5, "sabado":5, "domingo":6
    }
    for name, wd in weekdays.items():
        if name in lower:
            target = next_weekday(wd)
            break

    d = re.search(r'\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b', lower)
    if d:
        day, month = int(d.group(1)), int(d.group(2))
        year = int(d.group(3) or now.year)
        try:
            target = datetime(year, month, day)
        except ValueError:
            pass

    hour, minute = 9, 0
    hm = re.search(r'\b(?:às|as)?\s*(\d{1,2})(?::(\d{2}))?\s*(?:h|horas|hora)?\b', lower)
    if hm:
        h, m = int(hm.group(1)), int(hm.group(2) or 0)
        if 0 <= h <= 23 and 0 <= m <= 59:
            hour, minute = h, m

    return target.replace(hour=hour, minute=minute, second=0, microsecond=0)

def lookup_client(text):
    con = db()
    rows = con.execute("SELECT * FROM clients ORDER BY LENGTH(name) DESC").fetchall()
    con.close()
    lower = text.lower()
    for r in rows:
        if r["name"].lower() in lower:
            return dict(r)
    return None

def interpret(text):
    lower = text.lower()
    is_email = ("email" in lower or "e-mail" in lower or "mande" in lower or "enviar" in lower)
    action_type = "EMAIL" if is_email else "LEMBRETE"
    client = lookup_client(text)

    recipient = client["email"] if client else ""
    explicit = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)
    if explicit:
        recipient = explicit.group(0)

    subject = "Mensagem da AJC"
    if "orçamento" in lower or "orcamento" in lower:
        subject = "Orçamento"
    elif "etiqueta" in lower:
        subject = "Etiquetas"
    elif "aprovação" in lower or "aprovacao" in lower:
        subject = "Aprovação de arte"
    elif "cobran" in lower or "pagamento" in lower:
        subject = "Financeiro"

    message = text.strip()
    m = re.search(r'\b(?:dizendo|falando|perguntando|que diga)\b(.+)$', text, re.I)
    if m:
        message = m.group(1).strip(" .")
    elif action_type == "EMAIL":
        message = "Olá! Esta é uma mensagem programada pela Agenda AJC."

    return {
        "title": "Enviar e-mail" if action_type == "EMAIL" else "Lembrete",
        "action_type": action_type,
        "recipient": recipient,
        "subject": subject if action_type == "EMAIL" else "",
        "message": message,
        "scheduled_at": parse_datetime_pt(text).isoformat(timespec="minutes"),
        "client_name": client["name"] if client else ""
    }

def send_email(task):
    host = os.getenv("AJC_SMTP_HOST", "")
    port = int(os.getenv("AJC_SMTP_PORT", "587"))
    user = os.getenv("AJC_EMAIL_USER", "")
    password = os.getenv("AJC_EMAIL_PASSWORD", "")
    sender = os.getenv("AJC_EMAIL_FROM", user)

    if not all([host, user, password, sender, task["recipient"]]):
        return False

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = task["recipient"]
    msg["Subject"] = task["subject"] or "Mensagem da AJC"
    msg.set_content(task["message"] or "")

    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=20) as s:
        s.starttls(context=ctx)
        s.login(user, password)
        s.send_message(msg)
    return True

def scheduler():
    while True:
        try:
            con = db()
            rows = con.execute("SELECT * FROM tasks WHERE status='AGENDADO'").fetchall()
            now = datetime.now()
            for row in rows:
                task = dict(row)
                if datetime.fromisoformat(task["scheduled_at"]) <= now:
                    if task["action_type"] == "EMAIL":
                        try:
                            status = "ENVIADO" if send_email(task) else "ERRO"
                        except Exception:
                            status = "ERRO"
                    else:
                        status = "CONCLUÍDO"
                    con.execute("UPDATE tasks SET status=? WHERE id=?", (status, task["id"]))
            con.commit()
            con.close()
        except Exception:
            pass
        time.sleep(20)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json")

@app.route("/sw.js")
def sw():
    return send_from_directory("static", "sw.js")

@app.route("/api/interpret", methods=["POST"])
def api_interpret():
    text = (request.json.get("text") or "").strip()
    if not text:
        return jsonify({"error":"Comando vazio"}), 400
    return jsonify(interpret(text))

@app.route("/api/clients", methods=["GET","POST"])
def clients():
    if request.method == "GET":
        con = db()
        rows = con.execute("SELECT * FROM clients ORDER BY name").fetchall()
        con.close()
        return jsonify([dict(r) for r in rows])
    d = request.json
    if not d.get("name") or not d.get("email"):
        return jsonify({"error":"Nome e e-mail são obrigatórios"}), 400
    con = db()
    con.execute("INSERT INTO clients(name,email,phone) VALUES(?,?,?)",
                (d["name"].strip(), d["email"].strip(), d.get("phone","").strip()))
    con.commit()
    con.close()
    return jsonify({"ok":True})

@app.route("/api/clients/<int:cid>", methods=["DELETE"])
def del_client(cid):
    con = db()
    con.execute("DELETE FROM clients WHERE id=?", (cid,))
    con.commit(); con.close()
    return jsonify({"ok":True})

@app.route("/api/tasks", methods=["GET","POST"])
def tasks():
    if request.method == "GET":
        con = db()
        rows = con.execute("SELECT * FROM tasks ORDER BY scheduled_at ASC").fetchall()
        con.close()
        return jsonify([dict(r) for r in rows])
    d = request.json
    if not d.get("scheduled_at") or not d.get("action_type"):
        return jsonify({"error":"Data e tipo são obrigatórios"}), 400
    con = db()
    cur = con.execute("""INSERT INTO tasks(title,action_type,recipient,subject,message,scheduled_at)
                         VALUES(?,?,?,?,?,?)""",
                      (d.get("title","Compromisso"), d["action_type"], d.get("recipient",""),
                       d.get("subject",""), d.get("message",""), d["scheduled_at"]))
    con.commit(); con.close()
    return jsonify({"ok":True,"id":cur.lastrowid})

@app.route("/api/tasks/<int:tid>", methods=["DELETE"])
def del_task(tid):
    con = db()
    con.execute("DELETE FROM tasks WHERE id=?", (tid,))
    con.commit(); con.close()
    return jsonify({"ok":True})

init_db()
threading.Thread(target=scheduler, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5050")), debug=False)
