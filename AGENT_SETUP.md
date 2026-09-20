# AGENT_SETUP.md — set up TunnelShare from scratch

Copy-paste the prompt below into any coding agent. It clones, installs,
runs, verifies (upload + download), and opens a tunnel.

## Copy-paste prompt (one step, any OS)

```text
Set up TunnelShare from scratch and prove it works. Clone
https://github.com/Aakash-chouksey/tunnelshare (or use the directory
I give you), run ./setup.sh --run, and report back the local URL,
the public tunnel URL it prints, and the upload-plus-download
verification below. Install cloudflared yourself if the script asks:
macOS `brew install cloudflared`, Debian/Ubuntu `sudo apt-get
install -y cloudflared`, Windows via winget or the Cloudflare
downloads page. Only ask me if a step needs a password or a manual
download.
```

Then verify with curl (substitute id and code from the upload response):

```bash
curl -s -F "file=@README.md" -F "expiry_hours=24" -F "max_downloads=5" \
  http://127.0.0.1:8080/api/upload
curl -s "http://127.0.0.1:8080/api/download?id=<id>&code=<code>" \
  -o /tmp/ts-verify
diff README.md /tmp/ts-verify && echo UPLOAD_DOWNLOAD_VERIFIED
```

## Legacy multi-step prompt

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
./setup.sh --run

# Manual equivalent (legacy, multi-step):
# python3 -m venv .venv
# .venv/bin/pip install -r requirements.txt

# 3. Run (serves http://127.0.0.1:8080)
.venv/bin/python app.py
```

> **Note**: The manual three-step sequence above is **legacy**. For a single-command experience, run `./setup.sh --run` instead.

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

> If the URL does not resolve on your own machine right away, wait a
> minute and retry. A too-early first lookup can stick as a cached
> miss in local DNS while the name already works everywhere else.

## One-step setup (new)

Run the entire flow in a single command:

```bash
./setup.sh --run
```

This detects your OS, installs Python/venv dependencies, installs cloudflared if needed,
starts the Flask app, and opens a public tunnel URL. See `setup.sh` for details.