/* Worker routing check. Run: node workers-site/test_worker.mjs
 *
 * Proves the stable link survives a restart: /s/<id> redirects to whatever
 * URL /publish last stored, the publish route rejects a bad token and a
 * non-tunnel URL, and a missing live URL serves the offline page.
 */
import worker from "./src/worker.js";

function memKV() {
  const m = new Map();
  return {
    async get(k) { return m.has(k) ? m.get(k) : null; },
    async put(k, v) { m.set(k, v); },
    async delete(k) { m.delete(k); },
    _map: m,
  };
}

function assets() {
  return {
    async fetch(req) {
      const p = new URL(req.url).pathname;
      if (p === "/" || p === "/index.html") {
        return new Response("<html>landing</html>", { headers: { "Content-Type": "text/html" } });
      }
      return new Response("nope", { status: 404 });
    },
  };
}

const env = { TUNNEL_KV: memKV(), PUBLISH_TOKEN: "s3cret", ASSETS: assets() };
const base = "https://stable.example";
const call = (path, init) => worker.fetch(new Request(base + path, init), env);
const post = (body, token) =>
  call("/publish", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: "Bearer " + token },
    body: JSON.stringify(body),
  });

const TUNNEL = "https://random-words.trycloudflare.com";

// Offline before anything is published.
let r = await call("/s/abc");
assert(r.status === 200, "offline page before publish");
assert((await r.text()).includes("sender is offline"), "offline text");

// Auth and URL validation.
assert((await post({ url: TUNNEL }, "wrong")).status === 403, "bad token rejected");
assert((await post({ url: "https://evil.example.com" }, "s3cret")).status === 400, "non-tunnel url rejected");
assert((await post({ url: "http://x.trycloudflare.com" }, "s3cret")).status === 400, "http rejected");

// Publish, then the stable link redirects to the live tunnel with the path intact.
r = await post({ url: TUNNEL }, "s3cret");
assert(r.status === 200, "publish ok");
assert((await r.json()).url === TUNNEL, "publish echoes the url");

r = await call("/s/abc123");
assert(r.status === 302, "share link redirects, got " + r.status);
assert(r.headers.get("Location") === TUNNEL + "/s/abc123", "redirect target " + r.headers.get("Location"));

r = await call("/live");
assert((await r.json()).url === TUNNEL, "live reports the url");

// A second publish replaces the first, which is what a tunnel restart does.
const TUNNEL2 = "https://other-name.trycloudflare.com";
await post({ url: TUNNEL2 }, "s3cret");
r = await call("/s/abc123");
assert(r.headers.get("Location") === TUNNEL2 + "/s/abc123", "restart keeps the same link working");

// Clearing falls back to the offline page.
await post({ url: null }, "s3cret");
r = await call("/s/abc123");
assert((await r.text()).includes("sender is offline"), "offline after clear");

// Static routes still work, and POST is refused.
r = await call("/");
assert(r.status === 200 && (await r.text()).includes("landing"), "landing still served");
assert((await call("/", { method: "POST" })).status === 405, "POST to a static path refused");

function assert(ok, label) {
  if (!ok) throw new Error("FAIL: " + label);
  console.log("ok: " + label);
}

console.log("\nall worker routing checks passed");
