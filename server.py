#!/usr/bin/env python3
"""
Mohan Brothers — website backend.

Zero-dependency backend (Python standard library only). It:
  * serves the static site (index.html + assets/)
  * accepts "Request a Quote" enquiries  -> POST /api/enquiries
  * stores them in a local SQLite database (enquiries.db)
  * exposes a token-protected admin API + page to review enquiries

Run:
    python3 server.py
    # then open http://localhost:8000  (site)
    #          http://localhost:8000/admin  (enquiry inbox)

Config via environment variables (all optional):
    PORT          listen port               (default 8000)
    HOST          bind address              (default 0.0.0.0)
    ADMIN_TOKEN   admin password/token      (default: random, printed at startup)
    DB_PATH       sqlite file path          (default ./enquiries.db)
    RATE_LIMIT    max enquiries / IP / hour (default 8)

  Email notification (optional — enabled only when SMTP_HOST is set):
    SMTP_HOST     SMTP server hostname
    SMTP_PORT     SMTP port                 (default 587)
    SMTP_USER     SMTP username (login)
    SMTP_PASS     SMTP password / app password
    SMTP_SECURITY starttls | ssl | none     (default starttls)
    SMTP_FROM     "From" address            (default: SMTP_USER)
    NOTIFY_TO     recipient(s), comma-sep   (default mbros1937@rediffmail.com)
"""

import base64
import json
import os
import re
import secrets
import smtplib
import sqlite3
import ssl
import threading
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8000"))
HOST = os.environ.get("HOST", "0.0.0.0")
DB_PATH = os.environ.get("DB_PATH", os.path.join(ROOT, "enquiries.db"))
RATE_LIMIT = int(os.environ.get("RATE_LIMIT", "8"))  # per IP per hour
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN") or secrets.token_urlsafe(18)
ADMIN_TOKEN_FROM_ENV = bool(os.environ.get("ADMIN_TOKEN"))
ADMIN_USER = os.environ.get("ADMIN_USER", "MOHAN_ADMIN")

# Email notification config. Notifications are sent only when SMTP_HOST is set.
SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_SECURITY = os.environ.get("SMTP_SECURITY", "starttls").strip().lower()  # starttls|ssl|none
SMTP_FROM = os.environ.get("SMTP_FROM", "").strip() or SMTP_USER
NOTIFY_TO = [
    a.strip()
    for a in os.environ.get("NOTIFY_TO", "mbros1937@rediffmail.com").split(",")
    if a.strip()
]
EMAIL_ENABLED = bool(SMTP_HOST and NOTIFY_TO)

# The set of services offered, mirrored from the contact form. Free text is
# still accepted, but anything outside this list is flagged for review.
KNOWN_SERVICES = {
    "Mechanical Maintenance / Overhauling",
    "Precision Machining (Turning / Boring / Milling)",
    "Gear Cutting / Gear Manufacturing",
    "Gearbox Strip & Rebuild",
    "Heavy Fabrication / Structural Work",
    "Hydraulic Press Work",
    "Reverse Engineering",
    "Multiple Services / General Enquiry",
}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Loose phone check: digits, spaces, +, -, (), at least 7 digits.
PHONE_DIGITS_RE = re.compile(r"\d")

# ─────────────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────────────
_db_lock = threading.Lock()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db():
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS enquiries (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at  TEXT    NOT NULL,
                name        TEXT    NOT NULL,
                company     TEXT    NOT NULL,
                phone       TEXT    NOT NULL,
                email       TEXT,
                service     TEXT    NOT NULL,
                message     TEXT    NOT NULL,
                status      TEXT    NOT NULL DEFAULT 'new',
                ip          TEXT,
                user_agent  TEXT
            );
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_enquiries_created "
            "ON enquiries (created_at DESC);"
        )


# ─────────────────────────────────────────────────────────────
# Simple in-memory per-IP rate limiter (sliding 1-hour window)
# ─────────────────────────────────────────────────────────────
_hits = {}
_hits_lock = threading.Lock()


