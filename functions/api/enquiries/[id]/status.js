// Cloudflare Pages Function — /api/enquiries/:id/status
//   POST -> update an enquiry's workflow status (token required)

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}

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

export async function onRequestPost({ request, env, params }) {
  if (!authed(request, env)) return json({ error: "Unauthorized" }, 401);
  if (!env.DB) return json({ error: "Server not configured (no database)." }, 500);

  const id = parseInt(params.id, 10);
  if (!Number.isFinite(id)) return json({ error: "Bad enquiry id" }, 400);

  let body = {};
  try {
    body = await request.json();
  } catch (e) {
    return json({ error: "Invalid request body" }, 400);
  }

  const status = (body.status == null ? "" : String(body.status)).trim();
  if (!["new", "handled", "archived"].includes(status)) {
    return json({ error: "status must be new, handled or archived" }, 422);
  }

  const res = await env.DB.prepare("UPDATE enquiries SET status = ? WHERE id = ?")
    .bind(status, id)
    .run();

  if (!res.meta.changes) return json({ error: "Enquiry not found" }, 404);
  return json({ ok: true, id, status });
}
