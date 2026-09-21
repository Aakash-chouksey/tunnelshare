# TunnelShare landing (workers-site)

Live: https://tunnelshare.billuu-probe.workers.dev

The worker is named `tunnelshare`, so the URL is
`tunnelshare.billuu-probe.workers.dev`. Cloudflare fixes the
`<worker>.<account>.workers.dev` shape, so a bare
`tunnelshare.workers.dev` is not possible on workers.dev. A custom
domain is the follow-up if you want shorter.

The worker serves the landing page from `./public` and routes the stable
share link. It holds the live quick-tunnel URL in KV under `live_url`.

| Route | Behaviour |
|---|---|
| `POST /publish` | `Authorization: Bearer <PUBLISH_TOKEN>` and `{"url": "https://x.trycloudflare.com"}` writes the live URL. `{"url": null}` clears it. |
| `GET /live` | `{url, updated_at}`, or `null` when nothing is live. |
| `GET /s/<id>` | 302 to `<live url>/s/<id>`, or an offline page when nothing is live. |
| `*` | static assets from `./public` with SPA fallback. |

The live URL goes stale after 24 hours, which keeps a dead tunnel from
serving redirects forever.

## Deploy (lead only, after review)

```bash
cd workers-site
wrangler kv namespace create TUNNEL_KV   # paste the printed id into wrangler.toml
wrangler secret put PUBLISH_TOKEN < ../.publish-token
wrangler deploy
```

`setup.sh` creates `../.publish-token` on first run. Set the same value as the
worker secret. Without the KV binding the worker returns 503 on `/publish` and
the app falls back to the tunnel URL.

Check the routing without deploying:

```bash
node test_worker.mjs
```

OAuth login is already done — no API token needed. Do **not** deploy
without lead approval.

Dry-run check (safe, no deploy):

```bash
cd workers-site
wrangler deploy --dry-run
```

## Files

- `wrangler.toml` — `tunnelshare`, `compatibility_date 2026-01-01`,
  `workers_dev true`, the `TUNNEL_KV` binding, assets `./public` (SPA fallback).
- `src/worker.js` — the stable-link router plus static assets. Sets
  `Cache-Control` (immutable for css/js/fonts/images, revalidate for html).
- `test_worker.mjs` — routing check with a stub KV and stub assets.
- `public/index.html` — hero, live demo mock, how-it-works, features,
  self-host, FAQ, footer. GitHub: https://github.com/Aakash-chouksey/tunnelshare
- `public/styles.css` — Cloudflare-like theme, orange `#F6821F`, navy `#0B0F1A`.
- `public/demo.js` — demo interactivity only, zero backend calls.

## Custom domain (later)

1. `wrangler deploy` first so the worker exists.
2. Cloudflare dashboard → Workers & Pages → `tunnelshare` →
   Settings → Domains & Routes → Add Custom Domain.
3. Or via CLI: `wrangler domains add <yourdomain.com>` (route/zone
   must already be on Cloudflare).
4. Wait for DNS + cert provisioning, then verify the landing loads
   over HTTPS on the custom domain.

## Notes

- Keep the landing static: content model is
  `{hero, liveDemo, howItWorks[3], features[6], selfHost[3 steps], faq[4], footer}`.
