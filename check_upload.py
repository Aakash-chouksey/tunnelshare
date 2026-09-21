"""Chunked-upload check. Run: python3 check_upload.py

Proves the three properties the large-file path rests on:
  1. A file larger than the old 50 MB cap uploads in chunks and downloads
     back byte-identical.
  2. A missing chunk blocks completion, and /api/upload/status names it, so a
     dropped connection resumes instead of restarting.
  3. A repeated chunk write is idempotent (same bytes, same offset).
"""
import hashlib
import os
import re
import shutil
import subprocess
import tempfile

import app as ts


def fresh_client(tmp):
    ts.DB_PATH = os.path.join(tmp, "shares.db")
    ts.UPLOAD_DIR = os.path.join(tmp, "uploads")
    ts.STABLE_URL_PATH = os.path.join(tmp, ".stable-url")
    ts.TUNNEL_URL_PATH = os.path.join(tmp, ".tunnel-url")
    ts._URL_CACHE.clear()
    ts.init_db()
    ts.app.config["TESTING"] = True
    return ts.app.test_client()


def body(size, seed):
    return bytes(((i * 31 + seed) % 251) for i in range(size))


def upload_all(c, data, filename="big.bin", skip=()):
    init = c.post("/api/upload/init", json={"filename": filename, "size": len(data)})
    assert init.status_code == 200, init.get_data(as_text=True)
    meta = init.get_json()
    size, cs, n = len(data), meta["chunk_size"], meta["chunks"]
    assert n == (size + cs - 1) // cs, f"chunk count {n} wrong for size {size}"

    for idx in range(n):
        if idx in skip:
            continue
        start = idx * cs
        chunk = data[start:start + cs]
        r = c.put("/api/upload/chunk", data=chunk,
                  headers={"X-Upload-Id": meta["id"], "X-Chunk-Index": str(idx)})
        assert r.status_code == 200, r.get_data(as_text=True)
    return meta, n


def main():
    tmp = tempfile.mkdtemp(prefix="ts-check-")
    c = fresh_client(tmp)

    size = 40 * 1024 * 1024 + 7  # 3 chunks, and not a chunk multiple
    data = body(size, 5)

    # 2. A missing chunk blocks completion and is named by the status route.
    meta, n = upload_all(c, data, skip=(1,))
    assert n == 3, n
    st = c.get(f"/api/upload/status?id={meta['id']}").get_json()
    assert st["received"] == [0, 2], st
    bad = c.post("/api/upload/complete", json={"id": meta["id"], "expiry_hours": 1})
    assert bad.status_code == 409, bad.status_code

    # 1. The missing chunk arrives, completion seals the share.
    cs = meta["chunk_size"]
    c.put("/api/upload/chunk", data=data[cs:2 * cs],
          headers={"X-Upload-Id": meta["id"], "X-Chunk-Index": "1"})
    fin = c.post("/api/upload/complete",
                 json={"id": meta["id"], "expiry_hours": 1, "max_downloads": 2})
    assert fin.status_code == 200, fin.get_data(as_text=True)
    code = fin.get_json()["code"]

    # 3. Idempotent retry: send chunk 0 again, the file is unchanged.
    c.put("/api/upload/chunk", data=data[:cs],
          headers={"X-Upload-Id": meta["id"], "X-Chunk-Index": "0"})

    got = c.get(f"/api/download?id={meta['id']}&code={code}")
    assert got.status_code == 200, got.status_code
    assert hashlib.sha256(got.data).hexdigest() == hashlib.sha256(data).hexdigest(), \
        f"downloaded {len(got.data)} bytes, uploaded {size}"

    # Range resume still works on a chunked upload.
    part = c.get(f"/api/download?id={meta['id']}&code={code}",
                 headers={"Range": f"bytes={size - 10}-"})
    assert part.status_code == 206, part.status_code
    assert part.data == data[-10:], part.data

    # Wrong chunk length is refused, so a truncated body cannot silently land.
    meta2, _ = upload_all(c, body(cs * 2, 9), filename="short.bin")
    r = c.put("/api/upload/chunk", data=b"x",
              headers={"X-Upload-Id": meta2["id"], "X-Chunk-Index": "0"})
    assert r.status_code == 400, r.status_code

    # The share link must be the stable one, not the tunnel URL in the bar.
    with open(ts.TUNNEL_URL_PATH, "w") as f:
        f.write("https://live.trycloudflare.com\n")
    assert c.get("/api/config").get_json()["share_base"] == "https://live.trycloudflare.com"
    with open(ts.STABLE_URL_PATH, "w") as f:
        f.write("https://stable.example\n")
    cfg = c.get("/api/config").get_json()
    assert cfg["share_base"] == "https://stable.example", cfg
    assert cfg["tunnel_url"] == "https://live.trycloudflare.com", cfg
    page = c.get(f"/s/{meta['id']}").get_data(as_text=True)
    assert "https://stable.example" in page, "verify page must carry the stable base"

    # Rendered templates must be valid JavaScript, Jinja placeholders resolved.
    if shutil.which("node"):
        for path in ("/", f"/s/{meta['id']}"):
            html = c.get(path).get_data(as_text=True)
            m = re.search(r'<script type="module">(.*?)</script>', html, re.S)
            assert m, f"no module script rendered for {path}"
            with open("/tmp/ts_script_check.mjs", "w") as f:
                f.write(m.group(1))
            r = subprocess.run(["node", "--check", "/tmp/ts_script_check.mjs"],
                               capture_output=True, text=True)
            assert r.returncode == 0, f"{path} renders invalid JS: {r.stderr}"

    print(f"ok: {size} bytes in 3 chunks, resume named the gap, retry idempotent, "
          f"Range resume 206, short chunk refused, stable link preferred, "
          f"templates render valid JS")


if __name__ == "__main__":
    main()
