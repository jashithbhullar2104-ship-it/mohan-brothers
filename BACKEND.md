# Mohan Brothers — Backend

A small, **zero-dependency** backend for the Mohan Brothers website. It serves the
static site and receives "Request a Quote" enquiries, storing them in a local
SQLite database and exposing a token-protected admin inbox.

Built entirely on the Python standard library — no `pip install`, no Node, no
external services. If you have `python3`, you can run it.

## Run

```bash
cd /Users/JB/Desktop/factory
python3 server.py
```

Then open:

| URL | What |
|-----|------|
| http://localhost:8000/        | The website |
| http://localhost:8000/admin   | Enquiry inbox (needs admin token) |
| http://localhost:8000/api/health | Health check |

On first start the server prints a random **admin token** to the console. Paste it
into the admin page to sign in. To keep it stable across restarts, set your own:

```bash
ADMIN_TOKEN="choose-a-long-secret" python3 server.py
```

## Configuration (environment variables)

| Variable | Default | Purpose |
|----------|---------|---------|
| `PORT`        | `8000`            | Listen port |
| `HOST`        | `0.0.0.0`         | Bind address |
| `ADMIN_TOKEN` | random (printed)  | Password for the admin inbox |
| `DB_PATH`     | `./enquiries.db`  | SQLite database file |
| `RATE_LIMIT`  | `8`               | Max enquiries per IP per hour |

### Email notifications (optional)

When `SMTP_HOST` is set, the workshop is emailed on **every** new enquiry. The send
happens in a background thread, so it never slows down or breaks the form — if email
fails, the enquiry is still saved and the customer still sees the thank-you message
(the failure is logged to the console).

| Variable | Default | Purpose |
|----------|---------|---------|
| `SMTP_HOST`     | — (off)                  | SMTP server hostname. **Enables email when set.** |
| `SMTP_PORT`     | `587`                    | SMTP port |
| `SMTP_USER`     | —                        | SMTP login username |
| `SMTP_PASS`     | —                        | SMTP password / app password |
| `SMTP_SECURITY` | `starttls`               | `starttls`, `ssl`, or `none` |
| `SMTP_FROM`     | `SMTP_USER`              | "From" address |
| `NOTIFY_TO`     | `mbros1937@rediffmail.com` | Recipient(s), comma-separated |

The notification email sets **`Reply-To`** to the customer's email (when provided),
so the team can just hit Reply to respond directly.

Example — Gmail (use an [App Password](https://support.google.com/accounts/answer/185833)):

```bash
ADMIN_TOKEN="your-long-secret" \
SMTP_HOST="smtp.gmail.com" SMTP_PORT=587 SMTP_SECURITY=starttls \
SMTP_USER="you@gmail.com" SMTP_PASS="your-app-password" \
SMTP_FROM="you@gmail.com" NOTIFY_TO="mbros1937@rediffmail.com" \
python3 server.py
```

Example — provider using SSL on port 465: set `SMTP_PORT=465 SMTP_SECURITY=ssl`.

## API

### `POST /api/enquiries` — public
Submit a quote enquiry. Accepts JSON or form-encoded bodies.

```json
{
  "name":    "Ravi Kumar",
  "company": "Tata Steel Ltd.",
  "phone":   "+91 94311 13328",
  "email":   "ravi@example.com",
  "service": "Gear Cutting / Gear Manufacturing",
  "message": "Need 4 helical gears, module 8, EN24 ...",
  "website": ""
}
```

- `name`, `company`, `phone`, `service`, `message` are required; `email` is optional.
- `website` is a **honeypot** — leave it empty. If filled, the request is treated as spam.
- Returns `201 {ok, id, message}` on success, `422 {error, fields}` on validation errors,
  `429` if rate-limited.

### `GET /api/enquiries` — admin (token required)
Lists enquiries, newest first. Auth via `Authorization: Bearer <token>` header or `?token=`.

Query params: `status` (`new` | `handled` | `archived`), `limit` (default 200, max 1000).

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" http://localhost:8000/api/enquiries
```

### `POST /api/enquiries/{id}/status` — admin (token required)
Update an enquiry's workflow status.

```bash
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"status":"handled"}' \
     http://localhost:8000/api/enquiries/1/status
```

## Data

Enquiries live in the `enquiries` table of the SQLite file (`enquiries.db` by default).
Inspect directly with:

```bash
sqlite3 enquiries.db "SELECT id, created_at, name, company, service FROM enquiries ORDER BY id DESC;"
```

The admin page also has an **Export CSV** button.

## Security notes

- The admin token is compared in constant time. Use a long random value in production
  and always serve the admin page over HTTPS (put this behind nginx/Caddy with TLS).
- `server.py`, the database file, and any `*.py` are never served as static files.
- Spam protection: honeypot field + per-IP hourly rate limit. For a public production
  deployment, consider also placing a reverse proxy / WAF in front.
- This uses Python's built-in HTTP server, which is fine for low/moderate traffic
  behind a reverse proxy. For high traffic, run behind nginx and/or multiple workers.

## Deploy (sketch)

1. Copy the project to a server with `python3`.
2. Set `ADMIN_TOKEN` and (optionally) `PORT`.
3. Run under a process manager, e.g. systemd:

   ```ini
   [Service]
   WorkingDirectory=/srv/mohan-brothers
   Environment=ADMIN_TOKEN=your-long-secret
   Environment=PORT=8000
   ExecStart=/usr/bin/python3 server.py
   Restart=always
   ```

4. Put nginx/Caddy in front for TLS and to serve `assets/` efficiently.
