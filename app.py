import os

# ChromeDB requires sqlite3 >= 3.35.0. 
# We patch it here to use the installed pysqlite3-binary (or pysqlite3)
try:
    __import__('pysqlite3')
    import sys
    sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
except ImportError:
    pass # Fallback to system sqlite3 if pysqlite3 not installed

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import models
import cv_utils
import email_utils
import rag_utils
from werkzeug.utils import secure_filename
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "supersecretkey") # Replace with env var in production

# Config
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

DB_PATH = "ai_secretary_app.db"

# Init components
SessionLocal = models.init_db(DB_PATH)
rag_utils.init_chroma()
# rag_utils.init_llm() # Uncomment to load heavy LLM, or let it fallback

# Global email creds (per session/lifetime of app for now, as per original script design)
# Ideally, this should be session-based or encrypted DB. For now keeping simple mirror of original.
EMAIL_CONFIG = {
    "host_imap": "imap.gmail.com",
    "port_imap": 993,
    "host_smtp": "smtp.gmail.com",
    "port_smtp": 587,
    "user": "",
    "pass": ""
}

@app.route('/')
def index():
    db = SessionLocal()
    
    # Analytics Data
    total_contacts = db.query(models.Contact).count()
    upcoming_meetings = db.query(models.Meeting).filter(models.Meeting.date_time > datetime.now()).count()
    pending_tasks = db.query(models.Task).filter(models.Task.status == "Pending").count()
    recent_logs = db.query(models.LogEntry).order_by(models.LogEntry.timestamp.desc()).limit(5).all()
    
    # Chart Data (Dummy distribution for demo if real data is scarce)
    task_dist = {
        "Pending": db.query(models.Task).filter(models.Task.status == "Pending").count(),
        "In Progress": db.query(models.Task).filter(models.Task.status == "In Progress").count(),
        "Completed": db.query(models.Task).filter(models.Task.status == "Completed").count()
    }
    
    db.close()
    
    return render_template('index.html', 
                          total_contacts=total_contacts,
                          upcoming_meetings=upcoming_meetings,
                          pending_tasks=pending_tasks,
                          recent_logs=recent_logs,
                          task_dist=task_dist)

@app.route('/email', methods=['GET', 'POST'])
def email_page():
    global EMAIL_CONFIG
    if request.method == 'POST':
        if 'save_creds' in request.form:
            EMAIL_CONFIG['user'] = request.form.get('email_user')
            EMAIL_CONFIG['pass'] = request.form.get('email_pass')
            flash("Email credentials saved temporarily.", "success")
        elif 'fetch' in request.form:
            limit = int(request.form.get('limit', 50))
            emails, err = email_utils.fetch_emails(
                EMAIL_CONFIG['host_imap'], EMAIL_CONFIG['port_imap'],
                EMAIL_CONFIG['user'], EMAIL_CONFIG['pass'], limit
            )
            if err:
                flash(f"Error fetching emails: {err}", "danger")
            else:
                # Index them
                count = 0
                for e in emails:
                    title = f"{e['subject']} — {e['from']}"
                    meta = {"email_from": e['from'], "email_date": e['date']}
                    full_text = f"Subject: {e['subject']}\nFrom: {e['from']}\nDate: {e['date']}\n\n{e['body']}"
                    rag_utils.index_into_memory("email", title, full_text, extra_meta=meta)
                    count += 1
                flash(f"Fetched and indexed {count} emails.", "success")
        elif 'send' in request.form:
            to = request.form.get('to')
            subject = request.form.get('subject')
            body = request.form.get('body')
            res = email_utils.send_email_smtp(
                EMAIL_CONFIG['host_smtp'], EMAIL_CONFIG['port_smtp'],
                EMAIL_CONFIG['user'], EMAIL_CONFIG['pass'], to, subject, body
            )
            flash(res, "info")
            
    return render_template('email.html', email_user=EMAIL_CONFIG['user'])

@app.route('/chat', methods=['GET', 'POST'])
def chat():
    answer = ""
    query = ""
    if request.method == 'POST':
        action = request.form.get('action', 'ask') # Default to ask
        query = request.form.get('query', '')
        scope = request.form.get('scope', 'all')
        
        if action == 'ask':
            if query:
                answer = rag_utils.ask_seva_sakha(query, scope)
        elif action == 'remember':
            # This allows the user to explicitly ask the assistant to remember the last exchange
            mem_content = request.form.get('mem_content', '')
            mem_title = request.form.get('mem_title', 'Conversation Memory')
            if mem_content:
                msg = rag_utils.index_into_memory("interaction", mem_title, mem_content)
                flash(msg, "success")
                
    return render_template('chat.html', answer=answer, query=query)

