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

Then verify with curl. The upload is chunked, so the snippet does the four
calls the browser does: init, one PUT per chunk, complete, download.

```bash
SIZE=$(wc -c < README.md)
INIT=$(curl -s -X POST -H 'Content-Type: application/json' \
  -d "{\"filename\":\"README.md\",\"size\":$SIZE}" \
  http://127.0.0.1:8080/api/upload/init)
ID=$(printf '%s' "$INIT" | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
CS=$(printf '%s' "$INIT" | python3 -c 'import sys,json;print(json.load(sys.stdin)["chunk_size"])')
split -b "$CS" README.md /tmp/ts-part-
i=0; for p in /tmp/ts-part-*; do
  curl -s -X PUT -H "X-Upload-Id: $ID" -H "X-Chunk-Index: $i" \
    --data-binary "@$p" http://127.0.0.1:8080/api/upload/chunk >/dev/null
  i=$((i+1))
done
CODE=$(curl -s -X POST -H 'Content-Type: application/json' \
  -d "{\"id\":\"$ID\",\"expiry_hours\":24,\"max_downloads\":5}" \
  http://127.0.0.1:8080/api/upload/complete \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["code"])')
curl -s "http://127.0.0.1:8080/api/download?id=$ID&code=$CODE" -o /tmp/ts-verify
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
# Upload, chunked. Step 1 opens the upload and preallocates the file.
SIZE=$(wc -c < README.md)
INIT=$(curl -s -X POST -H 'Content-Type: application/json' \
  -d "{\"filename\":\"README.md\",\"size\":$SIZE}" \
  http://127.0.0.1:8080/api/upload/init)
ID=$(printf '%s' "$INIT" | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
CS=$(printf '%s' "$INIT" | python3 -c 'import sys,json;print(json.load(sys.stdin)["chunk_size"])')

# Step 2: one PUT per chunk. A retry re-sends at most one chunk.
split -b "$CS" README.md /tmp/ts-part-
i=0; for p in /tmp/ts-part-*; do
  curl -s -X PUT -H "X-Upload-Id: $ID" -H "X-Chunk-Index: $i" \
    --data-binary "@$p" http://127.0.0.1:8080/api/upload/chunk >/dev/null
  i=$((i+1))
done

# Step 3: seal it. Expect {"id": "...", "code": "...", "url": "/s/..."}
curl -s -X POST -H 'Content-Type: application/json' \
  -d "{\"id\":\"$ID\",\"expiry_hours\":24,\"max_downloads\":5}" \
  http://127.0.0.1:8080/api/upload/complete

# Download, then confirm the bytes match
curl -s "http://127.0.0.1:8080/api/download?id=$ID&code=$CODE" -o /tmp/ts-verify
diff README.md /tmp/ts-verify && echo UPLOAD_DOWNLOAD_VERIFIED
```

Optional: open `http://127.0.0.1:8080/s/<id>` in a browser to see the
verify/download page for the share.

Owner console needs the token link or a localhost visit. Token rotates every restart.
Share links need no token.

## Tunnel step

```bash
TUNNEL_TRANSPORT_PROTOCOL=http2 cloudflared tunnel --url http://127.0.0.1:8080
```

HTTP/2 transport is required, not optional. QUIC stalls through VPNs
(measured 90s stalls on this path). `./setup.sh --run` sets it for you.

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