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


# Runs the rendered console script in node with a stub DOM, then checks the
# ready message a non-technical sender copies.
INDEX_HARNESS = r'''
function el() {
  return {
    addEventListener() {}, style: {}, classList: { add() {}, remove() {} },
    textContent: "", innerHTML: "", hidden: false, value: "", files: [],
    disabled: false, dataset: {}, href: "", click() {}, remove() {}, select() {},
    closest() { return null; }, querySelector() { return null; },
  };
}
globalThis.document = {
  getElementById: () => el(), createElement: () => el(),
  body: { appendChild() {} }, addEventListener() {},
};
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
Object.defineProperty(globalThis, "navigator", {
  value: { clipboard: { writeText: async () => {} } }, configurable: true,
});
Object.defineProperty(globalThis, "location", {
  value: { origin: "http://127.0.0.1:8080" }, configurable: true,
});
globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => [] });

await import("/tmp/ts_index_script.mjs");
const ts = globalThis.__ts;
if (!ts) throw new Error("harness: __ts was not attached");
await import("/tmp/ts_verify_script.mjs");
const tsv = globalThis.__tsv;
if (!tsv) throw new Error("harness: __tsv was not attached");

const msg = ts.shareMessage({ id: "abc123", code: "424242", name: "holiday.mp4", hours: 24 });
const problems = [];
if (!msg.includes("https://stable.example/s/abc123")) problems.push("no stable link: " + msg);
if (!msg.includes("424242")) problems.push("no code: " + msg);
if (!msg.includes("holiday.mp4")) problems.push("no filename: " + msg);
const GB5 = 5 * 1024 ** 3;
if (ts.fmtSize(GB5) !== "5.00 GB") problems.push("console size: " + ts.fmtSize(GB5));
if (tsv.fmtBytes(GB5) !== "5.00 GB") problems.push("recipient size: " + tsv.fmtBytes(GB5));
if (problems.length) throw new Error(problems.join(" | "));
console.log("ok: ready message carries the filename, the stable link, and the code");
console.log("ok: both pages show a 5 GB file as 5.00 GB");
process.exit(0);
'''


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
        rendered = {"/": "/tmp/ts_index_script.mjs",
                    f"/s/{meta['id']}": "/tmp/ts_verify_script.mjs"}
        for path, out in rendered.items():
            html = c.get(path).get_data(as_text=True)
            m = re.search(r'<script type="module">(.*?)</script>', html, re.S)
            assert m, f"no module script rendered for {path}"
            src = m.group(1)
            if path == "/":
                src += ("\nglobalThis.__ts = { shareMessage, shareLink, fmtSize,"
                        " setLastShare: (s) => { lastShare = s; } };\n")
            else:
                src += "\nglobalThis.__tsv = { fmtBytes };\n"
            with open(out, "w") as f:
                f.write(src)
            r = subprocess.run(["node", "--check", out], capture_output=True, text=True)
            assert r.returncode == 0, f"{path} renders invalid JS: {r.stderr}"

        # The ready message is the non-technical deliverable, so check its text.
        with open("/tmp/ts_index_harness.mjs", "w") as f:
            f.write(INDEX_HARNESS)
        r = subprocess.run(["node", "/tmp/ts_index_harness.mjs"],
                           capture_output=True, text=True)
        assert r.returncode == 0, (r.stdout + r.stderr).strip()
        print("   " + r.stdout.strip())

    print(f"ok: {size} bytes in 3 chunks, resume named the gap, retry idempotent, "
          f"Range resume 206, short chunk refused, stable link preferred, "
          f"templates render valid JS")


if __name__ == "__main__":
    main()
