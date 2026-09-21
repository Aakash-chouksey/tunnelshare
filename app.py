"""TunnelShare MVP — ephemeral code-gated file sharing over a Cloudflare tunnel.

Data shape (principle-model-the-domain):
    Share = {id, filename, stored_name, size_bytes, mime, code_hash,
             salt, expires_at, max_downloads, download_count,
             attempt_count, status, created_at}

Status is a state machine resolved ONLY by resolve_status(). No scattered
booleans anywhere in the codebase.
"""
import hashlib
import hmac
import mimetypes
import os
import secrets
import sqlite3
import time

from flask import Flask, g, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "shares.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")

# OwnerGrant: server-secret token string held here; owner_granted() below is
# the ONLY place grant logic lives.
OWNER_TOKEN_PATH = os.path.join(BASE_DIR, ".owner-token")
OWNER_TOKEN = ""

# Runtime URLs written by setup.sh. The tunnel URL changes on every restart,
# so the stable link is the one to hand out; the tunnel URL is the fallback.
TUNNEL_URL_PATH = os.path.join(BASE_DIR, ".tunnel-url")
STABLE_URL_PATH = os.path.join(BASE_DIR, ".stable-url")
_URL_CACHE = {}

# Chunked upload: every request body stays under Cloudflare's 100 MB cap,
# and a dropped connection re-sends at most one chunk. A chunk retry writes
# the same bytes at the same offset, so the operation is idempotent.
CHUNK_SIZE = 16 * 1024 * 1024  # 16 MB
MAX_FILE_BYTES = int(os.environ.get("TS_MAX_BYTES", 10 * 1024**3))  # disk, not the edge, is the real cap
UPLOAD_TTL_SECONDS = 6 * 3600  # abandoned PENDING uploads reclaim their disk here
EXPIRY_OPTIONS = (1, 24, 72)
DEFAULT_EXPIRY_HOURS = 24
DEFAULT_MAX_DOWNLOADS = 5
MAX_ATTEMPTS = 5  # 5 failed codes -> LOCKED

# PENDING is the upload phase of the same Share. One machine, one resolver.
STATUSES = ("PENDING", "ACTIVE", "EXHAUSTED", "EXPIRED", "LOCKED")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = CHUNK_SIZE + 1024 * 1024


# ---------------------------------------------------------------- DB helpers
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    _init_owner_token()
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute(
        """CREATE TABLE IF NOT EXISTS shares (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            mime TEXT NOT NULL,
            code_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            expires_at INTEGER NOT NULL,
            max_downloads INTEGER NOT NULL,
            download_count INTEGER NOT NULL DEFAULT 0,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            created_at INTEGER NOT NULL
        )"""
    )
    # Which chunks of a PENDING upload have arrived. A row is a fact, not a
    # counter, so a retried chunk cannot double-count.
    db.execute(
        """CREATE TABLE IF NOT EXISTS chunks (
            share_id TEXT NOT NULL,
            idx INTEGER NOT NULL,
            PRIMARY KEY (share_id, idx)
        )"""
    )
    db.commit()
    db.close()


