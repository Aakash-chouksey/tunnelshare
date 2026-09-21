/* TunnelShare landing and stable-link router.
 *
 * The quick tunnel URL changes on every restart, so a shared link dies with
 * it. This worker holds the current tunnel URL in KV under `live_url`. A
 * request to /s/<id> redirects to the live tunnel, so the link a sender hands
 * out survives a restart.
 *
 *   POST /publish  Authorization: Bearer <PUBLISH_TOKEN>  {"url": "https://x.trycloudflare.com"}
 *   GET  /live     -> {url, updated_at}
 *   GET  /s/<id>   -> 302 to <live url>/s/<id>, or an offline page
 *   *              -> static assets from ./public
 */

const LIVE_KEY = "live_url";
const STALE_AFTER_MS = 24 * 60 * 60 * 1000;

const IMMUTABLE_EXT = new Set(["css", "js", "png", "jpg", "jpeg", "webp", "svg", "ico", "woff", "woff2"]);

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

function safeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length === 0 || a.length !== b.length) {
    return false;
  }
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function bearer(request) {
  const m = /^Bearer\s+(.+)$/i.exec(request.headers.get("Authorization") || "");
  return m ? m[1].trim() : "";
}

function isTunnelUrl(value) {
  try {
    const u = new URL(value);
    return u.protocol === "https:" && /\.trycloudflare\.com$/.test(u.hostname);
  } catch (e) {
    return false;
  }
}

async function readLive(env) {
  if (!env.TUNNEL_KV) return null;
  const raw = await env.TUNNEL_KV.get(LIVE_KEY);
  if (!raw) return null;
  let rec;
  try { rec = JSON.parse(raw); } catch (e) { return null; }
  if (!rec || !isTunnelUrl(rec.url)) return null;
  if (Date.now() - Number(rec.updated_at || 0) > STALE_AFTER_MS) return null;
  return rec;
}

async function publish(request, env) {
  if (!env.PUBLISH_TOKEN || !safeEqual(bearer(request), env.PUBLISH_TOKEN)) {
    return json({ error: "forbidden" }, 403);
  }
  if (!env.TUNNEL_KV) return json({ error: "TUNNEL_KV binding is not configured" }, 503);

  let body;
  try { body = await request.json(); } catch (e) { return json({ error: "bad json" }, 400); }
  if (body && body.url === null) {
    await env.TUNNEL_KV.delete(LIVE_KEY);
    return json({ cleared: true });
  }
  if (!body || !isTunnelUrl(body.url)) {
    return json({ error: "url must be an https trycloudflare.com URL" }, 400);
  }
  const rec = { url: String(body.url).replace(/\/+$/, ""), updated_at: Date.now() };
  await env.TUNNEL_KV.put(LIVE_KEY, JSON.stringify(rec));
  return json({ ok: true, url: rec.url, updated_at: rec.updated_at });
}

async function live(env) {
  const rec = await readLive(env);
  return json({ url: rec ? rec.url : null, updated_at: rec ? rec.updated_at : null });
}

function offlinePage() {
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TunnelShare - sender offline</title>
<style>body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;margin:0;padding:48px 20px;background:#0B0D12;color:#f3f4f6;line-height:1.5}
.card{max-width:520px;margin-inline:auto;background:#151922;border:1px solid #262c38;border-radius:14px;padding:28px 24px}
h1{font-size:1.3rem;margin:0 0 10px}p{color:#9ca3af;margin:0 0 10px}a{color:#F6821F}</style>
</head><body><div class="card"><h1>The sender is offline</h1>
<p>This link is valid, but the machine that holds the file is not sharing right now.</p>
<p>Ask the sender to start TunnelShare again. The same link will work.</p>
<p><a href="/">TunnelShare home</a></p></div></body></html>`;
  return new Response(html, {
    status: 200,
    headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" },
  });
}

async function shareRedirect(url, env) {
  const rec = await readLive(env);
  if (!rec) return offlinePage();
  return Response.redirect(rec.url + url.pathname + url.search, 302);
}

function cacheHeadersFor(pathname) {
  const ext = pathname.split(".").pop().toLowerCase();
  if (IMMUTABLE_EXT.has(ext)) {
    return { "Cache-Control": "public, max-age=31536000, immutable" };
  }
  if (pathname === "/" || pathname.endsWith(".html")) {
    return { "Cache-Control": "public, max-age=0, must-revalidate" };
  }
  return { "Cache-Control": "public, max-age=3600" };
}

function withCacheHeaders(res, pathname) {
  const headers = new Headers(res.headers);
  const extra = cacheHeadersFor(pathname);
  for (const [k, v] of Object.entries(extra)) {
    if (!headers.has(k)) headers.set(k, v);
  }
  return new Response(res.body, {
    status: res.status,
    statusText: res.statusText,
    headers,
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    // Dynamic routes run before the asset fetch, so /s/<id> is never swallowed
    // by the SPA fallback.
    if (path === "/publish") {
      if (request.method !== "POST") return json({ error: "method not allowed" }, 405);
      return publish(request, env);
    }
    if (path === "/live") {
      if (request.method !== "GET" && request.method !== "HEAD") {
        return json({ error: "method not allowed" }, 405);
      }
      return live(env);
    }
    if (path.startsWith("/s/")) {
      if (request.method !== "GET" && request.method !== "HEAD") {
        return new Response("Method Not Allowed", { status: 405 });
      }
      return shareRedirect(url, env);
    }

    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method Not Allowed", { status: 405 });
    }

    let res = await env.ASSETS.fetch(request);
    if (res.status !== 404) {
      return withCacheHeaders(res, path);
    }

    const fallback = new Request(new URL("/index.html", url), request);
    res = await env.ASSETS.fetch(fallback);
    return withCacheHeaders(res, "/index.html");
  },
};
