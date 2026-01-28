from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Date, Boolean
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

