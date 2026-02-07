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
    total_voicemails = db.query(models.Voicemail).count()
    
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


FETCHED_EMAILS = {}

@app.route('/email', methods=['GET', 'POST'])
def email_page():
    db = SessionLocal()
    accounts = db.query(models.EmailAccount).all()
    
    account_id = request.args.get('account_id') or request.form.get('account_id')
    active_account = None
    if account_id:
        active_account = db.query(models.EmailAccount).filter(models.EmailAccount.id == int(account_id)).first()
    elif accounts:
        active_account = accounts[0]
    
    if request.method == 'POST':
        if 'add_account' in request.form:
            email = request.form.get('email')
            password = request.form.get('password')
            provider = request.form.get('provider')
            
            # Simple provider defaults
            imap_host = "imap.gmail.com"
            smtp_host = "smtp.gmail.com"
            if provider == 'outlook':
                imap_host = "outlook.office365.com"
                smtp_host = "smtp.office365.com"
            
            # Create account
            try:
                acc = models.EmailAccount(
                    email=email, password=password, provider=provider,
                    imap_host=imap_host, smtp_host=smtp_host
                )
                db.add(acc)
                db.commit()
                flash("Account added successfully.", "success")
                return redirect(url_for('email_page', account_id=acc.id))
            except Exception as e:
                flash(f"Error adding account: {e}", "danger")
                
        elif 'fetch' in request.form and active_account:
            limit = int(request.form.get('limit', 20))
            emails, err = email_utils.fetch_emails(
                active_account.imap_host, active_account.imap_port,
                active_account.email, active_account.password, limit
            )
            if err:
                flash(f"Error fetching emails: {err}", "danger")
            else:
                FETCHED_EMAILS[active_account.id] = emails
                # Update stats for this account immediately
                stats = email_utils.get_mail_stats(active_account.imap_host, active_account.imap_port, active_account.email, active_account.password)
                
        elif 'send' in request.form:
            account_id_send = request.form.get('account_id')
            if not account_id_send:
                # Default to first account if not specified
                acc_send = accounts[0] if accounts else None
            else:
                acc_send = db.query(models.EmailAccount).get(int(account_id_send))
            
            if acc_send:
                to = request.form.get('to')
                subject = request.form.get('subject')
                body = request.form.get('body')
                res = email_utils.send_email_smtp(
                    acc_send.smtp_host, acc_send.smtp_port,
                    acc_send.email, acc_send.password, to, subject, body
                )
                flash(res, "info" if "✅" in res else "danger")
            else:
                flash("No email account configured to send from.", "danger")
            
            # Redirect back to where we came from (e.g. voicemail)
            if request.headers.get('Referer'):
                return redirect(request.headers.get('Referer'))

    # Get stats for all accounts (for sidebar)
    account_stats = {}
    for acc in accounts:
        # Optimized: Only fetch if we don't have recent stats or for active account? 
        # For now, fetch live. If slow, user can comment out.
        stats = email_utils.get_mail_stats(acc.imap_host, acc.imap_port, acc.email, acc.password)
        account_stats[acc.id] = stats

    # Get emails for view
    emails = []
    if active_account:
        emails = FETCHED_EMAILS.get(active_account.id, [])
        
    active_email = None
    email_idx = request.args.get('email_idx')
    if email_idx is not None and emails:
        try:
            active_email = emails[int(email_idx)]
        except:
            pass

    db.close()
    return render_template('email.html', accounts=accounts, active_account=active_account, emails=emails, active_email=active_email, active_email_idx=int(email_idx) if email_idx is not None else None, account_stats=account_stats)

@app.route('/api/draft_email', methods=['POST'])
def draft_email_api():
    data = request.json
    prompt = data.get('prompt', '')
    email_meta = data.get('email', {})
    
    system_prompt = "You are an intelligent email assistant. Draft a professional email response."
    user_content = f"Instructions: {prompt}\n\nContext:\nSubject: {email_meta.get('subject')}\nFrom: {email_meta.get('sender')}\nBody Snippet: {email_meta.get('body')}"
    
    msgs = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content}
    ]
    
    draft = rag_utils.safe_call_llm(msgs, max_new_tokens=300)
    return jsonify({'draft': draft})

@app.route('/ai-assistant')
def ai_assistant():
    return render_template('ai_assistant.html')

