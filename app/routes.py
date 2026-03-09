import subprocess
import sys
import os

from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from app.db import get_db
from app.crypto import encrypt, decrypt

main_bp = Blueprint("main", __name__)

# Track running scrape processes {op_id: Popen}
_scrape_processes = {}


@main_bp.route("/")
def index():
    return redirect(url_for("main.tab_google_maps"))


@main_bp.route("/google-maps")
def tab_google_maps():
    return render_template("tabs/google_maps.html", active_tab="google_maps")


@main_bp.route("/api/businesses")
def api_businesses():
    """AJAX endpoint: list businesses with pagination, filtering, search."""
    db = get_db()

    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    search = request.args.get("search", "").strip()
    source_query = request.args.get("source_query", "").strip()
    category = request.args.get("category", "").strip()

    per_page = min(per_page, 200)
    offset = (page - 1) * per_page

    conditions = []
    params = []
    if search:
        conditions.append("(name LIKE ? OR address LIKE ? OR phone LIKE ? OR website LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like, like])
    if source_query:
        conditions.append("source_query = ?")
        params.append(source_query)
    if category:
        conditions.append("category = ?")
        params.append(category)

    where = ""
    if conditions:
        where = "WHERE " + " AND ".join(conditions)

    total = db.execute(f"SELECT COUNT(*) FROM businesses {where}", params).fetchone()[0]

    rows = db.execute(
        f"SELECT * FROM businesses {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [per_page, offset],
    ).fetchall()

    businesses = [dict(r) for r in rows]

    # Get distinct source queries for filter dropdown
    queries = [r[0] for r in db.execute(
        "SELECT DISTINCT source_query FROM businesses WHERE source_query IS NOT NULL ORDER BY source_query"
    ).fetchall()]

    # Get distinct categories for filter dropdown
    categories = [r[0] for r in db.execute(
        "SELECT DISTINCT category FROM businesses WHERE category IS NOT NULL AND category != '' ORDER BY category"
    ).fetchall()]

    db.close()

    return jsonify({
        "businesses": businesses,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page if per_page else 1,
        "source_queries": queries,
        "categories": categories,
    })


@main_bp.route("/google-maps/scrape", methods=["POST"])
def start_scrape():
    """Start Google Maps scrape as subprocess."""
    query = request.form.get("query", "").strip()
    coords_sw = request.form.get("coords_sw", "").strip()
    coords_ne = request.form.get("coords_ne", "").strip()

    if not query:
        return jsonify({"error": "Podaj zapytanie"}), 400

    # Create operation log entry first
    db = get_db()
    cursor = db.execute(
        "INSERT INTO operations_log (operation_type, status, details) VALUES ('google_maps_scrape', 'running', ?)",
        (f"Query: {query}",),
    )
    op_id = cursor.lastrowid
    db.commit()
    db.close()

    # Launch subprocess
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "scrape_google_maps.py")
    cmd = [sys.executable, script, query, "--op-id", str(op_id)]
    if coords_sw and coords_ne:
        cmd += ["--coords-sw", coords_sw, "--coords-ne", coords_ne]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _scrape_processes[op_id] = proc

    return jsonify({"op_id": op_id, "status": "running"})


@main_bp.route("/google-maps/scrape/status/<int:op_id>")
def scrape_status(op_id):
    """Check scrape operation status."""
    db = get_db()
    row = db.execute("SELECT * FROM operations_log WHERE id = ?", (op_id,)).fetchone()
    db.close()

    if not row:
        return jsonify({"error": "Operacja nie znaleziona"}), 404

    result = {"op_id": op_id, "status": row["status"], "details": row["details"]}

    # Check if subprocess has finished
    proc = _scrape_processes.get(op_id)
    if proc and proc.poll() is not None:
        _scrape_processes.pop(op_id, None)

    return jsonify(result)


@main_bp.route("/api/scrape-areas")
def api_scrape_areas():
    """Return all scrape areas for map display."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM scrape_areas ORDER BY created_at DESC"
    ).fetchall()
    db.close()
    areas = [dict(r) for r in rows]
    return jsonify({"areas": areas})


@main_bp.route("/api/maps-key")
def api_maps_key():
    """Return decrypted Google Maps API key for Maps JS API."""
    db = get_db()
    row = db.execute("SELECT value FROM settings WHERE key = 'google_maps_key'").fetchone()
    db.close()
    if row and row["value"]:
        return jsonify({"key": decrypt(row["value"])})
    return jsonify({"key": ""})


@main_bp.route("/businesses")
def tab_businesses():
    return render_template("tabs/businesses.html", active_tab="businesses")


@main_bp.route("/api/scrape-tasks")
def api_scrape_tasks():
    """Return all scrape operations from operations_log."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM operations_log ORDER BY started_at DESC"
    ).fetchall()
    db.close()
    return jsonify({"tasks": [dict(r) for r in rows]})


@main_bp.route("/email-scraping")
def tab_email_scraping():
    return render_template("tabs/email_scraping.html", active_tab="email_scraping")


@main_bp.route("/campaigns")
def tab_campaigns():
    return render_template("tabs/campaigns.html", active_tab="campaigns")


@main_bp.route("/history")
def tab_history():
    return render_template("tabs/history.html", active_tab="history")


@main_bp.route("/settings")
def tab_settings():
    db = get_db()
    mailboxes = db.execute("SELECT * FROM mailboxes ORDER BY created_at DESC").fetchall()
    settings = {}
    for row in db.execute("SELECT key, value FROM settings").fetchall():
        settings[row["key"]] = row["value"]
    db.close()
    return render_template("tabs/settings.html", active_tab="settings", mailboxes=mailboxes, settings=settings)


@main_bp.route("/settings/api-key", methods=["POST"])
def save_api_key():
    key = request.form.get("api_key", "").strip()
    encrypted_key = encrypt(key)
    db = get_db()
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('api_key', ?)", (encrypted_key,))
    db.commit()
    db.close()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.content_type != "application/x-www-form-urlencoded":
        return jsonify({"ok": True})
    return redirect(url_for("main.tab_settings"))


@main_bp.route("/settings/google-maps-key", methods=["POST"])
def save_google_maps_key():
    key = request.form.get("google_maps_key", "").strip()
    encrypted_key = encrypt(key)
    db = get_db()
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('google_maps_key', ?)", (encrypted_key,))
    db.commit()
    db.close()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.content_type != "application/x-www-form-urlencoded":
        return jsonify({"ok": True})
    return redirect(url_for("main.tab_settings"))


@main_bp.route("/settings/mailbox", methods=["POST"])
def add_mailbox():
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "").strip()
    smtp_server = request.form.get("smtp_server", "smtp.purelymail.com").strip()
    smtp_port = int(request.form.get("smtp_port", 587))

    if email and password:
        db = get_db()
        db.execute(
            "INSERT INTO mailboxes (email, password, smtp_server, smtp_port) VALUES (?, ?, ?, ?)",
            (email, password, smtp_server, smtp_port),
        )
        db.commit()
        db.close()
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest" or "multipart/form-data" in (request.content_type or "")
    if is_ajax:
        return jsonify({"ok": True})
    return redirect(url_for("main.tab_settings"))


@main_bp.route("/settings/mailbox/<int:mailbox_id>/delete", methods=["POST"])
def delete_mailbox(mailbox_id):
    db = get_db()
    db.execute("DELETE FROM mailboxes WHERE id = ?", (mailbox_id,))
    db.commit()
    db.close()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.content_type != "application/x-www-form-urlencoded":
        return jsonify({"ok": True})
    return redirect(url_for("main.tab_settings"))
