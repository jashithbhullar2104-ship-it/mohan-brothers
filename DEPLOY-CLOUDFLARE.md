# Deploy to Cloudflare (Pages + D1) — step by step

This hosts the **whole site on Cloudflare**: the static pages, the enquiry API, and
the database. Everything below is done in the **Cloudflare dashboard** and on
**GitHub.com** — no command line or Node.js required.

What you get:
- `https://your-project.pages.dev/` — the website
- `https://your-project.pages.dev/admin` — the enquiry inbox
- A D1 database storing every enquiry
- (Optional) email alerts on new enquiries

---

## How the pieces fit

```
GitHub repo  ──push──►  Cloudflare Pages
                          ├─ index.html, admin.html, assets/   (static site)
                          ├─ functions/api/...                  (the backend, runs on demand)
                          └─ D1 database  (binding name: DB)     (stores enquiries)
```

The folder `functions/` is special: Cloudflare automatically turns each file into an
API route. You don't run a server — Cloudflare runs the code when a request comes in.

---

## Step 1 — Put the code on GitHub

I've already created a local git repo and committed everything. You just need to push
it to GitHub.

1. Go to <https://github.com/new> and create a repository, e.g. `mohan-brothers`
   (Private is fine). **Do not** add a README/.gitignore — the repo already has files.
2. GitHub shows a "push an existing repository" box. Copy the two lines and run them
   in Terminal from this folder. They look like:

   ```bash
   cd /Users/JB/Desktop/factory
   git remote add origin https://github.com/<your-username>/mohan-brothers.git
   git branch -M main
   git push -u origin main
   ```

   (If asked to log in, use your GitHub username + a Personal Access Token as the
   password: <https://github.com/settings/tokens> → "Generate new token (classic)" →
   tick `repo`.)

After this, your code is on GitHub. ✅

---

## Step 2 — Create the Cloudflare Pages project

1. Sign in / sign up at <https://dash.cloudflare.com> (free).
2. Left sidebar → **Workers & Pages** → **Create** → **Pages** tab →
   **Connect to Git**.
3. Authorise GitHub, pick the `mohan-brothers` repo, click **Begin setup**.
4. Build settings — **leave them empty** (this is a plain static site):
   - Framework preset: **None**
   - Build command: *(blank)*
   - Build output directory: `/`
5. Click **Save and Deploy**. Wait ~1 minute. You'll get a `*.pages.dev` URL.

The site loads now, but the form/admin won't work yet — they need the database and
password from the next steps.

---

## Step 3 — Create the D1 database

1. Sidebar → **Storage & Databases** → **D1 SQL Database** → **Create**.
2. Name it `mohan-brothers` → **Create**.
3. Open the database → **Console** tab.
4. Open the `schema.sql` file from this project, copy its contents, paste into the
   console, and **Run**. This creates the `enquiries` table.

---

## Step 4 — Connect the database + password to the site

1. Go back to **Workers & Pages** → your `mohan-brothers` **Pages** project →
   **Settings**.
2. Find **Bindings** (or **Functions → D1 database bindings**) → **Add binding**:
   - Variable name: `DB`   ← must be exactly `DB`
   - D1 database: select `mohan-brothers`
   - Save.
3. Find **Variables and Secrets** (Environment variables) → **Add**:
   - Name: `ADMIN_TOKEN`   Value: your admin password (e.g. `MBROS@1937`) →
     set it as a **Secret** if offered. Save.
4. **Redeploy** so the new bindings take effect:
   **Deployments** tab → latest deployment → **⋯** → **Retry deployment**
   (or just push any change to GitHub).

Done — the form now saves to D1, and `/admin` works with your password. ✅

---

## Step 5 — Test it

1. Visit `https://your-project.pages.dev/api/health` → should show
   `{"ok":true,"db":true,...}`. If `db` is `false`, the `DB` binding isn't set
   (redo Step 4.2 and redeploy).
2. Open the site, submit a test enquiry via **Request a Quote**.
3. Open `https://your-project.pages.dev/admin`, sign in with your `ADMIN_TOKEN`,
   and you should see the enquiry.

---

## Step 6 (optional) — Email alerts on new enquiries

Cloudflare can't send email via SMTP, so we use **Resend** (free tier) over HTTP.

1. Sign up at <https://resend.com>, create an **API key**.
2. To send to any address (like `mbros1937@rediffmail.com`) you must **verify a
   sending domain** in Resend (add the DNS records it gives you — easy if your domain
   is on Cloudflare). For a quick test, Resend lets you send to *your own* account
   email using `onboarding@resend.dev`.
3. In your Pages project → **Settings → Variables and Secrets**, add:
   - `RESEND_API_KEY` = your Resend key (Secret)
   - `MAIL_FROM` = e.g. `Mohan Brothers <enquiries@yourdomain.com>` (a verified address)
   - `NOTIFY_TO` = `mbros1937@rediffmail.com` (already the default if omitted)
4. Redeploy. New enquiries now email you, with **Reply-To** set to the customer.

If you skip this, everything still works — you just read enquiries in `/admin`.

---

## Step 7 (optional) — Use your own domain

Pages project → **Custom domains** → **Set up a domain** → enter e.g.
`mohanbrothers.com`. If the domain is already on Cloudflare, it's automatic; otherwise
follow the DNS steps shown.

---

## ⚠️ Don't forget the photos/videos

The site references 16 media files in `assets/img/` and `assets/video/` that aren't in
the project yet. Until you add them and push to GitHub, those images/videos will be
blank on the live site too. Put the real files in `assets/img/` and `assets/video/`
with the exact names listed, commit, and push — Cloudflare redeploys automatically.

---

## Updating the site later

Any change → commit → `git push` → Cloudflare rebuilds and redeploys in ~1 minute.
No manual steps.

## Note about the Python backend

`server.py` and `BACKEND.md` stay in the repo for **local testing** only
(`python3 server.py` on your computer). Cloudflare ignores them and uses the
`functions/` code instead. Both backends share identical behaviour and the same
`admin.html` / form, so what you test locally matches what runs in production.
