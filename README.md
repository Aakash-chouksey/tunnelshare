# TunnelShare MVP

Live landing page: https://tunnelshare.billuu-probe.workers.dev

Ephemeral, code-gated file sharing designed to sit behind a Cloudflare quick tunnel.
Upload a file &rarr; get a link (`/s/<id>`) plus a 6-digit code shown once &rarr;
the recipient enters the code to download. Shares self-destruct on expiry or
download budget.

## Quickstart

```bash
pip install -r requirements.txt
python app.py
# in another terminal:
cloudflared tunnel --url http://localhost:8080
```

Or one-shot:

```bash
./setup.sh
./share.sh
```

Hand your coding agent `AGENT_SETUP.md`. It holds a copy-paste prompt
that installs, runs, verifies, and tunnels the project with no guidance.

Open the printed `https://*.trycloudflare.com` URL on your phone / send to a friend.

## Resumable downloads

`GET /api/download?id=<id>&code=<code>` serves `206 Partial Content`
for `Range` headers, so a dropped connection resumes from the exact
byte it stopped at with up to 5 backoff retries plus Pause and Resume.
Whole-file fetches burn one download from the budget. Resume chunks
do not.

## API

| Method | Route | Notes |
|---|---|---|
| `GET /` | upload UI | |
| `POST /api/upload` | multipart `file` + `expiry_hours` (1/24/72, default 24) + `max_downloads` (default 5) | returns `{id, code, url}` — code shown once |
| `GET /s/<id>` | verify/download page | |
| `POST /api/download` | JSON `{id, code}` | file bytes or `403` JSON, kept for compat |
| `GET /api/download?id=<id>&code=<code>` | resumable bytes, honors `Range`, or `403` JSON |
| `GET /api/info/<id>` | `{filename, size, expires_at, downloads_left, status}` — never leaks the code | |
| `POST /api/delete` | JSON `{id, code}` | owner deletion |

## Security model

- Code is a `secrets`-generated 6-digit number (100000–999999), never stored.
  Stored as `sha256(salt + code)` with a per-share `hex16` salt, compared with
  `hmac.compare_digest`.
- 5 wrong codes &rarr; share is `LOCKED` (via `resolve_status()`).
- Filenames sanitized with `werkzeug.secure_filename`; stored as `<id><ext>`
  in `uploads/` (outside any static route). No directory listing, no
  unauthenticated file access — bytes only leave via `/api/download` after a
  correct code.
- 50 MB cap (`MAX_CONTENT_LENGTH` + streamed size check).
- Expired shares are purged on every request (file bytes deleted, row kept as
  `EXPIRED` so clients get a clear status instead of a bare 404).
- Binds `127.0.0.1` only — the only ingress is the Cloudflare tunnel.

## Limits (MVP)

- SQLite + local disk; single process, no rate limiting yet.
- No TLS locally (tunnel provides HTTPS).
- No owner-auth beyond the code; anyone with link + code can download/delete.

## Roadmap

- Per-IP rate limiting + CAPTCHA on verify page.
- Larger files via chunked upload; virus scanning hook.
- Postgres option, multi-worker safe locking.
-Burn-after-reading mode (max_downloads=1 default option in UI).