@app.route('/api/ai_assistant', methods=['POST'])
def ai_assistant_api():
    """
    Comprehensive AI Assistant API that processes natural language commands
    and executes actions across all secretary features
    """
    data = request.json
    user_message = data.get('message', '')
    history = data.get('history', [])
    
    db = SessionLocal()
    
    try:
        # Detect intent and extract entities
        intent_prompt = f"""Analyze this user request and identify the intent and entities.
User: {user_message}

Respond in this exact format:
INTENT: [one of: view_tasks, create_task, view_meetings, create_meeting, view_emails, view_contacts, create_contact, view_expenses, create_expense, view_voicemails, search_all, general_question, create_decision, view_decisions]
ENTITIES: [relevant data like dates, names, amounts, etc.]
"""
        
        intent_msgs = [
            {"role": "system", "content": "You are an intent classifier for an AI secretary."},
            {"role": "user", "content": intent_prompt}
        ]
        
        intent_response = rag_utils.safe_call_llm(intent_msgs, max_new_tokens=150)
        
        # Parse intent
        intent = "general_question"
        if "INTENT:" in intent_response:
            intent_line = [line for line in intent_response.split('\n') if 'INTENT:' in line][0]
            intent = intent_line.split('INTENT:')[1].strip().lower()
        
        response_text = ""
        actions = []
        
        # Execute based on intent
        if intent == "view_tasks" or "task" in user_message.lower():
            tasks = db.query(models.Task).order_by(models.Task.due_date).limit(10).all()
            if tasks:
                response_text = "📋 **Your Tasks:**\n\n"
                for task in tasks:
                    status_emoji = "✅" if task.status == "Completed" else "⏳" if task.status == "In Progress" else "📌"
                    due_str = task.due_date.strftime('%b %d') if task.due_date else "No due date"
                    response_text += f"{status_emoji} **{task.title}** - {task.priority} priority (Due: {due_str})\n"
                actions.append({"label": "View All Tasks", "url": "/items"})
            else:
                response_text = "You don't have any tasks yet. Would you like me to create one?"
                
        elif intent == "create_task" or "create task" in user_message.lower() or "add task" in user_message.lower():
            response_text = "I can help you create a task! Please provide:\n• Task title\n• Priority (Low/Medium/High)\n• Due date\n\nOr you can use the quick form:"
            actions.append({"label": "Create Task", "url": "/items"})
            
        elif intent == "view_meetings" or "meeting" in user_message.lower():
            upcoming = db.query(models.Meeting).filter(
                models.Meeting.date_time >= datetime.now()
            ).order_by(models.Meeting.date_time).limit(5).all()
            
            if upcoming:
                response_text = "📅 **Upcoming Meetings:**\n\n"
                for meeting in upcoming:
                    date_str = meeting.date_time.strftime('%b %d at %I:%M %p')
                    response_text += f"• **{meeting.title}**\n  {date_str}\n  Participants: {meeting.participants or 'None listed'}\n\n"
                actions.append({"label": "View Calendar", "url": "/calendar"})
            else:
                response_text = "You have no upcoming meetings scheduled."
                actions.append({"label": "Schedule Meeting", "url": "/items"})
                
        elif intent == "view_emails" or "email" in user_message.lower():
            accounts = db.query(models.EmailAccount).all()
            if accounts:
                response_text = f"📧 **Email Accounts:**\n\nYou have {len(accounts)} email account(s) configured:\n"
                for acc in accounts:
                    response_text += f"• {acc.email} ({acc.provider})\n"
                actions.append({"label": "Open Email", "url": "/email"})
            else:
                response_text = "You haven't configured any email accounts yet."
                actions.append({"label": "Add Email Account", "url": "/email"})
                
        elif intent == "view_contacts" or "contact" in user_message.lower():
            contacts = db.query(models.Contact).order_by(models.Contact.name).limit(10).all()
            if contacts:
                response_text = f"👥 **Your Contacts** ({len(contacts)} shown):\n\n"
                for contact in contacts:
                    org_str = f" - {contact.organization}" if contact.organization else ""
                    response_text += f"• **{contact.name}**{org_str}\n  {contact.email or 'No email'}\n\n"
                actions.append({"label": "View All Contacts", "url": "/contacts"})
            else:
                response_text = "You don't have any contacts saved yet."
                actions.append({"label": "Add Contact", "url": "/contacts"})
                
        elif intent == "view_expenses" or "expense" in user_message.lower():
            expenses = db.query(models.Expense).order_by(models.Expense.date.desc()).limit(10).all()
            if expenses:
                total = sum(e.amount for e in expenses)
                response_text = f"💰 **Recent Expenses** (Total: ${total:.2f}):\n\n"
                for exp in expenses:
                    date_str = exp.date.strftime('%b %d')
                    response_text += f"• **{exp.title}** - ${exp.amount:.2f}\n  {exp.category} ({date_str})\n\n"
                actions.append({"label": "View All Expenses", "url": "/expenses"})
            else:
                response_text = "No expenses tracked yet."
                actions.append({"label": "Add Expense", "url": "/expenses"})
                
        elif intent == "view_voicemails" or "voicemail" in user_message.lower():
            voicemails = db.query(models.Voicemail).order_by(
                models.Voicemail.received_date.desc()
            ).limit(5).all()
            
            if voicemails:
                response_text = f"📞 **Recent Voicemails** ({len(voicemails)}):\n\n"
                for vm in voicemails:
                    date_str = vm.received_date.strftime('%b %d at %I:%M %p')
                    response_text += f"• **{vm.caller_name}** ({vm.caller_number})\n  {date_str}\n  \"{vm.transcription[:100]}...\"\n\n"
                actions.append({"label": "View All Voicemails", "url": "/voicemail"})
            else:
                response_text = "No voicemails to display."
                
        elif intent == "view_decisions" or "decision" in user_message.lower():
            decisions = db.query(models.Decision).order_by(
                models.Decision.date.desc()
            ).limit(5).all()
            
            if decisions:
                response_text = "📝 **Recent Decisions:**\n\n"
                for dec in decisions:
                    date_str = dec.date.strftime('%b %d, %Y')
                    response_text += f"• **{dec.title}**\n  {date_str}\n  {dec.description[:100]}...\n\n"
                actions.append({"label": "View All Decisions", "url": "/items"})
            else:
                response_text = "No decisions recorded yet."
                
        elif intent == "search_all" or any(word in user_message.lower() for word in ["search", "find", "look for"]):
            # Use RAG to search across all data
            search_result = rag_utils.ask_seva_sakha(user_message, scope="all")
            response_text = f"🔍 **Search Results:**\n\n{search_result}"
            
        else:
            # General question - use RAG with context
            # First, gather context
            context_parts = []
            
            # Recent tasks
            tasks = db.query(models.Task).filter(models.Task.status != "Completed").limit(3).all()
            if tasks:
                context_parts.append(f"Pending tasks: {', '.join([t.title for t in tasks])}")
            
            # Upcoming meetings
            meetings = db.query(models.Meeting).filter(
                models.Meeting.date_time >= datetime.now()
            ).limit(2).all()
            if meetings:
                context_parts.append(f"Upcoming meetings: {', '.join([m.title for m in meetings])}")
            
            # Recent contacts
            contacts_count = db.query(models.Contact).count()
            context_parts.append(f"Total contacts: {contacts_count}")
            
            context = "\n".join(context_parts)
            
            # Try RAG first
            rag_answer = rag_utils.ask_seva_sakha(user_message, scope="all")
            
            # Enhance with LLM
            enhance_msgs = [
                {"role": "system", "content": "You are a helpful AI secretary assistant. Provide concise, friendly responses."},
                {"role": "user", "content": f"User question: {user_message}\n\nContext:\n{context}\n\nRAG Answer: {rag_answer}\n\nProvide a helpful response:"}
            ]
            
            response_text = rag_utils.safe_call_llm(enhance_msgs, max_new_tokens=300)
            
            # Add helpful actions
            actions.append({"label": "Dashboard", "url": "/"})
            actions.append({"label": "Search Documents", "url": "/chat"})
        
        db.close()
        
        return jsonify({
            'response': response_text,
            'actions': actions,
            'intent': intent
        })
        
    except Exception as e:
        db.close()
        print(f"AI Assistant Error: {e}")
        return jsonify({
            'response': f"I encountered an error: {str(e)}. Please try rephrasing your question.",
            'actions': [{"label": "Dashboard", "url": "/"}],
            'intent': 'error'
        })


