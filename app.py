import os

# ChromeDB requires sqlite3 >= 3.35.0. 
try:
    __import__('pysqlite3')
    import sys
    sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
except ImportError:
    pass

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import models
import cv_utils
import email_utils
import rag_utils
import audio_utils
import translation_utils
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta

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
    
    # Live Analytics Data - All metrics are real and measurable
    total_contacts = db.query(models.Contact).count()
    total_tasks = db.query(models.Task).count()
    upcoming_meetings = db.query(models.Meeting).filter(models.Meeting.date_time > datetime.now()).count()
    
    # Today's completed tasks
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    completed_today = db.query(models.Task).filter(
        models.Task.status == "Completed",
        models.Task.due_date >= today_start.date()
    ).count()
    
    # Pending tasks
    pending_tasks = db.query(models.Task).filter(models.Task.status == "Pending").count()
    
    # Total voicemails received
    total_voicemails = db.query(models.VoicemailLog).count()
    
    # Total call logs
    total_calls = db.query(models.CallLog).count()
    
    # Unread messages
    unread_messages = db.query(models.Message).filter(models.Message.read == False).count()
    
    # Total expenses tracked
    total_expenses = db.query(models.Expense).count()
    
    # Recent logs
    recent_logs = db.query(models.LogEntry).order_by(models.LogEntry.timestamp.desc()).limit(5).all()
    
    # Chart Data (Real task distribution)
    task_dist = {
        "Pending": pending_tasks,
        "In Progress": db.query(models.Task).filter(models.Task.status == "In Progress").count(),
        "Completed": db.query(models.Task).filter(models.Task.status == "Completed").count()
    }
    
    db.close()
    
    return render_template('index.html', 
                          total_contacts=total_contacts,
                          total_tasks=total_tasks,
                          upcoming_meetings=upcoming_meetings,
                          completed_today=completed_today,
                          pending_tasks=pending_tasks,
                          total_voicemails=total_voicemails,
                          total_calls=total_calls,
                          unread_messages=unread_messages,
                          total_expenses=total_expenses,
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
        action = request.form.get('action', 'ask')
        query = request.form.get('query', '')
        scope = request.form.get('scope', 'all')
        
        if action == 'ask':
            if query:
                answer = rag_utils.ask_seva_sakha(query, scope)
        elif action == 'remember':
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
    db = SessionLocal()
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
                db.add(m)
                
                # Log it
                log = models.LogEntry(event_type="meeting_added", description=f"Scheduled: {title}")
                db.add(log)
                db.commit()
                db.refresh(m)
                
                meeting_text = f"Meeting: {title}\nDate: {dt.strftime('%Y-%m-%d %H:%M')}\nParticipants: {participants}\n\nNotes:\n{notes}"
                res = rag_utils.index_into_memory("meeting", title, meeting_text, extra_meta={"participants": participants, "meeting_date": dt.strftime('%Y-%m-%d %H:%M')})
                flash(res, "info")
            except Exception as e:
                flash(f"Error: {e}", "danger")
                
        elif type_ == 'decision':
            title = request.form.get('title')
            date = request.form.get('date')
            text = request.form.get('text')
            try:
                d_date = datetime.strptime(date, '%Y-%m-%d').date() if date else datetime.now().date()
                d = models.Decision(title=title, date=d_date, description=text)
                db.add(d)
                log = models.LogEntry(event_type="decision_made", description=f"Decision: {title}")
                db.add(log)
                db.commit()
                db.refresh(d)
                
                decision_text = f"Decision: {title}\nDate: {d_date.strftime('%Y-%m-%d')}\n\nDetails:\n{text}"
                res = rag_utils.index_into_memory("decision", title, decision_text, extra_meta={"decision_date": d_date.strftime('%Y-%m-%d')})
                flash(res, "info")
            except Exception as e:
                flash(f"Error: {e}", "danger")
                
        elif type_ == 'travel':
            title = request.form.get('title')
            start = request.form.get('start')
            end = request.form.get('end')
            details = request.form.get('details')
            try:
                start_date = datetime.strptime(start, '%Y-%m-%d').date() if start else None
                end_date = datetime.strptime(end, '%Y-%m-%d').date() if end else None
                t = models.Travel(title=title, start_date=start_date, end_date=end_date, details=details)
                db.add(t)
                db.commit()
                db.refresh(t)
                
                travel_text = f"Trip: {title}\nStart Date: {start}\nEnd Date: {end}\n\nDetails:\n{details}"
                res = rag_utils.index_into_memory("travel", title, travel_text, extra_meta={"travel_start": start, "travel_end": end})
                flash(res, "info")
            except Exception as e:
                flash(f"Error: {e}", "danger")
                
        elif type_ == 'task':
            title = request.form.get('title')
            priority = request.form.get('priority', 'Medium')
            due_date_str = request.form.get('due_date')
            try:
                due = datetime.strptime(due_date_str, '%Y-%m-%d').date() if due_date_str else None
                task = models.Task(title=title, status="Pending", priority=priority, due_date=due)
                db.add(task)
                db.commit()
                db.refresh(task)
                
                task_text = f"Task: {title}\nPriority: {priority}\nDue Date: {due_date_str}\nStatus: Pending"
                rag_utils.index_into_memory("task", title, task_text, extra_meta={"priority": priority, "due_date": due_date_str})
                
                flash("Task added successfully.", "success")
            except Exception as e:
                flash(f"Error adding task: {e}", "danger")
    
    db.close()
    return render_template('items.html')

@app.route('/calls', methods=['GET', 'POST'])
def call_handler():
    db = SessionLocal()
    if request.method == 'POST':
        caller_name = request.form.get('caller_name')
        caller_number = request.form.get('caller_number')
        duration = request.form.get('duration', 0)
        notes = request.form.get('notes')
        call = models.CallLog(caller_name=caller_name, caller_number=caller_number, duration=int(duration), call_date=datetime.now(), notes=notes)
        db.add(call)
        db.commit()
        db.refresh(call)
        
        # Index to RAG
        call_text = f"Phone Call Log:\nCaller: {caller_name}\nNumber: {caller_number}\nDuration: {duration} seconds\nDate: {datetime.now().strftime('%Y-%m-%d %H:%M')}\nNotes: {notes}"
        rag_utils.index_into_memory("call_log", f"Call from {caller_name}", call_text, extra_meta={"caller": caller_name, "caller_number": caller_number, "duration": duration})
        
        flash("Call logged.", "success")
    
    call_logs = db.query(models.CallLog).order_by(models.CallLog.call_date.desc()).limit(20).all()
    db.close()
    return render_template('calls.html', call_logs=call_logs)

@app.route('/messages', methods=['GET', 'POST'])
def messages():
    db = SessionLocal()
    if request.method == 'POST':
        sender = request.form.get('sender')
        content = request.form.get('content')
        msg_type = request.form.get('type', 'sms')
        msg = models.Message(sender=sender, content=content, message_type=msg_type, message_date=datetime.now())
        db.add(msg)
        db.commit()
        db.refresh(msg)
        
        # Index to RAG
        msg_text = f"Message from {sender} ({msg_type}):\n{content}"
        rag_utils.index_into_memory("message", f"Message from {sender}", msg_text, extra_meta={"sender": sender, "message_type": msg_type, "message_date": datetime.now().strftime('%Y-%m-%d %H:%M')})
        
        flash("Message logged.", "success")
    
    message_list = db.query(models.Message).order_by(models.Message.message_date.desc()).limit(20).all()
    db.close()
    return render_template('messages.html', messages=message_list)

@app.route('/voicemail', methods=['GET', 'POST'])
def voicemail():
    db = SessionLocal()
    if request.method == 'POST':
        caller_name = request.form.get('caller_name')
        caller_number = request.form.get('caller_number')
        transcription = request.form.get('transcription')
        duration = request.form.get('duration', 0)
        vm = models.VoicemailLog(caller_name=caller_name, caller_number=caller_number, transcription=transcription, duration=int(duration), received_date=datetime.now())
        db.add(vm)
        db.commit()
        db.refresh(vm)
        
        # Index to RAG
        vm_text = f"Voicemail from {caller_name}:\nPhone: {caller_number}\nDuration: {duration} seconds\n\nTranscription:\n{transcription}"
        rag_utils.index_into_memory("voicemail", f"VM from {caller_name}", vm_text, extra_meta={"caller": caller_name, "caller_number": caller_number, "duration": duration})
        
        flash("Voicemail logged and indexed.", "success")
    
    voicemails = db.query(models.VoicemailLog).order_by(models.VoicemailLog.received_date.desc()).limit(20).all()
    db.close()
    return render_template('voicemail.html', voicemails=voicemails)

@app.route('/calendar', methods=['GET', 'POST'])
def calendar():
    db = SessionLocal()
    if request.method == 'POST':
        title = request.form.get('title')
        date_str = request.form.get('date')
        duration = request.form.get('duration', 60)
        description = request.form.get('description')
        attendees = request.form.get('attendees')
        location = request.form.get('location')
        try:
            dt = datetime.fromisoformat(date_str) if date_str else datetime.now()
            event = models.CalendarEvent(title=title, event_date=dt, duration=int(duration), description=description, attendees=attendees, location=location)
            db.add(event)
            db.commit()
            db.refresh(event)
            
            # Index to RAG
            event_text = f"Calendar Event: {title}\nDate: {dt.strftime('%Y-%m-%d %H:%M')}\nDuration: {duration} minutes\nLocation: {location}\nAttendees: {attendees}\nDescription: {description}"
            rag_utils.index_into_memory("calendar_event", title, event_text, extra_meta={"event_date": dt.strftime('%Y-%m-%d'), "duration": duration, "location": location, "attendees": attendees})
            
            flash("Event added to calendar.", "success")
        except Exception as e:
            flash(f"Error: {e}", "danger")
    
    events = db.query(models.CalendarEvent).filter(models.CalendarEvent.event_date >= datetime.now()).order_by(models.CalendarEvent.event_date).limit(20).all()
    db.close()
    return render_template('calendar.html', events=events)

@app.route('/expenses', methods=['GET', 'POST'])
def expenses():
    db = SessionLocal()
    if request.method == 'POST':
        title = request.form.get('title')
        amount = request.form.get('amount', 0)
        category = request.form.get('category')
        date_str = request.form.get('date')
        notes = request.form.get('notes')
        try:
            d = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else datetime.now().date()
            exp = models.Expense(title=title, amount=float(amount), category=category, date=d, notes=notes)
            db.add(exp)
            db.commit()
            db.refresh(exp)
            
            # Index to RAG
            exp_text = f"Expense: {title}\nAmount: ${amount}\nCategory: {category}\nDate: {d.strftime('%Y-%m-%d')}\nNotes: {notes}"
            rag_utils.index_into_memory("expense", title, exp_text, extra_meta={"amount": amount, "category": category, "expense_date": d.strftime('%Y-%m-%d')})
            
            flash("Expense logged.", "success")
        except Exception as e:
            flash(f"Error: {e}", "danger")
    
    expense_list = db.query(models.Expense).order_by(models.Expense.date.desc()).limit(50).all()
    total = sum(e.amount for e in expense_list) if expense_list else 0
    db.close()
    return render_template('expenses.html', expenses=expense_list, total=total)

@app.route('/research', methods=['GET', 'POST'])
def research():
    answer = ""
    topic = ""
    query = ""
    
    if request.method == 'POST':
        topic = request.form.get('topic', '')
        query = request.form.get('query', '')
        
        if query and topic:
            # First search internal memory
            internal_answer = rag_utils.ask_seva_sakha(query, scope="all")
            
            # Then ask Mistral for synthesis
            messages = [
                {
                    "role": "system",
                    "content": "You are a research assistant. Provide comprehensive, well-structured research findings."
                },
                {
                    "role": "user",
                    "content": f"Topic: {topic}\n\nQuery: {query}\n\nInternal Research:\n{internal_answer}\n\nProvide a detailed research report with key findings, recommendations, and action items."
                }
            ]
            
            answer = rag_utils.safe_call_llm(messages, max_new_tokens=800)
            
            # Index the research for future reference
            rag_utils.index_into_memory("research", f"Research: {topic}", answer, extra_meta={"research_topic": topic, "query": query})
            
    return render_template('research.html', topic=topic, query=query, answer=answer)

@app.route('/data-entry', methods=['GET', 'POST'])
def data_entry():
    result = ""
    
    if request.method == 'POST':
        form_data = request.form.get('form_data', '')
        target_system = request.form.get('target', '')
        
        if form_data:
            # Parse and validate form data
            messages = [
                {
                    "role": "system",
                    "content": "You are a data validation expert. Validate and clean the provided form data, identify any issues, and suggest corrections."
                },
                {
                    "role": "user",
                    "content": f"Please validate this form data for {target_system}:\n\n{form_data}\n\nProvide validation results and any required corrections."
                }
            ]
            
            result = rag_utils.safe_call_llm(messages, max_new_tokens=500)
            flash("Data entry validation completed.", "success")
            
    return render_template('data_entry.html', result=result)

@app.route('/reports', methods=['GET', 'POST'])
def reports():
    db = SessionLocal()
    report_data = {
        'total_meetings': db.query(models.Meeting).count(),
        'total_tasks': db.query(models.Task).count(),
        'completed_tasks': db.query(models.Task).filter(models.Task.status == 'Completed').count(),
        'total_expenses': db.query(models.Expense).count(),
        'total_amount': sum(e.amount for e in db.query(models.Expense).all()) or 0,
        'total_contacts': db.query(models.Contact).count(),
        'pending_tasks': db.query(models.Task).filter(models.Task.status == 'Pending').count(),
    }
    
    report_content = ""
    if request.method == 'POST':
        report_type = request.form.get('report_type', 'summary')
        
        # Generate report using LLM
        report_prompt = f"""
        Generate an executive {report_type} report with the following data:
        - Total Meetings: {report_data['total_meetings']}
        - Total Tasks: {report_data['total_tasks']}
        - Completed Tasks: {report_data['completed_tasks']}
        - Pending Tasks: {report_data['pending_tasks']}
        - Total Expenses: ${report_data['total_amount']:.2f}
        - Total Contacts: {report_data['total_contacts']}
        
        Provide insights, trends, and recommendations.
        """
        
        messages = [
            {
                "role": "system",
                "content": "You are an executive report generator. Create professional, actionable reports."
            },
            {
                "role": "user",
                "content": report_prompt
            }
        ]
        
        report_content = rag_utils.safe_call_llm(messages, max_new_tokens=1000)
        
    db.close()
    return render_template('reports.html', report_data=report_data, report_content=report_content)

@app.route('/translation', methods=['GET', 'POST'])
def translation():
    translated_text = ""
    
    if request.method == 'POST':
        text = request.form.get('text', '')
        source_lang = request.form.get('source_lang', 'en')
        target_lang = request.form.get('target_language', 'es')
        
        if text:
            translated_text = translation_utils.translate_text(text, target_lang)
            
            # Index translation for reference
            rag_utils.index_into_memory(
                "translation",
                f"Translation to {translation_utils.LANGUAGE_MAP.get(target_lang, target_lang)}",
                f"Source ({source_lang}):\n{text}\n\nTarget ({target_lang}):\n{translated_text}"
            )
            
            flash("Translation completed.", "success")
    
    return render_template('translation.html', translated_text=translated_text)

@app.route('/voice', methods=['GET', 'POST'])
def voice():
    response_text = ""
    
    if request.method == 'POST':
        action = request.form.get('action', 'command')
        command = request.form.get('command', '')
        
        if command:
            # Process voice command through LLM
            messages = [
                {
                    "role": "system",
                    "content": "You are a voice assistant. Process the user's voice command and provide a concise, actionable response."
                },
                {
                    "role": "user",
                    "content": command
                }
            ]
            
            response_text = rag_utils.safe_call_llm(messages, max_new_tokens=200)
            
            # Index voice interaction
            rag_utils.index_into_memory("voice_command", "Voice Command", f"Command: {command}\n\nResponse: {response_text}")
    
    return render_template('voice.html', response_text=response_text)

@app.route('/transcription', methods=['GET', 'POST'])
def transcription():
    transcribed_text = ""
    audio_duration = 0
    
    if request.method == 'POST':
        file = request.files.get('file')
        
        if file and file.filename:
            # Check file extension
            allowed_extensions = {'.wav', '.mp3', '.m4a', '.ogg', '.flac', '.wma'}
            file_ext = os.path.splitext(file.filename)[1].lower()
            
            if file_ext not in allowed_extensions:
                flash(f"Unsupported format: {file_ext}. Supported: {', '.join(allowed_extensions)}", "warning")
                return render_template('transcription.html', transcribed_text=transcribed_text, audio_duration=audio_duration)
            
            filename = secure_filename(file.filename)
            path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            
            try:
                file.save(path)
                print(f"File saved to: {path}")
                
                # Get audio duration
                audio_duration = audio_utils.get_audio_duration(path)
                print(f"Audio duration: {audio_duration} seconds")
                
                # Transcribe audio
                transcribed_text = audio_utils.transcribe_audio(path)
                
                if transcribed_text and not transcribed_text.startswith("Could not") and not transcribed_text.startswith("Transcription"):
                    # Index transcription
                    rag_utils.index_into_memory(
                        "transcription",
                        f"Audio: {filename}",
                        transcribed_text,
                        extra_meta={"audio_file": filename, "duration": audio_duration}
                    )
                    flash(f"✅ Audio transcribed successfully ({audio_duration}s).", "success")
                else:
                    flash(f"⚠️ Transcription issue: {transcribed_text}", "warning")
                    
            except Exception as e:
                print(f"Error in transcription route: {e}")
                flash(f"❌ Processing error: {str(e)}", "danger")
    
    return render_template('transcription.html', transcribed_text=transcribed_text, audio_duration=audio_duration)

if __name__ == '__main__':
    # Try to load LLM on startup if desired, or keep it lazy/fallback
    rag_utils.init_llm()
    app.run(debug=True, port=8000)
