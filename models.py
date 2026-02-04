from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Date, Boolean, Float
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime

Base = declarative_base()

class Contact(Base):
    __tablename__ = "contacts"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    email = Column(String)
    organization = Column(String)
    role = Column(String)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

class Meeting(Base):
    __tablename__ = "meetings"
    id = Column(Integer, primary_key=True)
    title = Column(String)
    date_time = Column(DateTime)
    participants = Column(String) # Comma separated
    notes = Column(Text)
    sentiment = Column(String, default="Neutral") # Fake analytics field

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    title = Column(String)
    status = Column(String, default="Pending") # Pending, In Progress, Completed
    priority = Column(String, default="Medium") # Low, Medium, High
    due_date = Column(Date)

class Decision(Base):
    __tablename__ = "decisions"
    id = Column(Integer, primary_key=True)
    title = Column(String)
    date = Column(Date)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

class Travel(Base):
    __tablename__ = "travel"
    id = Column(Integer, primary_key=True)
    title = Column(String)
    start_date = Column(Date)
    end_date = Column(Date)
    details = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

class Expense(Base):
    __tablename__ = "expenses"
    id = Column(Integer, primary_key=True)
    title = Column(String)
    amount = Column(Float)
    category = Column(String)
    date = Column(Date)
    receipt_path = Column(String)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

class CallLog(Base):
    __tablename__ = "call_logs"
    id = Column(Integer, primary_key=True)
    caller_name = Column(String)
    caller_number = Column(String)
    duration = Column(Integer)  # seconds
    call_date = Column(DateTime)
    notes = Column(Text)
    status = Column(String, default="Completed")

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    sender = Column(String)
    content = Column(Text)
    message_type = Column(String)  # sms, chat, etc
    message_date = Column(DateTime)
    read = Column(Boolean, default=False)



class CalendarEvent(Base):
    __tablename__ = "calendar_events"
    id = Column(Integer, primary_key=True)
    title = Column(String)
    event_date = Column(DateTime)
    duration = Column(Integer)  # minutes
    description = Column(Text)
    attendees = Column(String)
    location = Column(String)

class LogEntry(Base):
    __tablename__ = "logs"
    id = Column(Integer, primary_key=True)
    event_type = Column(String) # email_fetch, doc_upload, chat_query
    description = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow)

def init_db(db_path):
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)

class Voicemail(Base):
    __tablename__ = "voicemails"
    id = Column(Integer, primary_key=True)
    caller_name = Column(String)
    caller_number = Column(String)
    transcription = Column(Text)
    audio_path = Column(String, nullable=True)
    duration = Column(Integer, default=0)
    received_date = Column(DateTime, default=datetime.utcnow)
    is_read = Column(Boolean, default=False)

class EmailAccount(Base):
    __tablename__ = "email_accounts"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    password = Column(String, nullable=False) # In production, encrypt this
    imap_host = Column(String, default="imap.gmail.com")
    imap_port = Column(Integer, default=993)
    smtp_host = Column(String, default="smtp.gmail.com")
    smtp_port = Column(Integer, default=587)
    provider = Column(String, default="gmail") # gmail, outlook, etc.

