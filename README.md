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
# in another terminal (HTTP/2 transport: QUIC stalls through VPNs):
TUNNEL_TRANSPORT_PROTOCOL=http2 cloudflared tunnel --url http://localhost:8080
```

Or one-shot:

```bash
./setup.sh
./share.sh
```

Hand your coding agent `AGENT_SETUP.md`. It holds a copy-paste prompt
that installs, runs, verifies, and tunnels the project with no guidance.

Open the printed `https://*.trycloudflare.com` URL on your phone / send to a friend.

Owner console needs the token link or a localhost visit. Token rotates every restart.
Share links (`/s/<id>`) need no token.

## Large files and resume

Every request body stays under Cloudflare's 100 MB cap, so uploads are chunked
(16 MB each). A dropped connection re-sends at most one chunk, and the client
asks `/api/upload/status` for the chunks the server already holds, so a reload
resumes instead of restarting. A chunk retry writes the same bytes at the same
offset, so it is idempotent.

Downloads stream and honor `Range`, so a dropped download resumes from the
exact byte it stopped at with up to 5 backoff retries plus Pause and Resume.
Whole-file fetches burn one download from the budget. Resume chunks do not.

`TS_MAX_BYTES` sets the upload cap (default 10 GB). Disk, not the edge, is the
real limit.

Run the upload checks with `python3 check_upload.py`.

## API

| Method | Route | Notes |
|---|---|---|
| `GET /` | upload UI | |
| `POST /api/upload/init` | JSON `{filename, size}` | opens a `PENDING` share, preallocates the file, returns `{id, chunk_size, chunks}` |
| `PUT /api/upload/chunk` | headers `X-Upload-Id`, `X-Chunk-Index`, raw body | writes one chunk at its offset, returns `{received, chunks}` |
| `GET /api/upload/status?id=` | | `{status, received:[idx], chunks, chunk_size, size_bytes}` |
| `POST /api/upload/complete` | JSON `{id, expiry_hours, max_downloads}` | seals the share, returns `{id, code, url}` — code shown once |
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
- 10 GB default upload cap (`TS_MAX_BYTES`); every request body stays under
  Cloudflare's 100 MB limit.
- Expired shares are purged on every request (file bytes deleted, row kept as
  `EXPIRED` so clients get a clear status instead of a bare 404).
- Abandoned uploads purge themselves after 6 hours (`UPLOAD_TTL_SECONDS`).
- Binds `127.0.0.1` only — the only ingress is the Cloudflare tunnel.

## Limits (MVP)

- SQLite + local disk; single process, no rate limiting yet.
- No TLS locally (tunnel provides HTTPS).
- No owner-auth beyond the code; anyone with link + code can download/delete.

## Stable link

A quick tunnel URL changes on every restart, so a link built from it dies.
The stable link points at the Workers host instead. The worker holds the live
tunnel URL in KV, and `/s/<id>` redirects to it. The link a sender hands out
therefore survives a restart.

One-time setup:

```bash
cd workers-site
wrangler kv namespace create TUNNEL_KV     # paste the printed id into wrangler.toml
wrangler secret put PUBLISH_TOKEN < ../.publish-token   # created by setup.sh
wrangler deploy
```

After that, `./setup.sh --run` publishes the live tunnel URL on every start
and prints the stable link. `TS_STABLE_HOST` and `TS_PUBLISH_ENDPOINT`
override the defaults. Without the worker, the app falls back to the tunnel
URL, and everything else still works.

The worker has its own check: `node workers-site/test_worker.mjs`.

## Roadmap

- Per-IP rate limiting + CAPTCHA on verify page.
- Virus scanning hook.
- Postgres option, multi-worker safe locking.
- Burn-after-reading mode (max_downloads=1 default option in UI).
