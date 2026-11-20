"""GroundWire contact-form relay.

Accepts POST /api/contact (JSON) from groundwire.tech and emails it to the company mailbox through the
Hostinger SMTP server. Standard library only. Runs behind Caddy, never exposed directly.

Abuse controls: JSON-only with a small body limit, strict field validation, a hidden honeypot field,
a minimum fill time, per-IP and global rate limits. The visitor's address is only used as Reply-To;
mail is always sent From/To the authenticated company mailbox, so the relay cannot be used to spoof
or send to arbitrary recipients.
"""

from __future__ import annotations

import json
import logging
import os
import re
import smtplib
import ssl
import threading
import time
from collections import deque
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.hostinger.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_SECURITY = os.environ.get("SMTP_SECURITY", "ssl")  # ssl | starttls | none (none = local tests only)
SMTP_USER = os.environ.get("SMTP_USER", "contact@groundwire.tech")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
CONTACT_TO = os.environ.get("CONTACT_TO", SMTP_USER)
PORT = int(os.environ.get("PORT", "8080"))
PER_IP_LIMIT = int(os.environ.get("PER_IP_LIMIT", "5"))  # messages per IP per window
PER_IP_WINDOW_S = 600
GLOBAL_DAILY_LIMIT = int(os.environ.get("GLOBAL_DAILY_LIMIT", "200"))
MIN_FILL_SECONDS = float(os.environ.get("MIN_FILL_SECONDS", "3"))
MAX_BODY = 16 * 1024

TOPICS = {
    "AI agents & automation", "Knowledge / RAG assistant", "Evaluation & quality",
    "AI security review", "Integration / MCP", "Not sure yet",
}
EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9-]{1,63})+$")
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("contact")

_lock = threading.Lock()
_per_ip: dict[str, deque[float]] = {}
_global: deque[float] = deque()


class Invalid(ValueError):
    pass


def _one_line(v: object, field: str, max_len: int, required: bool) -> str:
    s = CTRL_RE.sub("", str(v or "")).replace("\r", " ").replace("\n", " ").strip()
    if required and not s:
        raise Invalid(f"{field} is required")
    if len(s) > max_len:
        raise Invalid(f"{field} is too long")
    return s


def validate(data: dict) -> dict:
    if not isinstance(data, dict):
        raise Invalid("invalid request")
    name = _one_line(data.get("name"), "Name", 100, True)
    company = _one_line(data.get("company"), "Company", 120, False)
    email = _one_line(data.get("email"), "Email", 200, True)
    if not EMAIL_RE.match(email):
        raise Invalid("Please enter a valid email address")
    topic = _one_line(data.get("topic"), "Topic", 60, False) or "Not sure yet"
    if topic not in TOPICS:
        topic = "Not sure yet"
    msg = CTRL_RE.sub("", str(data.get("message") or "")).replace("\r\n", "\n").strip()
    if len(msg) < 10:
        raise Invalid("Please add a few more details to your message")
    if len(msg) > 5000:
        raise Invalid("Message is too long (5,000 characters max)")
    return {"name": name, "company": company, "email": email, "topic": topic, "message": msg}


def is_bot(data: dict) -> bool:
    if str(data.get("website") or "").strip():  # hidden honeypot field
        return True
    try:
        elapsed = float(data.get("elapsed_ms", 0)) / 1000
    except (TypeError, ValueError):
        return True
    return elapsed < MIN_FILL_SECONDS


def allow(ip: str) -> bool:
    now = time.monotonic()
    with _lock:
        while _global and now - _global[0] > 86400:
            _global.popleft()
        q = _per_ip.setdefault(ip, deque())
        while q and now - q[0] > PER_IP_WINDOW_S:
            q.popleft()
        if len(q) >= PER_IP_LIMIT or len(_global) >= GLOBAL_DAILY_LIMIT:
            return False
        q.append(now)
        _global.append(now)
        if len(_per_ip) > 10000:  # bound memory
            for k in [k for k, v in _per_ip.items() if not v]:
                del _per_ip[k]
        return True


