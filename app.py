"""MyTransfer: a small, self-hosted file-sharing server.

Run with: python app.py
Then open http://localhost:8080 in a browser.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock

from flask import Flask, abort, flash, redirect, render_template, request, send_file, session, url_for
from werkzeug.utils import secure_filename


# In a packaged app, templates are unpacked to _MEIPASS while user files must
# remain in the user's writable Windows app-data folder.
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_DIR = (
    Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "MyTransfer"
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
DATA_DIR = APP_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DATABASE_PATH = DATA_DIR / "shares.json"
MAX_FILE_SIZE = int(os.environ.get("MYTRANSFER_MAX_FILE_SIZE", 5 * 1024**3))
ADMIN_PASSWORD = os.environ.get("MYTRANSFER_ADMIN_PASSWORD", "")

app = Flask(
    __name__,
    template_folder=str(RESOURCE_DIR / "templates"),
    static_folder=str(RESOURCE_DIR / "static"),
)
app.config["SECRET_KEY"] = os.environ.get("MYTRANSFER_SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE
db_lock = Lock()


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utc_now().isoformat()


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def initialise_storage() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if not DATABASE_PATH.exists():
        DATABASE_PATH.write_text("[]\n", encoding="utf-8")


def load_shares() -> list[dict]:
    initialise_storage()
    with db_lock:
        try:
            return json.loads(DATABASE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Do not overwrite a damaged file: an administrator can recover it.
            return []


def save_shares(shares: list[dict]) -> None:
    with db_lock:
        temporary = DATABASE_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(shares, indent=2) + "\n", encoding="utf-8")
        temporary.replace(DATABASE_PATH)


def create_share_from_path(source_path: Path, expiry_option: str, original_name: str | None = None) -> dict:
    """Copy a selected local file into MyTransfer storage and create its link."""
    expiry_options = {"3d": timedelta(days=3), "7d": timedelta(days=7)}
    if expiry_option not in expiry_options:
        raise ValueError("Expiry must be 3d or 7d")
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if source_path.stat().st_size > MAX_FILE_SIZE:
        raise ValueError(f"File exceeds the {format_size(MAX_FILE_SIZE)} limit")

    original_name = secure_filename(original_name or source_path.name)
    if not original_name:
        raise ValueError("That filename is not valid")
    initialise_storage()
    stored_name = f"{uuid.uuid4().hex}_{original_name}"
    stored_path = UPLOAD_DIR / stored_name
    # Stream-copying avoids reading a potentially large file into memory.
    with source_path.open("rb") as source, stored_path.open("wb") as destination:
        while chunk := source.read(1024 * 1024):
            destination.write(chunk)

    share = {
        "id": uuid.uuid4().hex,
        "token": secrets.token_urlsafe(24),
        "original_name": original_name,
        "stored_name": stored_name,
        "size": stored_path.stat().st_size,
        "created_at": iso_now(),
        "expires_at": (utc_now() + expiry_options[expiry_option]).isoformat(),
        "downloads": 0,
    }
    shares = load_shares()
    shares.append(share)
    save_shares(shares)
    return share


def is_expired(share: dict) -> bool:
    expiry = share.get("expires_at")
    return bool(expiry and parse_iso(expiry) <= utc_now())


def public_base_url() -> str:
    """A configured public URL is useful when this app is run through a tunnel."""
    configured = os.environ.get("MYTRANSFER_PUBLIC_URL", "").rstrip("/")
    return configured or request.url_root.rstrip("/")


def dashboard_is_protected() -> bool:
    return bool(ADMIN_PASSWORD)


def require_dashboard_access():
    if dashboard_is_protected() and not session.get("dashboard_authorised"):
        return redirect(url_for("login"))
    return None


@app.route("/login", methods=["GET", "POST"])
def login():
    if not dashboard_is_protected():
        return redirect(url_for("index"))
    if request.method == "POST":
        password = request.form.get("password", "")
        if secrets.compare_digest(password, ADMIN_PASSWORD):
            session.clear()
            session["dashboard_authorised"] = True
            return redirect(url_for("index"))
        flash("Incorrect password.", "error")
    return render_template("login.html")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
def index():
    access_denied = require_dashboard_access()
    if access_denied:
        return access_denied
    shares = load_shares()
    shares.sort(key=lambda item: item["created_at"], reverse=True)
    for share in shares:
        share["is_expired"] = is_expired(share)
        share["link"] = f"{public_base_url()}{url_for('download', token=share['token'])}"
    return render_template("index.html", shares=shares, max_file_size=MAX_FILE_SIZE, dashboard_protected=dashboard_is_protected())


@app.post("/shares")
def create_share():
    access_denied = require_dashboard_access()
    if access_denied:
        return access_denied
    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        flash("Choose a file first.", "error")
        return redirect(url_for("index"))

    original_name = secure_filename(uploaded.filename)
    if not original_name:
        flash("That filename is not valid.", "error")
        return redirect(url_for("index"))

    option = request.form.get("expires", "7d")
    if option not in {"3d", "7d"}:
        abort(400)
    initialise_storage()
    temporary_path = UPLOAD_DIR / f".incoming_{uuid.uuid4().hex}"
    uploaded.save(temporary_path)
    try:
        share = create_share_from_path(temporary_path, option, original_name)
    finally:
        temporary_path.unlink(missing_ok=True)
    flash("Your share link is ready.", "success")
    return redirect(url_for("index"))


@app.get("/download/<token>")
def download(token: str):
    shares = load_shares()
    share = next((item for item in shares if secrets.compare_digest(item["token"], token)), None)
    if not share or is_expired(share):
        abort(404)

    path = UPLOAD_DIR / share["stored_name"]
    if not path.is_file():
        abort(404)
    share["downloads"] += 1
    save_shares(shares)
    return send_file(path, as_attachment=True, download_name=share["original_name"], max_age=0)


@app.post("/shares/<share_id>/revoke")
def revoke_share(share_id: str):
    access_denied = require_dashboard_access()
    if access_denied:
        return access_denied
    shares = load_shares()
    share = next((item for item in shares if item["id"] == share_id), None)
    if not share:
        abort(404)
    path = UPLOAD_DIR / share["stored_name"]
    if path.exists():
        path.unlink()
    save_shares([item for item in shares if item["id"] != share_id])
    flash("The link has been revoked and its stored file deleted.", "success")
    return redirect(url_for("index"))


@app.errorhandler(413)
def file_too_large(_error):
    flash(f"That file is larger than the {format_size(MAX_FILE_SIZE)} limit.", "error")
    return redirect(url_for("index"))


@app.template_filter("size")
def format_size(size: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


@app.template_filter("datetime")
def format_datetime(value: str | None) -> str:
    if not value:
        return "Never"
    return parse_iso(value).astimezone().strftime("%d %b %Y, %H:%M")


if __name__ == "__main__":
    initialise_storage()
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)
