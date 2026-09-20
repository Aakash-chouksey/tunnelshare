# TunnelShare landing (workers-site)

Live: https://tunnelshare.billuu-probe.workers.dev

The worker is named `tunnelshare`, so the URL is
`tunnelshare.billuu-probe.workers.dev`. Cloudflare fixes the
`<worker>.<account>.workers.dev` shape, so a bare
`tunnelshare.workers.dev` is not possible on workers.dev. A custom
domain is the follow-up if you want shorter.

Static-only landing page for TunnelShare, deployable with the wrangler CLI.
No R2, no D1, no backend — one Worker serves `./public` with SPA fallback.

## Deploy (lead only, after review)

```bash
cd workers-site
wrangler deploy
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
  `workers_dev true`, assets `./public` (SPA fallback).
- `src/worker.js` — serves static assets, falls back to `index.html`,
  sets `Cache-Control` (immutable for css/js/fonts/images, revalidate for html).
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

- Do not touch `../app.py` or `../templates/` — owned by someone else.
- Keep the landing static: content model is
  `{hero, liveDemo, howItWorks[3], features[6], selfHost[3 steps], faq[4], footer}`.
