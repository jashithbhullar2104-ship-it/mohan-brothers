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

function authed(request, env) {
  const auth = request.headers.get("Authorization") || "";
  let token = auth.startsWith("Bearer ") ? auth.slice(7).trim() : "";
  if (!token) token = new URL(request.url).searchParams.get("token") || "";
  return Boolean(env.ADMIN_TOKEN) && safeEqual(token, env.ADMIN_TOKEN);
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
