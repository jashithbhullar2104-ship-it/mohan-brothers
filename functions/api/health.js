// Cloudflare Pages Function — /api/health
export function onRequestGet({ env }) {
  return new Response(
    JSON.stringify({ ok: true, db: Boolean(env.DB), time: new Date().toISOString() }),
    { headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" } }
  );
}