def rate_limited(ip):
    now = time.time()
    window = 3600
    with _hits_lock:
        bucket = [t for t in _hits.get(ip, []) if now - t < window]
        if len(bucket) >= RATE_LIMIT:
            _hits[ip] = bucket
            return True
        bucket.append(now)
        _hits[ip] = bucket
        return False


# ─────────────────────────────────────────────────────────────
# Email notification (sent in a background thread; never blocks the request)
# ─────────────────────────────────────────────────────────────
def _build_email(record, new_id):
    msg = EmailMessage()
    subject = f"New enquiry #{new_id}: {record['service']} — {record['company']}"
    msg["Subject"] = subject
    msg["From"] = formataddr(("Mohan Brothers Website", SMTP_FROM))
    msg["To"] = ", ".join(NOTIFY_TO)
    # Let the team hit "Reply" to answer the customer directly.
    if record.get("email"):
        msg["Reply-To"] = record["email"]

    phone = record["phone"]
    wa = "".join(ch for ch in phone if ch.isdigit())
    lines = [
        "New quote enquiry from the website:",
        "",
        f"  Name     : {record['name']}",
        f"  Company  : {record['company']}",
        f"  Phone    : {phone}",
        f"  Email    : {record.get('email') or '—'}",
        f"  Service  : {record['service']}",
        "",
        "  Message:",
        f"  {record['message']}",
        "",
        f"  Received : {record['created_at']}",
        f"  Enquiry  : #{new_id}",
    ]
    if wa:
        lines += ["", f"  WhatsApp : https://wa.me/{wa}"]
    msg.set_content("\n".join(lines))
    return msg


def _send_email(record, new_id):
    """Blocking send; called from a daemon thread."""
    try:
        msg = _build_email(record, new_id)
        if SMTP_SECURITY == "ssl":
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=20) as s:
                if SMTP_USER:
                    s.login(SMTP_USER, SMTP_PASS)
                s.send_message(msg)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
                s.ehlo()
                if SMTP_SECURITY == "starttls":
                    s.starttls(context=ssl.create_default_context())
                    s.ehlo()
                if SMTP_USER:
                    s.login(SMTP_USER, SMTP_PASS)
                s.send_message(msg)
        print(f"[email] enquiry #{new_id} notified -> {', '.join(NOTIFY_TO)}")
    except Exception as exc:  # never let email problems affect the site
        print(f"[email] FAILED to notify for enquiry #{new_id}: {exc!r}")


def notify_async(record, new_id):
    if not EMAIL_ENABLED:
        return
    threading.Thread(target=_send_email, args=(record, new_id), daemon=True).start()


# ─────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────
def clean(value, limit):
    if value is None:
        return ""
    return str(value).strip()[:limit]