@app.route('/voicemail', methods=['GET', 'POST'])
def voicemail():
    db = SessionLocal()
    if request.method == 'POST':
        caller_name = request.form.get('caller_name', 'Unknown')
        caller_number = request.form.get('caller_number', '')
        transcription = request.form.get('transcription', '')
        duration = request.form.get('duration', 0)
        
        vm = models.Voicemail(
            caller_name=caller_name,
            caller_number=caller_number,
            transcription=transcription,
            duration=int(duration)
        )
        db.add(vm)
        db.commit()
        db.refresh(vm)
        
        # Index to RAG
        vm_text = f"Voicemail from {caller_name}:\nPhone: {caller_number}\nDuration: {duration} seconds\n\nTranscription:\n{transcription}"
        rag_utils.index_into_memory("voicemail", f"VM from {caller_name}", vm_text, extra_meta={"caller": caller_name, "caller_number": caller_number, "duration": duration})
        
        flash("Voicemail logged and indexed.", "success")
        return redirect(url_for('voicemail'))
    
    voicemails = db.query(models.Voicemail).order_by(models.Voicemail.received_date.desc()).all()
    accounts = db.query(models.EmailAccount).all()
    db.close()
    return render_template('voicemail.html', voicemails=voicemails, accounts=accounts)

@app.route('/voicemail/delete/<int:vm_id>', methods=['POST'])
def delete_voicemail(vm_id):
    db = SessionLocal()
    try:
        vm = db.query(models.Voicemail).get(vm_id)
        if vm:
            db.delete(vm)
            db.commit()
            flash("Voicemail archived.", "success")
        else:
            flash("Voicemail not found.", "danger")
    except Exception as e:
        flash(f"Error deleting voicemail: {e}", "danger")
    finally:
        db.close()
    return redirect(url_for('voicemail'))

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
            import json
            import re
            
            # Detect if it's a scheduling command
            intent_prompt = f"""
            Analyze this voice command: "{command}"
            
            Current date and time: {datetime.now().strftime('%Y-%m-%d %H:%M')}
            
            Is this a request to schedule a meeting, call, or event?
            If YES, return a JSON object with:
            {{
                "is_scheduling": true,
                "title": "Short title describing the event",
                "date": "YYYY-MM-DD HH:MM",
                "duration": 60,
                "attendees": "Names of people involved",
                "description": "Any additional details"
            }}
            
            If NO, return a JSON object with:
            {{
                "is_scheduling": false,
                "response": "A helpful response to the user's query"
            }}
            
            Return ONLY the valid JSON. Do not include markdown formatting or explanations.
            """
            
            msgs = [{"role": "user", "content": intent_prompt}]
            llm_response = rag_utils.safe_call_llm(msgs, max_new_tokens=400)
            
            try:
                # Clean response to ensure valid JSON (remove potential markdown wrappers)
                json_str = llm_response.strip()
                if json_str.startswith('```json'):
                    json_str = json_str[7:]
                if json_str.startswith('```'):
                    json_str = json_str[3:]
                if json_str.endswith('```'):
                    json_str = json_str[:-3]
                
                data = json.loads(json_str.strip())
                
                if data.get('is_scheduling'):
                    title = data.get('title', 'Untitled Event')
                    date_str = data.get('date')
                    duration = int(data.get('duration', 60))
                    attendees = data.get('attendees', '')
                    desc = data.get('description', '')
                    
                    try:
                        dt = datetime.strptime(date_str, '%Y-%m-%d %H:%M')
                    except (ValueError, TypeError):
                        # Fallback parsing
                        dt = datetime.now() + timedelta(days=1)
                        if date_str:
                             desc += f" [Note: Original date string '{date_str}' could not be parsed]"
                    
                    db = SessionLocal()
                    event = models.CalendarEvent(
                        title=title,
                        event_date=dt,
                        duration=duration,
                        description=desc,
                        attendees=attendees,
                        location="Voice Scheduled"
                    )
                    db.add(event)
                    db.commit()
                    
                    date_formatted = dt.strftime('%b %d at %I:%M %p')
                    response_text = f"✅ **Scheduled:** {title}\n📅 {date_formatted}\n👥 {attendees or 'No attendees'}"
                    
                    # Index to generic memory too
                    event_text = f"Calendar Event: {title}\nDate: {dt.strftime('%Y-%m-%d %H:%M')}\nAttendees: {attendees}\nDescription: {desc}"
                    rag_utils.index_into_memory("calendar_event", title, event_text)
                    
                    db.close()
                else:
                    response_text = data.get('response', llm_response)
                    
            except Exception as e:
                print(f"Error parsing voice intent: {e}")
                # Fallback to general chat if parsing fails
                response_text = rag_utils.ask_seva_sakha(command, scope="all")

            # Log interaction
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
