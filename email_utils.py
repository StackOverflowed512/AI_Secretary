import imaplib
import email
import smtplib
from email.header import decode_header
from email.mime.text import MIMEText
from bs4 import BeautifulSoup
import time

def _decode_header_val(h):
    try:
        if not h: return ""
        dh = decode_header(h)
        parts = []
        for part, enc in dh:
            if isinstance(part, bytes):
                try:
                    parts.append(part.decode(enc or 'utf-8', errors='ignore'))
                except:
                    parts.append(part.decode('utf-8', errors='ignore'))
            else:
                parts.append(part)
        return "".join(parts)
    except Exception:
        return h or ""

def _get_text_from_msg(msg):
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get('Content-Disposition'))
            if ctype == 'text/plain' and 'attachment' not in disp:
                try:
                    text_part = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', errors='ignore')
                    return text_part
                except:
                    pass
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == 'text/html':
                try:
                    html = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', errors='ignore')
                    soup = BeautifulSoup(html, "html.parser")
                    return soup.get_text(separator="\n")
                except:
                    pass
    else:
        ctype = msg.get_content_type()
        payload = msg.get_payload(decode=True)
        if payload:
            try:
                return payload.decode(msg.get_content_charset() or 'utf-8', errors='ignore')
            except:
                try:
                    return payload.decode('utf-8', errors='ignore')
                except:
                    return str(payload)
    return ""

def fetch_emails(host, port, user, password, limit=50):
    if not user or not password:
        return [], "User/Pass missing"
    
    try:
        M = imaplib.IMAP4_SSL(host, port)
        M.login(user, password)
        M.select("INBOX")
        typ, data = M.search(None, 'ALL')
        if typ != 'OK':
            M.logout()
            return [], f"IMAP search failed: {typ}"
        
        all_ids = data[0].split()
        to_fetch = all_ids[-limit:]
        
        results = []
        for num in to_fetch:
            typ, msg_data = M.fetch(num, '(RFC822)')
            if typ != 'OK':
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            subject = _decode_header_val(msg.get('Subject', ''))
            from_ = _decode_header_val(msg.get('From', ''))
            date_ = msg.get('Date', '')
            body = _get_text_from_msg(msg)
            
            results.append({
                "subject": subject,
                "from": from_,
                "date": date_,
                "body": body
            })
            
        M.logout()
        return results, None # Success
    except Exception as e:
        return [], str(e)

def send_email_smtp(host, port, user, password, to_addr, subject, body):
    if not user or not password:
        return "User/Pass missing"
    
    try:
        msg = MIMEText(body or "", "plain")
        msg["From"] = user
        msg["To"] = to_addr
        msg["Subject"] = subject or "(no subject)"
        
        server = smtplib.SMTP(host, port, timeout=10)
        server.starttls()
        server.login(user, password)
        server.sendmail(user, [to_addr], msg.as_string())
        server.quit()
        return "✅ Email sent."
    except Exception as e:
        return f"❌ Email send failed: {e}"

def get_mail_stats(host, port, user, password):
    if not user or not password:
        return {"unread": 0, "total": 0, "error": "Creds missing"}
    
    try:
        M = imaplib.IMAP4_SSL(host, port)
        M.login(user, password)
        M.select("INBOX")
        
        # Unread
        typ, data = M.search(None, '(UNSEEN)')
        unread = len(data[0].split()) if data[0] else 0
        
        # Total
        typ, data = M.search(None, 'ALL')
        total = len(data[0].split()) if data[0] else 0
        
        M.logout()
        return {"unread": unread, "total": total, "error": None}
    except Exception as e:
        return {"unread": 0, "total": 0, "error": str(e)}

