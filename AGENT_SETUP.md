# AGENT_SETUP.md — set up TunnelShare from scratch

Copy-paste the prompt below into any coding agent. It clones, installs,
runs, verifies (upload + download), and opens a tunnel.

## Copy-paste prompt

```text
Set up TunnelShare from scratch and prove it works.

1. Clone the repo and enter it:
   git clone https://github.com/Aakash-chouksey/tunnelshare
   cd tunnelshare
2. Install and run (Python 3.10+ required, cloudflared required for the tunnel step):
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   .venv/bin/python app.py  (serves http://127.0.0.1:8080 — keep it running)
3. Verify with curl:
   - Upload: curl -s -F "file=@README.md" http://127.0.0.1:8080/api/upload
     (expect JSON with id, code, url)
   - Download: curl -s -X POST -H 'Content-Type: application/json'
     -d '{"id":"<id>","code":"<code>"}' http://127.0.0.1:8080/api/download -o /tmp/ts-verify
     and confirm the bytes match the uploaded file.
4. Open a public URL:
   cloudflared tunnel --url http://127.0.0.1:8080
5. Report back: local URL, public tunnel URL, and the upload-plus-download verification result.
```

## What the agent needs (prereqs)

- Python 3.10+, `pip`, `venv`
- `git`, `curl`
- `cloudflared` (only for the tunnel step — everything else works without it)

## Commands (same as the prompt, expanded)

```bash
# 1. Clone
git clone https://github.com/Aakash-chouksey/tunnelshare
cd tunnelshare

# 2. One-shot setup (venv + deps) — or do it manually, see below
./setup.sh

# Manual equivalent:
# python3 -m venv .venv
# .venv/bin/pip install -r requirements.txt

# 3. Run (serves http://127.0.0.1:8080)
.venv/bin/python app.py
```

## Verification (curl upload + download)

In another terminal while the app is running:

```bash
# Upload — expect JSON: {"id": "...", "code": "...", "url": "/s/..."}
curl -s -F "file=@README.md" -F "expiry_hours=24" -F "max_downloads=5" \
  http://127.0.0.1:8080/api/upload

# Download — substitute the id and code from the upload response
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"id":"<id>","code":"<code>"}' \
  http://127.0.0.1:8080/api/download -o /tmp/ts-verify

# Confirm the bytes match
diff README.md /tmp/ts-verify && echo UPLOAD_DOWNLOAD_VERIFIED
```

Optional: open `http://127.0.0.1:8080/s/<id>` in a browser to see the
verify/download page for the share.

## Tunnel step

```bash
cloudflared tunnel --url http://127.0.0.1:8080
```

Open the printed `https://*.trycloudflare.com` URL on your phone or send
it to a friend. The app binds `127.0.0.1` only — the tunnel is the only
ingress, and it provides HTTPS.