def validate_enquiry(payload):
    """Return (data, errors). data is the sanitised record on success."""
    errors = {}

    name = clean(payload.get("name") or payload.get("co_name"), 120)
    company = clean(payload.get("company") or payload.get("co"), 160)
    phone = clean(payload.get("phone") or payload.get("ph"), 40)
    email = clean(payload.get("email") or payload.get("em"), 160)
    service = clean(payload.get("service") or payload.get("svc"), 120)
    message = clean(payload.get("message") or payload.get("msg"), 4000)

    if len(name) < 2:
        errors["name"] = "Please enter your name."
    if len(company) < 2:
        errors["company"] = "Please enter your company / organisation."
    if len(PHONE_DIGITS_RE.findall(phone)) < 7:
        errors["phone"] = "Please enter a valid phone number."
    if email and not EMAIL_RE.match(email):
        errors["email"] = "Please enter a valid email address."
    if len(service) < 2:
        errors["service"] = "Please select a service."
    if len(message) < 5:
        errors["message"] = "Please describe the job."

    data = {
        "name": name,
        "company": company,
        "phone": phone,
        "email": email,
        "service": service,
        "message": message,
    }
    return data, errors


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ─────────────────────────────────────────────────────────────
# HTTP handler
# ─────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    server_version = "MohanBrothers/1.0"

    # ---- helpers ----
    def _client_ip(self):
        fwd = self.headers.get("X-Forwarded-For")
        if fwd:
            return fwd.split(",")[0].strip()
        return self.client_address[0]

    def _send_json(self, status, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_file(self, path, content_type):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except FileNotFoundError:
            self._send_json(404, {"error": "Not found"})
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _authed(self):
        # HTTP Basic auth: username (ADMIN_USER) + password (ADMIN_TOKEN).
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(auth[6:].strip()).decode("utf-8")
        except Exception:
            return False
        user, sep, pw = decoded.partition(":")
        if not sep:
            return False
        return secrets.compare_digest(user, ADMIN_USER) and secrets.compare_digest(pw, ADMIN_TOKEN)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        if length > 1_000_000:  # 1 MB hard cap
            raise ValueError("Body too large")
        raw = self.rfile.read(length)
        ctype = self.headers.get("Content-Type", "")
        if "application/json" in ctype:
            return json.loads(raw.decode("utf-8") or "{}")
        # x-www-form-urlencoded fallback
        parsed = parse_qs(raw.decode("utf-8"))
        return {k: v[0] for k, v in parsed.items()}

    # ---- static file serving (safe, scoped to ROOT) ----
    def _serve_static(self, url_path):
        rel = url_path.lstrip("/")
        if rel == "":
            rel = "index.html"
        target = os.path.normpath(os.path.join(ROOT, rel))
        if not target.startswith(ROOT + os.sep) and target != ROOT:
            self._send_json(403, {"error": "Forbidden"})
            return
        if os.path.isdir(target):
            target = os.path.join(target, "index.html")
        if not os.path.isfile(target):
            self._send_json(404, {"error": "Not found"})
            return
        self._send_file(target, guess_type(target))

    # ---- routing ----
    def do_GET(self):
        route = urlparse(self.path).path

        if route == "/api/health":
            return self._send_json(200, {"ok": True, "time": now_iso()})

        if route == "/admin" or route == "/admin/":
            return self._send_file(os.path.join(ROOT, "admin.html"), "text/html; charset=utf-8")

        if route == "/api/enquiries":
            return self._admin_list()

        if route.startswith("/api/"):
            return self._send_json(404, {"error": "Unknown endpoint"})

        # never serve the database or this script over HTTP
        if route.lstrip("/") in {"server.py", os.path.basename(DB_PATH)} or route.endswith(
            (".db", ".db-wal", ".db-shm", ".py")
        ):
            return self._send_json(403, {"error": "Forbidden"})

        return self._serve_static(route)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        route = urlparse(self.path).path
        if route == "/api/enquiries":
            return self._create_enquiry()
        if re.fullmatch(r"/api/enquiries/\d+/status", route):
            return self._update_status(int(route.split("/")[3]))
        return self._send_json(404, {"error": "Unknown endpoint"})

    # ---- handlers ----
    def _create_enquiry(self):
        ip = self._client_ip()
        try:
            payload = self._read_body()
        except (ValueError, json.JSONDecodeError):
            return self._send_json(400, {"error": "Invalid request body"})

        # Honeypot: bots fill hidden fields humans never see.
        if clean(payload.get("website") or payload.get("hp"), 200):
            # Pretend success so bots don't learn anything.
            return self._send_json(200, {"ok": True})

        if rate_limited(ip):
            return self._send_json(
                429, {"error": "Too many enquiries from this address. Please try later or call us."}
            )

        data, errors = validate_enquiry(payload)
        if errors:
            return self._send_json(422, {"error": "Please check the form.", "fields": errors})

        record = {
            **data,
            "created_at": now_iso(),
            "status": "new",
            "ip": ip,
            "user_agent": clean(self.headers.get("User-Agent"), 400),
        }
        with _db_lock, get_db() as conn:
            cur = conn.execute(
                """INSERT INTO enquiries
                   (created_at, name, company, phone, email, service, message, status, ip, user_agent)
                   VALUES (:created_at,:name,:company,:phone,:email,:service,:message,:status,:ip,:user_agent)""",
                record,
            )
            new_id = cur.lastrowid

        flagged = "" if data["service"] in KNOWN_SERVICES else " (custom service)"
        print(f"[enquiry #{new_id}] {data['name']} / {data['company']} — {data['service']}{flagged}")
        notify_async(record, new_id)
        return self._send_json(
            201,
            {"ok": True, "id": new_id, "message": "Thank you — we have received your enquiry."},
        )

    def _admin_list(self):
        if not self._authed():
            return self._send_json(401, {"error": "Unauthorized"})
        qs = parse_qs(urlparse(self.path).query)
        status = (qs.get("status") or [""])[0]
        limit = min(int((qs.get("limit") or ["200"])[0] or "200"), 1000)
        sql = "SELECT * FROM enquiries"
        args = []
        if status in {"new", "handled", "archived"}:
            sql += " WHERE status = ?"
            args.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with get_db() as conn:
            rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
            counts = {
                r["status"]: r["n"]
                for r in conn.execute(
                    "SELECT status, COUNT(*) n FROM enquiries GROUP BY status"
                ).fetchall()
            }
        return self._send_json(200, {"enquiries": rows, "counts": counts, "total": sum(counts.values())})

    def _update_status(self, enquiry_id):
        if not self._authed():
            return self._send_json(401, {"error": "Unauthorized"})
        try:
            payload = self._read_body()
        except (ValueError, json.JSONDecodeError):
            return self._send_json(400, {"error": "Invalid request body"})
        status = clean(payload.get("status"), 20)
        if status not in {"new", "handled", "archived"}:
            return self._send_json(422, {"error": "status must be new, handled or archived"})
        with _db_lock, get_db() as conn:
            cur = conn.execute(
                "UPDATE enquiries SET status = ? WHERE id = ?", (status, enquiry_id)
            )
        if cur.rowcount == 0:
            return self._send_json(404, {"error": "Enquiry not found"})
        return self._send_json(200, {"ok": True, "id": enquiry_id, "status": status})

    # quieter, single-line logs
    def log_message(self, fmt, *args):
        print(f"{self.log_date_time_string()} {self._client_ip()} {fmt % args}")


# ─────────────────────────────────────────────────────────────
# Minimal MIME map (avoids platform mimetypes quirks)
# ─────────────────────────────────────────────────────────────
_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".txt": "text/plain; charset=utf-8",
    ".pdf": "application/pdf",
}


def guess_type(path):
    _, ext = os.path.splitext(path.lower())
    return _MIME.get(ext, "application/octet-stream")


def main():
    init_db()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print("─" * 56)
    print("  Mohan Brothers backend")
    print(f"  Site   : http://localhost:{PORT}/")
    print(f"  Admin  : http://localhost:{PORT}/admin")
    print(f"  API    : POST http://localhost:{PORT}/api/enquiries")
    print(f"  DB     : {DB_PATH}")
    if EMAIL_ENABLED:
        print(f"  Email  : ON  -> {', '.join(NOTIFY_TO)}  (via {SMTP_HOST}:{SMTP_PORT}, {SMTP_SECURITY})")
    else:
        print("  Email  : OFF (set SMTP_HOST + credentials to enable notifications)")
    print(f"  Admin user : {ADMIN_USER}")
    if ADMIN_TOKEN_FROM_ENV:
        print("  Admin pass : (loaded from ADMIN_TOKEN env var)")
    else:
        print(f"  Admin pass : {ADMIN_TOKEN}")
        print("  (set ADMIN_TOKEN env var to make this permanent)")
    print("─" * 56)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        httpd.shutdown()


if __name__ == "__main__":
    main()
