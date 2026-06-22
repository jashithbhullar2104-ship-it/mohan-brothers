// Cloudflare Pages Function — /api/enquiries
//   POST  -> create a new enquiry (public)
//   GET   -> list enquiries for the admin inbox (token required)
//
// Bindings expected on the Pages project:
//   DB              -> D1 database (required)
//   ADMIN_TOKEN     -> admin password for the inbox (required)
//   RESEND_API_KEY  -> optional, enables email notifications via Resend
//   NOTIFY_TO       -> optional, comma-separated recipients (default mbros1937@rediffmail.com)
//   MAIL_FROM       -> optional, verified "From" address for Resend

const KNOWN_SERVICES = new Set([
  "Mechanical Maintenance / Overhauling",
  "Precision Machining (Turning / Boring / Milling)",
  "Gear Cutting / Gear Manufacturing",
  "Gearbox Strip & Rebuild",
  "Heavy Fabrication / Structural Work",
  "Hydraulic Press Work",
  "Reverse Engineering",
  "Multiple Services / General Enquiry",
]);

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}

function clean(v, limit) {
  return (v == null ? "" : String(v)).trim().slice(0, limit);
}

// constant-time string comparison
function safeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

// Admin auth: HTTP Basic with username + password.
//   Username: env.ADMIN_USER (default "MOHAN_ADMIN")
//   Password: env.ADMIN_TOKEN (required)
function authed(request, env) {
  const user = env.ADMIN_USER || "MOHAN_ADMIN";
  const pass = env.ADMIN_TOKEN || "";
  if (!pass) return false;
  const h = request.headers.get("Authorization") || "";
  if (!h.startsWith("Basic ")) return false;
  let decoded = "";
  try { decoded = atob(h.slice(6).trim()); } catch (e) { return false; }
  const i = decoded.indexOf(":");
  if (i < 0) return false;
  return safeEqual(decoded.slice(0, i), user) && safeEqual(decoded.slice(i + 1), pass);
}

// ── POST /api/enquiries : create ─────────────────────────────
export async function onRequestPost({ request, env }) {
  if (!env.DB) return json({ error: "Server not configured (no database)." }, 500);

  let body = {};
  const ctype = request.headers.get("Content-Type") || "";
  try {
    if (ctype.includes("application/json")) {
      body = await request.json();
    } else {
      const fd = await request.formData();
      for (const [k, v] of fd) body[k] = v;
    }
  } catch (e) {
    return json({ error: "Invalid request body" }, 400);
  }

  // Honeypot: bots fill hidden fields. Pretend success, store nothing.
  if (clean(body.website || body.hp, 200)) return json({ ok: true });

  const data = {
    name: clean(body.name || body.co_name, 120),
    company: clean(body.company || body.co, 160),
    phone: clean(body.phone || body.ph, 40),
    email: clean(body.email || body.em, 160),
    service: clean(body.service || body.svc, 120),
    message: clean(body.message || body.msg, 4000),
  };

  const errors = {};
  if (data.name.length < 2) errors.name = "Please enter your name.";
  if (data.company.length < 2) errors.company = "Please enter your company / organisation.";
  if ((data.phone.match(/\d/g) || []).length < 7) errors.phone = "Please enter a valid phone number.";
  if (data.email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(data.email)) errors.email = "Please enter a valid email address.";
  if (data.service.length < 2) errors.service = "Please select a service.";
  if (data.message.length < 5) errors.message = "Please describe the job.";
  if (Object.keys(errors).length) return json({ error: "Please check the form.", fields: errors }, 422);

  const created_at = new Date().toISOString();
  const ip = request.headers.get("CF-Connecting-IP") || "";
  const ua = clean(request.headers.get("User-Agent"), 400);

  let id;
  try {
    const res = await env.DB.prepare(
      `INSERT INTO enquiries
        (created_at, name, company, phone, email, service, message, status, ip, user_agent)
       VALUES (?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)`
    )
      .bind(created_at, data.name, data.company, data.phone, data.email, data.service, data.message, ip, ua)
      .run();
    id = res.meta.last_row_id;
  } catch (e) {
    return json({ error: "Could not save enquiry. Please try WhatsApp or call us." }, 500);
  }

  // Fire-and-forget email notification (never blocks or fails the response).
  await maybeNotify(env, { ...data, created_at }, id).catch(() => {});

  return json({ ok: true, id, message: "Thank you — we have received your enquiry." }, 201);
}

// ── GET /api/enquiries : admin list ──────────────────────────
export async function onRequestGet({ request, env }) {
  if (!authed(request, env)) return json({ error: "Unauthorized" }, 401);
  if (!env.DB) return json({ error: "Server not configured (no database)." }, 500);

  const url = new URL(request.url);
  const status = url.searchParams.get("status") || "";
  let limit = parseInt(url.searchParams.get("limit") || "200", 10);
  if (!Number.isFinite(limit) || limit <= 0) limit = 200;
  limit = Math.min(limit, 1000);

  let rows;
  if (["new", "handled", "archived"].includes(status)) {
    rows = await env.DB.prepare(
      "SELECT * FROM enquiries WHERE status = ? ORDER BY created_at DESC LIMIT ?"
    ).bind(status, limit).all();
  } else {
    rows = await env.DB.prepare(
      "SELECT * FROM enquiries ORDER BY created_at DESC LIMIT ?"
    ).bind(limit).all();
  }

  const countRows = await env.DB.prepare(
    "SELECT status, COUNT(*) AS n FROM enquiries GROUP BY status"
  ).all();
  const counts = {};
  for (const r of countRows.results) counts[r.status] = r.n;
  const total = Object.values(counts).reduce((a, b) => a + b, 0);

  return json({ enquiries: rows.results, counts, total });
}

// ── Optional email via Resend HTTP API ───────────────────────
async function maybeNotify(env, record, id) {
  if (!env.RESEND_API_KEY) return; // email disabled until a key is set
  const to = (env.NOTIFY_TO || "mbros1937@rediffmail.com")
    .split(",").map((s) => s.trim()).filter(Boolean);
  const from = env.MAIL_FROM || "Mohan Brothers <onboarding@resend.dev>";
  const wa = record.phone.replace(/\D/g, "");
  const flagged = KNOWN_SERVICES.has(record.service) ? "" : " (custom service)";

  const text =
`New quote enquiry from the website:

  Name     : ${record.name}
  Company  : ${record.company}
  Phone    : ${record.phone}
  Email    : ${record.email || "—"}
  Service  : ${record.service}${flagged}

  Message:
  ${record.message}

  Received : ${record.created_at}
  Enquiry  : #${id}` + (wa ? `\n\n  WhatsApp : https://wa.me/${wa}` : "");

  const payload = {
    from,
    to,
    subject: `New enquiry #${id}: ${record.service} — ${record.company}`,
    text,
  };
  if (record.email) payload.reply_to = record.email;

  await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.RESEND_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}
