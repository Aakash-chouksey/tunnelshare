/* TunnelShare landing — static asset worker.
 * Serves ./public via the Workers Assets binding with SPA fallback
 * to index.html and sensible cache-control headers.
 */

const IMMUTABLE_EXT = new Set(["css", "js", "png", "jpg", "jpeg", "webp", "svg", "ico", "woff", "woff2"]);

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

    // Only GET/HEAD are served from static assets.
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method Not Allowed", { status: 405 });
    }

    // 1. Try the exact asset path first.
    let res = await env.ASSETS.fetch(request);
    if (res.status !== 404) {
      return withCacheHeaders(res, url.pathname);
    }

    // 2. SPA fallback: any unknown route serves index.html (200).
    const fallback = new Request(new URL("/index.html", url), request);
    res = await env.ASSETS.fetch(fallback);
    return withCacheHeaders(res, "/index.html");
  },
};
