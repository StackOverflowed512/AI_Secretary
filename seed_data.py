import models
import random
from datetime import datetime, timedelta
from faker import Faker
import os

# Initialize Faker
fake = Faker()

# Init DB
db_path = "ai_secretary_app.db"
# Since models.py uses a relative path in init_db that assumes cwd is where app runs,
# and we run this script from root, it should be fine.
# But let's delete the old db to ensure schema update if migration isn't a thing (we used create_all matching new schema).
if os.path.exists(db_path):
    print("Removing old DB to refresh schema...")
    os.remove(db_path)

SessionLocal = models.init_db(db_path)
db = SessionLocal()

print("Seeding data...")

# 1. Contacts
roles = ["CEO", "CTO", "VP Marketing", "Project Manager", "Lead Developer", "Product Owner"]
orgs = ["TechCorp", "Innovate Inc", "FutureSystems", "Creative Solutions"]

for _ in range(15):
    c = models.Contact(
        name=fake.name(),
        email=fake.email(),
        organization=random.choice(orgs),
        role=random.choice(roles),
        notes=fake.text(max_nb_chars=100)
    )
    db.add(c)

# 2. Meetings
titles = ["Q1 Strategy Review", "Product Launch Sync", "Budget Approval", "Team Standup", "Client Kickoff"]
for _ in range(10):
    future = random.choice([True, False])
    if future:
        dt = datetime.now() + timedelta(days=random.randint(1, 14), hours=random.randint(9, 17))
    else:
        dt = datetime.now() - timedelta(days=random.randint(1, 30), hours=random.randint(9, 17))
        
    m = models.Meeting(
        title=random.choice(titles),
        date_time=dt,
        participants=f"{fake.name()}, {fake.name()}",
        notes=fake.text(max_nb_chars=200),
        sentiment=random.choice(["Positive", "Neutral", "Productive"])
    )
    db.add(m)

# 3. Tasks
task_titles = ["Review Q4 Report", "Update Website Assets", "Call Vendor X", "Prepare Slides for Board", "Fix Login Bug"]
statuses = ["Pending", "In Progress", "Completed"]
priorities = ["Low", "Medium", "High"]

for _ in range(12):
    t = models.Task(
        title=random.choice(task_titles),
        status=random.choice(statuses),
        priority=random.choice(priorities),
        due_date=datetime.now().date() + timedelta(days=random.randint(-5, 10))
    )
    db.add(t)

# 4. Logs
actions = ["email_fetched", "doc_uploaded", "meeting_added", "decision_made", "chat_query"]
for _ in range(20):
    l = models.LogEntry(
        event_type=random.choice(actions),
        description=fake.sentence(),
        timestamp=datetime.now() - timedelta(hours=random.randint(1, 100))
    )
    db.add(l)

db.commit()
db.close()
print("Data seeding completed!")