def build_message(f: dict, ip: str) -> EmailMessage:
    m = EmailMessage()
    who = f["name"] + (f" ({f['company']})" if f["company"] else "")
    m["Subject"] = f"Website enquiry: {f['topic']} from {who}"[:200]
    m["From"] = Address(display_name="GroundWire website", addr_spec=SMTP_USER)
    m["To"] = CONTACT_TO
    m["Reply-To"] = Address(display_name=f["name"], addr_spec=f["email"])
    m["Date"] = formatdate(localtime=False)
    m["Message-ID"] = make_msgid(domain=SMTP_USER.split("@")[-1])
    m.set_content(
        f"New enquiry from the groundwire.tech contact form\n\n"
        f"Name:    {f['name']}\nCompany: {f['company'] or '-'}\nEmail:   {f['email']}\nTopic:   {f['topic']}\n\n"
        f"{f['message']}\n\n--\nReply to this email to answer {f['name']} directly.\nSender IP: {ip}\n"
    )
    return m


def send(m: EmailMessage) -> None:
    if SMTP_SECURITY == "ssl":
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20, context=ssl.create_default_context()) as s:
            s.login(SMTP_USER, SMTP_PASSWORD)
            s.send_message(m)
        return
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
        if SMTP_SECURITY == "starttls":
            s.starttls(context=ssl.create_default_context())
        if SMTP_PASSWORD:
            s.login(SMTP_USER, SMTP_PASSWORD)
        s.send_message(m)


class Handler(BaseHTTPRequestHandler):
    server_version = "contact"
    sys_version = ""

    def _json(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _ip(self) -> str:
        # Caddy is the only client; it appends the real client address last.
        xff = self.headers.get("X-Forwarded-For", "")
        return (xff.split(",")[-1].strip() if xff else self.client_address[0])[:64]

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._json(200, {"ok": True, "configured": bool(SMTP_PASSWORD) or SMTP_SECURITY == "none"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/api/contact":
            return self._json(404, {"error": "not found"})
        if "application/json" not in self.headers.get("Content-Type", ""):
            return self._json(415, {"error": "JSON required"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length <= 0 or length > MAX_BODY:
            return self._json(413, {"error": "Message is too large"})
        ip = self._ip()
        try:
            data = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            return self._json(400, {"error": "invalid request"})
        if not isinstance(data, dict):
            return self._json(400, {"error": "invalid request"})
        if is_bot(data):
            log.info("dropped likely bot submission ip=%s", ip)
            return self._json(200, {"ok": True})  # look successful to bots
        try:
            fields = validate(data)
        except Invalid as e:
            return self._json(400, {"error": str(e)})
        if not allow(ip):
            log.warning("rate limited ip=%s", ip)
            return self._json(429, {"error": "Too many messages. Please try again later or email us directly."})
        if not SMTP_PASSWORD and SMTP_SECURITY != "none":
            log.error("SMTP_PASSWORD is not set; cannot send")
            return self._json(503, {"error": "Messaging is not configured yet. Please email us directly."})
        try:
            send(build_message(fields, ip))
        except (smtplib.SMTPException, OSError) as e:
            log.error("send failed: %s", type(e).__name__)
            return self._json(502, {"error": "We couldn't send your message. Please email us directly."})
        log.info("sent enquiry ip=%s topic=%s", ip, fields["topic"])
        return self._json(200, {"ok": True})

    def log_message(self, fmt: str, *args: object) -> None:  # quiet default access log
        return


if __name__ == "__main__":
    if not SMTP_PASSWORD and SMTP_SECURITY != "none":
        log.warning("SMTP_PASSWORD not set: the form will answer 503 until it is configured")
    log.info("contact relay listening on :%d, sending via %s:%d as %s", PORT, SMTP_HOST, SMTP_PORT, SMTP_USER)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()  # noqa: S104 - internal network only