@app.route('/documents', methods=['GET', 'POST'])
def documents():
    summary = ""
    index_msg = ""
    if request.method == 'POST':
        file = request.files.get('file')
        if file and file.filename:
            filename = secure_filename(file.filename)
            path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(path)
            
            # Extract
            text = cv_utils.extract_pdf_with_ocr(path)
            if not text.strip():
                flash("No readable text extracted.", "danger")
            else:
                # Index
                index_msg = rag_utils.index_into_memory("document", filename, text)
                flash(index_msg, "success")
                
                # Summarize
                msgs = [
                    {"role": "system", "content": rag_utils.SYSTEM_PROMPT},
                    {"role": "user", "content": f"Provide executive summary (3 bullets + 3 risks + 3 actions):\n\n{text[:8000]}"}
                ]
                summary = rag_utils.safe_call_llm(msgs, max_new_tokens=500)
                
    return render_template('documents.html', summary=summary, index_msg=index_msg)

@app.route('/contacts', methods=['GET', 'POST'])
def contacts():
    db = SessionLocal()
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        org = request.form.get('organization')
        role = request.form.get('role')
        notes = request.form.get('notes')
        
        c = models.Contact(name=name, email=email, organization=org, role=role, notes=notes)
        db.add(c)
        db.commit()
        db.refresh(c)
        
        # Index
        full = f"Name: {name}\nEmail: {email}\nOrg: {org}\nRole: {role}\nNotes:\n{notes}"
        rag_utils.index_into_memory("contact", name, full, extra_meta={"contact_id": c.id})
        
        flash("Contact added and indexed.", "success")
        
    contacts_list = db.query(models.Contact).order_by(models.Contact.name).all()
    db.close()
    return render_template('contacts.html', contacts=contacts_list)

@app.route('/knowledge', methods=['GET', 'POST'])
def knowledge_hub():
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'quick_learn':
            title = request.form.get('title')
            content = request.form.get('content')
            if title and content:
                msg = rag_utils.index_into_memory("general_knowledge", title, content)
                flash(msg, "success")
            else:
                flash("Title and content are required.", "danger")
                
        elif action == 'file_upload':
            file = request.files.get('file')
            if file and file.filename:
                filename = secure_filename(file.filename)
                path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(path)
                
                # Use existing extraction logic
                text = cv_utils.extract_pdf_with_ocr(path)
                if text.strip():
                    msg = rag_utils.index_into_memory("document", filename, text)
                    flash(msg, "success")
                else:
                    flash("Could not extract text from document.", "danger")
        
    return render_template('knowledge.html')

@app.route('/items', methods=['GET', 'POST'])
def items():
    if request.method == 'POST':
        type_ = request.form.get('type') # meeting, decision, travel
        
        if type_ == 'meeting':
            title = request.form.get('title')
            date_str = request.form.get('date')
            participants = request.form.get('participants')
            notes = request.form.get('notes')
            
            # Save to DB
            try:
                dt = datetime.fromisoformat(date_str) if date_str else datetime.now()
                m = models.Meeting(title=title, date_time=dt, participants=participants, notes=notes)
                db = SessionLocal()
                db.add(m)
                
                # Log it
                log = models.LogEntry(event_type="meeting_added", description=f"Scheduled: {title}")
                db.add(log)
                db.commit()
                db.close()
            except Exception as e:
                print(f"DB Error: {e}")

            res = rag_utils.index_into_memory("meeting", title, 
                f"Title: {title}\nDate: {date_str}\nParticipants: {participants}\n\nNotes:\n{notes}",
                extra_meta={"participants": participants, "meeting_date": date_str})
            flash(res, "info")
            
        elif type_ == 'decision':
            # Decisions can go to Tasks or just RAG. For now, let's treat decision as a completed task or just RAG?
            # User asked for "Analytics". Let's log it as a LogEntry atleast.
            title = request.form.get('title')
            date = request.form.get('date')
            text = request.form.get('text')
            
            db = SessionLocal()
            log = models.LogEntry(event_type="decision_made", description=f"Decision: {title}")
            db.add(log)
            db.commit()
            db.close()
            
            res = rag_utils.index_into_memory("decision", title, 
                f"Decision: {title}\nDate: {date}\n\n{text}",
                extra_meta={"decision_date": date})
            flash(res, "info")
            
        elif type_ == 'task': # New type for Tasks
            title = request.form.get('title')
            due_date_str = request.form.get('due_date')
            priority = request.form.get('priority')
            
            try:
                due = datetime.strptime(due_date_str, '%Y-%m-%d').date() if due_date_str else None
                t = models.Task(title=title, status="Pending", priority=priority, due_date=due)
                db = SessionLocal()
                db.add(t)
                db.commit()
                db.close()
                flash("Task added to dashboard.", "success")
            except Exception as e:
                flash(f"Error adding task: {e}", "danger")

        elif type_ == 'travel':
            title = request.form.get('title')
            start = request.form.get('start')
            end = request.form.get('end')
            details = request.form.get('details')
            res = rag_utils.index_into_memory("travel", title,
                f"Trip: {title}\nStart: {start}\nEnd: {end}\n\nDetails:\n{details}",
                extra_meta={"travel_start": start, "travel_end": end})
            flash(res, "info")
            
    return render_template('items.html')

if __name__ == '__main__':
    # Try to load LLM on startup if desired, or keep it lazy/fallback
    rag_utils.init_llm()
    app.run(debug=True, port=8000)