def _init_owner_token():
    """Generate server-secret owner token, persist with mode 0o600, print link."""
    global OWNER_TOKEN
    OWNER_TOKEN = secrets.token_hex(16)
    fd = os.open(OWNER_TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(OWNER_TOKEN)
    try:
        os.chmod(OWNER_TOKEN_PATH, 0o600)
    except OSError:
        pass
    print(f"Owner console: http://127.0.0.1:8080/?token={OWNER_TOKEN}", flush=True)


def owner_granted():
    """ONLY place owner grant logic lives."""
    token = OWNER_TOKEN or ""
    if token and hmac.compare_digest(request.cookies.get("ts_owner", "") or "", token):
        g._owner_set_cookie = False
        return True
    q = request.args.get("token", "") or ""
    if q and token and hmac.compare_digest(q, token):
        g._owner_set_cookie = True
        return True
    if request.remote_addr in ("127.0.0.1", "::1") and "CF-Connecting-IP" not in request.headers:
        g._owner_set_cookie = True
        return True
    g._owner_set_cookie = False
    return False


@app.after_request
def _set_owner_cookie(resp):
    try:
        if getattr(g, "_owner_set_cookie", False) and OWNER_TOKEN:
            resp.set_cookie("ts_owner", OWNER_TOKEN, httponly=True, samesite="Lax", path="/")
    except Exception:
        pass
    return resp


def row_to_share(row):
    return dict(row) if row else None


def get_share(db, share_id):
    return row_to_share(db.execute("SELECT * FROM shares WHERE id = ?", (share_id,)).fetchone())


# ------------------------------------------------------- state machine (only place)
def resolve_status(share, now=None):
    """Single resolver for Share.status. All transitions go through here.

    Precedence: LOCKED > EXPIRED > EXHAUSTED > ACTIVE.
    """
    now = int(now if now is not None else time.time())
    if share["attempt_count"] >= MAX_ATTEMPTS:
        return "LOCKED"
    if now >= share["expires_at"]:
        return "EXPIRED"
    if share["status"] == "PENDING":
        return "PENDING"
    if share["download_count"] >= share["max_downloads"]:
        return "EXHAUSTED"
    return "ACTIVE"


def refresh_status(db, share, now=None):
    """Re-resolve status, persist if changed, delete file body on expiry."""
    now = int(now if now is not None else time.time())
    status = resolve_status(share, now)
    if status != share["status"]:
        db.execute("UPDATE shares SET status = ? WHERE id = ?", (status, share["id"]))
        db.commit()
        share["status"] = status
    if status == "EXPIRED":
        # Remove file bytes so expired content is unrecoverable; keep the row
        # so /api/info can still report EXPIRED instead of 404.
        try:
            os.remove(os.path.join(UPLOAD_DIR, share["stored_name"]))
        except OSError:
            pass
    return status


def purge_expired(db):
    """Run on each request: expire anything past its deadline."""
    now = int(time.time())
    rows = db.execute("SELECT * FROM shares WHERE status IN ('ACTIVE', 'PENDING')").fetchall()
    for row in rows:
        share = row_to_share(row)
        if resolve_status(share, now) == "EXPIRED":
            refresh_status(db, share, now)


@app.before_request
def _purge_on_each_request():
    try:
        purge_expired(get_db())
    except Exception:
        pass  # never break a request because of janitor work


# ---------------------------------------------------------------- helpers
def new_share_id(db):
    for _ in range(20):
        sid = secrets.token_urlsafe(6)[:8]
        if not db.execute("SELECT 1 FROM shares WHERE id = ?", (sid,)).fetchone():
            return sid
    raise RuntimeError("could not allocate share id")


def hash_code(code, salt):
    return hashlib.sha256((salt + code).encode()).hexdigest()


def read_url_file(path):
    """Read a one-line runtime URL file, re-reading only when it changes."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return ""
    cached = _URL_CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(path, "r", encoding="utf-8") as f:
            value = f.read().strip().rstrip("/")
    except OSError:
        value = ""
    _URL_CACHE[path] = (mtime, value)
    return value


def public_base():
    """Stable link first, live tunnel second, empty when neither is known."""
    return read_url_file(STABLE_URL_PATH) or read_url_file(TUNNEL_URL_PATH)


def chunk_count(size):
    return max(1, (size + CHUNK_SIZE - 1) // CHUNK_SIZE)


def chunk_length(size, idx):
    return min(CHUNK_SIZE, size - idx * CHUNK_SIZE)


# ---------------------------------------------------------------- routes
@app.get("/")
def index():
    if not owner_granted():
        return ("<h1>Forbidden</h1>"
                "<p>This console is private to the sender. "
                "Open it on the sender machine or with the owner link.</p>", 403)
    return render_template("index.html", share_base=public_base())


@app.get("/api/config")
def api_config():
    """Public URLs the UI needs to build a share link. No secret here."""
    return jsonify({"stable_url": read_url_file(STABLE_URL_PATH),
                    "tunnel_url": read_url_file(TUNNEL_URL_PATH),
                    "share_base": public_base()})


@app.get("/s/<share_id>")
def verify_page(share_id):
    db = get_db()
    share = get_share(db, share_id)
    if share is None:
        return render_template("verify.html", share_id=share_id, found=False,
                               share_base=public_base()), 404
    refresh_status(db, share)
    return render_template("verify.html", share_id=share_id, found=True,
                           share_base=public_base())


@app.post("/api/upload/init")
def api_upload_init():
    """Open a PENDING share and preallocate its file. No bytes move yet."""
    if not owner_granted():
        return jsonify({"error": "forbidden"}), 403
    data = request.get_json(force=True, silent=True) or {}
    try:
        size = int(data.get("size", 0))
    except (TypeError, ValueError):
        size = 0
    if size <= 0:
        return jsonify({"error": "size must be a positive number of bytes"}), 400
    if size > MAX_FILE_BYTES:
        return jsonify({"error": f"file exceeds {MAX_FILE_BYTES // 1024**3} GB limit"}), 413

    clean = secure_filename(str(data.get("filename", ""))) or "file"
    _, ext = os.path.splitext(clean)
    ext = ext[:16]

    db = get_db()
    now = int(time.time())
    share_id = new_share_id(db)
    stored_name = share_id + ext
    dest = os.path.join(UPLOAD_DIR, stored_name)
    # Preallocate: every chunk has a home from the start, so a chunk write is a
    # plain seek-and-write and a retry is byte-identical.
    with open(dest, "wb") as out:
        out.truncate(size)

    mime = mimetypes.guess_type(clean)[0] or "application/octet-stream"
    db.execute(
        """INSERT INTO shares (id, filename, stored_name, size_bytes, mime,
                               code_hash, salt, expires_at, max_downloads,
                               download_count, attempt_count, status, created_at)
           VALUES (?, ?, ?, ?, ?, '', '', ?, ?, 0, 0, 'PENDING', ?)""",
        (share_id, clean, stored_name, size, mime,
         now + UPLOAD_TTL_SECONDS, DEFAULT_MAX_DOWNLOADS, now),
    )
    db.commit()
    return jsonify({"id": share_id, "chunk_size": CHUNK_SIZE,
                    "chunks": chunk_count(size)})


@app.put("/api/upload/chunk")
def api_upload_chunk():
    """Write one chunk at its offset. Safe to repeat."""
    if not owner_granted():
        return jsonify({"error": "forbidden"}), 403
    upload_id = request.headers.get("X-Upload-Id", "")
    try:
        idx = int(request.headers.get("X-Chunk-Index", ""))
    except (TypeError, ValueError):
        return jsonify({"error": "X-Chunk-Index must be an integer"}), 400

    db = get_db()
    share = get_share(db, upload_id)
    if share is None:
        return jsonify({"error": "not found"}), 404
    if refresh_status(db, share) != "PENDING":
        return jsonify({"error": "upload is not open"}), 409
    total = chunk_count(share["size_bytes"])
    if not 0 <= idx < total:
        return jsonify({"error": "chunk index out of range"}), 400

    expected = chunk_length(share["size_bytes"], idx)
    body = request.stream.read(expected + 1)
    if len(body) != expected:
        return jsonify({"error": f"chunk {idx} must be {expected} bytes"}), 400

    path = os.path.join(UPLOAD_DIR, share["stored_name"])
    try:
        with open(path, "r+b") as out:
            out.seek(idx * CHUNK_SIZE)
            out.write(body)
    except OSError:
        return jsonify({"error": "could not write chunk"}), 500

    db.execute("INSERT OR IGNORE INTO chunks (share_id, idx) VALUES (?, ?)",
               (upload_id, idx))
    db.commit()
    received = db.execute("SELECT COUNT(*) FROM chunks WHERE share_id = ?",
                          (upload_id,)).fetchone()[0]
    return jsonify({"received": received, "chunks": total})


@app.get("/api/upload/status")
def api_upload_status():
    """Which chunks the server already holds. The client resumes from here."""
    if not owner_granted():
        return jsonify({"error": "forbidden"}), 403
    db = get_db()
    share = get_share(db, request.args.get("id", ""))
    if share is None:
        return jsonify({"error": "not found"}), 404
    status = refresh_status(db, share)
    rows = db.execute("SELECT idx FROM chunks WHERE share_id = ? ORDER BY idx",
                      (share["id"],)).fetchall()
    return jsonify({"status": status, "received": [r["idx"] for r in rows],
                    "chunks": chunk_count(share["size_bytes"]),
                    "chunk_size": CHUNK_SIZE,
                    "size_bytes": share["size_bytes"]})


@app.post("/api/upload/complete")
def api_upload_complete():
    """Seal a fully received upload. Only here does the code get created."""
    if not owner_granted():
        return jsonify({"error": "forbidden"}), 403
    data = request.get_json(force=True, silent=True) or {}
    upload_id = data.get("id", "")
    db = get_db()
    share = get_share(db, upload_id)
    if share is None:
        return jsonify({"error": "not found"}), 404
    if refresh_status(db, share) != "PENDING":
        return jsonify({"error": "upload is not open"}), 409

    total = chunk_count(share["size_bytes"])
    received = db.execute("SELECT COUNT(*) FROM chunks WHERE share_id = ?",
                          (upload_id,)).fetchone()[0]
    if received != total:
        return jsonify({"error": f"missing chunks: {received} of {total} received"}), 409
    path = os.path.join(UPLOAD_DIR, share["stored_name"])
    if os.path.getsize(path) != share["size_bytes"]:
        return jsonify({"error": "stored size does not match the declared size"}), 500

    try:
        expiry_hours = int(data.get("expiry_hours", DEFAULT_EXPIRY_HOURS))
    except (TypeError, ValueError):
        expiry_hours = DEFAULT_EXPIRY_HOURS
    if expiry_hours not in EXPIRY_OPTIONS:
        expiry_hours = DEFAULT_EXPIRY_HOURS
    try:
        max_downloads = int(data.get("max_downloads", DEFAULT_MAX_DOWNLOADS))
    except (TypeError, ValueError):
        max_downloads = DEFAULT_MAX_DOWNLOADS
    max_downloads = max(1, min(100, max_downloads))

    code = str(secrets.randbelow(900000) + 100000)  # 100000-999999, shown once
    salt = secrets.token_hex(8)
    now = int(time.time())
    db.execute(
        """UPDATE shares SET code_hash = ?, salt = ?, status = 'ACTIVE',
                             expires_at = ?, max_downloads = ? WHERE id = ?""",
        (hash_code(code, salt), salt, now + expiry_hours * 3600, max_downloads, upload_id),
    )
    db.commit()
    return jsonify({"id": upload_id, "code": code, "url": f"/s/{upload_id}"})


@app.get("/api/info/<share_id>")
def api_info(share_id):
    db = get_db()
    share = get_share(db, share_id)
    if share is None:
        return jsonify({"error": "not found"}), 404
    status = refresh_status(db, share)
    return jsonify({
        "filename": share["filename"],
        "size": share["size_bytes"],
        "size_bytes": share["size_bytes"],
        "expires_at": share["expires_at"],
        "downloads_left": max(0, share["max_downloads"] - share["download_count"]),
        "status": status,
    })


@app.get("/api/shares")
def api_shares():
    if not owner_granted():
        return jsonify({"error": "forbidden"}), 403
    db = get_db()
    rows = db.execute(
        "SELECT * FROM shares ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    out = []
    for row in rows:
        share = row_to_share(row)
        out.append({
            "id": share["id"],
            "filename": share["filename"],
            "size_bytes": share["size_bytes"],
            "expires_at": share["expires_at"],
            "downloads_left": max(0, share["max_downloads"] - share["download_count"]),
            "download_count": share["download_count"],
            "status": resolve_status(share),
        })
    return jsonify(out)


@app.post("/api/download")
def api_download():
    data = request.get_json(force=True, silent=True) or {}
    share_id = data.get("id", "")
    code = str(data.get("code", ""))
    db = get_db()
    share = get_share(db, share_id)
    if share is None:
        return jsonify({"error": "not found"}), 404

    status = refresh_status(db, share)
    if status != "ACTIVE":
        return jsonify({"error": f"share is {status.lower()}"}), 403

    if not hmac.compare_digest(hash_code(code, share["salt"]), share["code_hash"]):
        attempt_count = share["attempt_count"] + 1
        db.execute("UPDATE shares SET attempt_count = ? WHERE id = ?", (attempt_count, share_id))
        db.commit()
        share["attempt_count"] = attempt_count
        status = refresh_status(db, share)
        if status == "LOCKED":
            return jsonify({"error": "too many wrong codes, share is locked"}), 403
        left = MAX_ATTEMPTS - attempt_count
        return jsonify({"error": f"wrong code ({left} attempts left)"}), 403

    path = os.path.join(UPLOAD_DIR, share["stored_name"])
    if not os.path.isfile(path):
        return jsonify({"error": "file no longer available"}), 410

    db.execute(
        "UPDATE shares SET download_count = download_count + 1 WHERE id = ?", (share_id,)
    )
    db.commit()
    share["download_count"] += 1
    refresh_status(db, share)
    return send_file(path, as_attachment=True,
                     download_name=share["filename"], mimetype=share["mime"])


@app.get("/api/download")
def api_download_get():
    share_id = request.args.get("id", "")
    code = str(request.args.get("code", ""))
    db = get_db()
    share = get_share(db, share_id)
    if share is None:
        return jsonify({"error": "not found"}), 404

    status = refresh_status(db, share)
    if status != "ACTIVE":
        return jsonify({"error": f"share is {status.lower()}"}), 403

    if not hmac.compare_digest(hash_code(code, share["salt"]), share["code_hash"]):
        attempt_count = share["attempt_count"] + 1
        db.execute("UPDATE shares SET attempt_count = ? WHERE id = ?", (attempt_count, share_id))
        db.commit()
        share["attempt_count"] = attempt_count
        status = refresh_status(db, share)
        if status == "LOCKED":
            return jsonify({"error": "too many wrong codes, share is locked"}), 403
        left = MAX_ATTEMPTS - attempt_count
        return jsonify({"error": f"wrong code ({left} attempts left)"}), 403

    path = os.path.join(UPLOAD_DIR, share["stored_name"])
    if not os.path.isfile(path):
        return jsonify({"error": "file no longer available"}), 410

    # Increment budget on full-body (200) responses and on ranges that
    # start at byte 0 (a whole file can be fetched that way). Resume
    # retries start past byte 0 and stay free.
    first_byte = 0
    range_hdr = request.headers.get("Range") or ""
    if range_hdr:
        try:
            first_byte = int(range_hdr.split("=")[1].split("-")[0] or 0)
        except (IndexError, ValueError):
            first_byte = 0
    if not range_hdr or first_byte == 0:
        db.execute(
            "UPDATE shares SET download_count = download_count + 1 WHERE id = ?", (share_id,)
        )
        db.commit()
        share["download_count"] += 1
        refresh_status(db, share)
    return send_file(path, as_attachment=True,
                     download_name=share["filename"], mimetype=share["mime"],
                     conditional=True)


@app.post("/api/delete")
def api_delete():
    if not owner_granted():
        return jsonify({"error": "forbidden"}), 403
    data = request.get_json(force=True, silent=True) or {}
    share_id = data.get("id", "")
    code = str(data.get("code", ""))
    db = get_db()
    share = get_share(db, share_id)
    if share is None:
        return jsonify({"error": "not found"}), 404
    if not hmac.compare_digest(hash_code(code, share["salt"]), share["code_hash"]):
        return jsonify({"error": "wrong code"}), 403
    try:
        os.remove(os.path.join(UPLOAD_DIR, share["stored_name"]))
    except OSError:
        pass
    db.execute("DELETE FROM shares WHERE id = ?", (share_id,))
    db.commit()
    return jsonify({"deleted": share_id})


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=8080)
