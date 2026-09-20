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

MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB
EXPIRY_OPTIONS = (1, 24, 72)
DEFAULT_EXPIRY_HOURS = 24
DEFAULT_MAX_DOWNLOADS = 5
MAX_ATTEMPTS = 5  # 5 failed codes -> LOCKED

STATUSES = ("ACTIVE", "EXHAUSTED", "EXPIRED", "LOCKED")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_BYTES


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
    db.commit()
    db.close()


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
    rows = db.execute("SELECT * FROM shares WHERE status = 'ACTIVE'").fetchall()
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


# ---------------------------------------------------------------- routes
@app.get("/")
def index():
    return render_template("index.html")


@app.get("/s/<share_id>")
def verify_page(share_id):
    db = get_db()
    share = get_share(db, share_id)
    if share is None:
        return render_template("verify.html", share_id=share_id, found=False), 404
    refresh_status(db, share)
    return render_template("verify.html", share_id=share_id, found=True)


@app.post("/api/upload")
def api_upload():
    db = get_db()
    f = request.files.get("file")
    if f is None or not f.filename:
        return jsonify({"error": "no file provided"}), 400

    try:
        expiry_hours = int(request.form.get("expiry_hours", DEFAULT_EXPIRY_HOURS))
    except (TypeError, ValueError):
        expiry_hours = DEFAULT_EXPIRY_HOURS
    if expiry_hours not in EXPIRY_OPTIONS:
        expiry_hours = DEFAULT_EXPIRY_HOURS

    try:
        max_downloads = int(request.form.get("max_downloads", DEFAULT_MAX_DOWNLOADS))
    except (TypeError, ValueError):
        max_downloads = DEFAULT_MAX_DOWNLOADS
    max_downloads = max(1, min(100, max_downloads))

    clean = secure_filename(f.filename) or "file"
    # cap extension length to avoid pathological stored names
    _, ext = os.path.splitext(clean)
    ext = ext[:16]

    now = int(time.time())
    share_id = new_share_id(db)
    stored_name = share_id + ext
    dest = os.path.join(UPLOAD_DIR, stored_name)

    # Stream to disk with hard size cap (belt + suspenders w/ MAX_CONTENT_LENGTH)
    size = 0
    with open(dest, "wb") as out:
        while True:
            chunk = f.stream.read(1024 * 64)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                out.close()
                try:
                    os.remove(dest)
                except OSError:
                    pass
                return jsonify({"error": "file exceeds 50MB limit"}), 413
            out.write(chunk)
    if size == 0:
        try:
            os.remove(dest)
        except OSError:
            pass
        return jsonify({"error": "empty file"}), 400

    code = str(secrets.randbelow(900000) + 100000)  # 100000-999999, shown once
    salt = secrets.token_hex(8)  # hex16
    mime = mimetypes.guess_type(clean)[0] or "application/octet-stream"

    db.execute(
        """INSERT INTO shares (id, filename, stored_name, size_bytes, mime,
                               code_hash, salt, expires_at, max_downloads,
                               download_count, attempt_count, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 'ACTIVE', ?)""",
        (share_id, clean, stored_name, size, mime,
         hash_code(code, salt), salt,
         now + expiry_hours * 3600, max_downloads, now),
    )
    db.commit()
    return jsonify({"id": share_id, "code": code, "url": f"/s/{share_id}"})


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


@app.post("/api/delete")
def api_delete():
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
