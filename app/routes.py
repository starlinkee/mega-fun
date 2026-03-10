import subprocess
import sys
import os
import re
import time
import json

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session
from app.db import get_db
from app.crypto import encrypt, decrypt
from config import APP_PASSWORD

# Brute force protection: track failed login attempts per IP
_login_attempts = {}  # ip -> {"count": int, "locked_until": float}
_MAX_ATTEMPTS = 5
_LOCKOUT_SECONDS = 300  # 5 minut


def _extract_source_query(details):
    """Extract source_query from operations_log details field.
    Handles both formats:
      - initial: 'Query: restauracja'
      - final:   'Znaleziono 60, zapisano 60 nowych (query: restauracja)'
    """
    if not details:
        return None
    m = re.search(r'\(query:\s*(.+?)\)\s*$', details)
    if m:
        return m.group(1).strip()
    if details.startswith("Query: "):
        return details[7:].strip()
    return None

main_bp = Blueprint("main", __name__)

# Track running scrape processes {op_id: Popen}
_scrape_processes = {}


@main_bp.before_app_request
def require_login():
    allowed = ("main.login", "static")
    if request.endpoint in allowed:
        return
    if not session.get("authenticated"):
        return redirect(url_for("main.login"))


@main_bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    ip = request.remote_addr

    if request.method == "POST":
        now = time.time()
        attempt = _login_attempts.get(ip, {"count": 0, "locked_until": 0})

        if attempt["locked_until"] > now:
            remaining = int(attempt["locked_until"] - now)
            return render_template("login.html", error=f"Zbyt wiele prób. Spróbuj za {remaining} sekund.")

        password = request.form.get("password", "")
        if password == APP_PASSWORD:
            _login_attempts.pop(ip, None)
            session["authenticated"] = True
            return redirect(url_for("main.index"))

        attempt["count"] += 1
        if attempt["count"] >= _MAX_ATTEMPTS:
            attempt["locked_until"] = now + _LOCKOUT_SECONDS
            attempt["count"] = 0
            _login_attempts[ip] = attempt
            return render_template("login.html", error=f"Zbyt wiele nieudanych prób. Konto zablokowane na {_LOCKOUT_SECONDS // 60} minut.")

        _login_attempts[ip] = attempt
        remaining_attempts = _MAX_ATTEMPTS - attempt["count"]
        error = f"Nieprawidłowe hasło. Pozostało prób: {remaining_attempts}."

    return render_template("login.html", error=error)


@main_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("main.login"))


@main_bp.route("/")
def index():
    return redirect(url_for("main.tab_dashboard"))


@main_bp.route("/dashboard")
def tab_dashboard():
    return render_template("tabs/dashboard.html", active_tab="dashboard")


@main_bp.route("/api/dashboard-stats")
def api_dashboard_stats():
    """Return dashboard statistics: today, this month, total."""
    db = get_db()

    stats = {}

    # Scrapes (operations_log)
    stats["scrapes_total"] = db.execute(
        "SELECT COUNT(*) FROM operations_log"
    ).fetchone()[0]
    stats["scrapes_month"] = db.execute(
        "SELECT COUNT(*) FROM operations_log WHERE strftime('%Y-%m', started_at, 'localtime') = strftime('%Y-%m', 'now', 'localtime')"
    ).fetchone()[0]
    stats["scrapes_today"] = db.execute(
        "SELECT COUNT(*) FROM operations_log WHERE date(started_at, 'localtime') = date('now', 'localtime')"
    ).fetchone()[0]

    # Businesses
    stats["businesses_total"] = db.execute(
        "SELECT COUNT(*) FROM businesses"
    ).fetchone()[0]
    stats["businesses_month"] = db.execute(
        "SELECT COUNT(*) FROM businesses WHERE strftime('%Y-%m', created_at, 'localtime') = strftime('%Y-%m', 'now', 'localtime')"
    ).fetchone()[0]
    stats["businesses_today"] = db.execute(
        "SELECT COUNT(*) FROM businesses WHERE date(created_at, 'localtime') = date('now', 'localtime')"
    ).fetchone()[0]

    # Websites (businesses with non-empty website)
    stats["websites_total"] = db.execute(
        "SELECT COUNT(*) FROM businesses WHERE website IS NOT NULL AND website != ''"
    ).fetchone()[0]
    stats["websites_month"] = db.execute(
        "SELECT COUNT(*) FROM businesses WHERE website IS NOT NULL AND website != '' AND strftime('%Y-%m', created_at, 'localtime') = strftime('%Y-%m', 'now', 'localtime')"
    ).fetchone()[0]
    stats["websites_today"] = db.execute(
        "SELECT COUNT(*) FROM businesses WHERE website IS NOT NULL AND website != '' AND date(created_at, 'localtime') = date('now', 'localtime')"
    ).fetchone()[0]

    # Emails scraped
    stats["emails_total"] = db.execute(
        "SELECT COUNT(*) FROM emails"
    ).fetchone()[0]
    stats["emails_month"] = db.execute(
        "SELECT COUNT(*) FROM emails WHERE strftime('%Y-%m', created_at, 'localtime') = strftime('%Y-%m', 'now', 'localtime')"
    ).fetchone()[0]
    stats["emails_today"] = db.execute(
        "SELECT COUNT(*) FROM emails WHERE date(created_at, 'localtime') = date('now', 'localtime')"
    ).fetchone()[0]

    # Campaigns
    stats["campaigns_total"] = db.execute(
        "SELECT COUNT(*) FROM campaigns"
    ).fetchone()[0]
    stats["campaigns_month"] = db.execute(
        "SELECT COUNT(*) FROM campaigns WHERE strftime('%Y-%m', created_at, 'localtime') = strftime('%Y-%m', 'now', 'localtime')"
    ).fetchone()[0]
    stats["campaigns_today"] = db.execute(
        "SELECT COUNT(*) FROM campaigns WHERE date(created_at, 'localtime') = date('now', 'localtime')"
    ).fetchone()[0]

    # Emails sent (campaign_emails with status='sent')
    stats["sent_total"] = db.execute(
        "SELECT COUNT(*) FROM campaign_emails WHERE status = 'sent'"
    ).fetchone()[0]
    stats["sent_month"] = db.execute(
        "SELECT COUNT(*) FROM campaign_emails WHERE status = 'sent' AND strftime('%Y-%m', sent_at, 'localtime') = strftime('%Y-%m', 'now', 'localtime')"
    ).fetchone()[0]
    stats["sent_today"] = db.execute(
        "SELECT COUNT(*) FROM campaign_emails WHERE status = 'sent' AND date(sent_at, 'localtime') = date('now', 'localtime')"
    ).fetchone()[0]

    db.close()
    return jsonify(stats)


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
    country = request.args.get("country", "").strip()
    city = request.args.get("city", "").strip()

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
    if country:
        conditions.append("country = ?")
        params.append(country)
    if city:
        conditions.append("city = ?")
        params.append(city)

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

    # Get distinct countries and cities for filter dropdowns
    countries = [r[0] for r in db.execute(
        "SELECT DISTINCT country FROM businesses WHERE country IS NOT NULL AND country != '' ORDER BY country"
    ).fetchall()]

    cities = [r[0] for r in db.execute(
        "SELECT DISTINCT city FROM businesses WHERE city IS NOT NULL AND city != '' ORDER BY city"
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
        "countries": countries,
        "cities": cities,
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
    row = db.execute("SELECT value FROM settings WHERE key = 'api_key'").fetchone()
    db.close()
    if row and row["value"]:
        return jsonify({"key": decrypt(row["value"])})
    return jsonify({"key": ""})


@main_bp.route("/businesses")
def tab_businesses():
    return render_template("tabs/businesses.html", active_tab="businesses")


@main_bp.route("/api/scrape-tasks")
def api_scrape_tasks():
    """Return all scrape operations from operations_log with business counts."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM operations_log WHERE operation_type = 'google_maps_scrape' ORDER BY started_at DESC"
    ).fetchall()
    tasks = []
    for row in rows:
        t = dict(row)
        source_query = _extract_source_query(t.get("details"))
        t["source_query"] = source_query
        if source_query:
            count = db.execute(
                "SELECT COUNT(*) FROM businesses WHERE source_query = ?",
                (source_query,),
            ).fetchone()[0]
        else:
            count = 0
        t["business_count"] = count
        tasks.append(t)
    db.close()
    return jsonify({"tasks": tasks})


@main_bp.route("/api/scrape-tasks/<int:op_id>/emails")
def api_scrape_task_emails(op_id):
    """Return emails collected in a specific scrape task (by source_query)."""
    db = get_db()
    row = db.execute("SELECT * FROM operations_log WHERE id = ?", (op_id,)).fetchone()
    if not row:
        db.close()
        return jsonify({"error": "Nie znaleziono zadania"}), 404

    source_query = _extract_source_query(row["details"])

    if not source_query:
        db.close()
        return jsonify({"emails": [], "source_query": None})

    emails = db.execute(
        """SELECT e.id, e.email, e.source, e.created_at,
                  b.name AS business_name, b.website
           FROM emails e
           JOIN businesses b ON e.business_id = b.id
           WHERE b.source_query = ?
           ORDER BY e.created_at DESC""",
        (source_query,),
    ).fetchall()
    db.close()
    return jsonify({"emails": [dict(e) for e in emails], "source_query": source_query})


@main_bp.route("/email-scraping")
def tab_email_scraping():
    return render_template("tabs/email_scraping.html", active_tab="email_scraping")


# Track running email scrape processes {op_id: Popen}
_email_scrape_processes = {}


def _launch_email_scrape_subprocess(op_id, source_query, business_ids, country, city, max_pages):
    """Build command and launch email scrape subprocess. Updates _email_scrape_processes."""
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "scrape_emails.py")
    cmd = [sys.executable, script]
    # Pass string params via env vars to avoid [Errno 22] Invalid argument on Windows
    # caused by non-ASCII characters (Polish diacritics) in CLI arguments.
    env = os.environ.copy()
    env["SCRAPE_OP_ID"] = str(op_id)
    env["SCRAPE_MAX_PAGES"] = str(max_pages)
    env["SCRAPE_SOURCE_QUERY"] = source_query or ""
    env["SCRAPE_BUSINESS_IDS"] = business_ids or ""
    env["SCRAPE_COUNTRY"] = country or ""
    env["SCRAPE_CITY"] = city or ""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    _email_scrape_processes[op_id] = proc


def _promote_next_email_scrape():
    """Promote the oldest queued email scrape to running and launch it."""
    db = get_db()
    row = db.execute(
        "SELECT * FROM operations_log WHERE operation_type = 'email_scrape' AND status = 'queued'"
        " ORDER BY started_at ASC LIMIT 1"
    ).fetchone()
    if not row:
        db.close()
        return

    op_id = row["id"]
    params = json.loads(row["params"] or "{}")
    source_query = params.get("source_query", "")
    business_ids = params.get("business_ids", "")
    country = params.get("country", "")
    city = params.get("city", "")

    filters = []
    if source_query:
        filters.append(f"query: {source_query}")
    if country:
        filters.append(f"kraj: {country}")
    if city:
        filters.append(f"miasto: {city}")
    details_suffix = f" ({', '.join(filters)})" if filters else ""

    db.execute(
        "UPDATE operations_log SET status = 'running', details = ? WHERE id = ?",
        (f"Scraping emaili{details_suffix}", op_id),
    )
    db.commit()

    max_pages_row = db.execute("SELECT value FROM settings WHERE key = 'email_max_pages'").fetchone()
    max_pages = max_pages_row["value"] if max_pages_row else "10"
    db.close()

    _launch_email_scrape_subprocess(op_id, source_query, business_ids, country, city, max_pages)


@main_bp.route("/email-scraping/scrape", methods=["POST"])
def start_email_scrape():
    """Start email scraping as subprocess, or queue it if one is already running."""
    source_query = request.form.get("source_query", "").strip()
    business_ids = request.form.get("business_ids", "").strip()
    country = request.form.get("country", "").strip()
    city = request.form.get("city", "").strip()

    filters = []
    if source_query:
        filters.append(f"query: {source_query}")
    if country:
        filters.append(f"kraj: {country}")
    if city:
        filters.append(f"miasto: {city}")
    details_suffix = f" ({', '.join(filters)})" if filters else ""

    db = get_db()

    # Check if any email scrape is already running
    already_running = db.execute(
        "SELECT id FROM operations_log WHERE operation_type = 'email_scrape' AND status = 'running' LIMIT 1"
    ).fetchone()

    if already_running:
        # Queue this job — store params as JSON so we can launch it later
        params_json = json.dumps({
            "source_query": source_query,
            "business_ids": business_ids,
            "country": country,
            "city": city,
        })
        cursor = db.execute(
            "INSERT INTO operations_log (operation_type, status, details, params) VALUES ('email_scrape', 'queued', ?, ?)",
            (f"W kolejce{details_suffix}", params_json),
        )
        op_id = cursor.lastrowid
        db.commit()
        db.close()
        return jsonify({"op_id": op_id, "status": "queued"})

    # No scrape running — start immediately
    cursor = db.execute(
        "INSERT INTO operations_log (operation_type, status, details) VALUES ('email_scrape', 'running', ?)",
        (f"Scraping emaili{details_suffix}",),
    )
    op_id = cursor.lastrowid
    db.commit()

    max_pages_row = db.execute("SELECT value FROM settings WHERE key = 'email_max_pages'").fetchone()
    max_pages = max_pages_row["value"] if max_pages_row else "10"
    db.close()

    _launch_email_scrape_subprocess(op_id, source_query, business_ids, country, city, max_pages)

    return jsonify({"op_id": op_id, "status": "running"})


@main_bp.route("/email-scraping/scrape/status/<int:op_id>")
def email_scrape_status(op_id):
    """Check email scrape operation status. Promotes next queued job when current finishes."""
    db = get_db()
    row = db.execute("SELECT * FROM operations_log WHERE id = ?", (op_id,)).fetchone()
    db.close()

    if not row:
        return jsonify({"error": "Operacja nie znaleziona"}), 404

    result = {"op_id": op_id, "status": row["status"], "details": row["details"]}

    proc = _email_scrape_processes.get(op_id)
    if proc and proc.poll() is not None:
        _email_scrape_processes.pop(op_id, None)
        # Process just finished — promote next queued scrape if any
        _promote_next_email_scrape()

    return jsonify(result)


@main_bp.route("/api/emails")
def api_emails():
    """AJAX endpoint: list emails with pagination, filtering, search."""
    db = get_db()

    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    search = request.args.get("search", "").strip()
    source_query = request.args.get("source_query", "").strip()

    per_page = min(per_page, 200)
    offset = (page - 1) * per_page

    conditions = []
    params = []
    if search:
        conditions.append("(e.email LIKE ? OR b.name LIKE ? OR e.source LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like])
    if source_query:
        conditions.append("b.source_query = ?")
        params.append(source_query)

    where = ""
    if conditions:
        where = "WHERE " + " AND ".join(conditions)

    total = db.execute(
        f"SELECT COUNT(*) FROM emails e LEFT JOIN businesses b ON e.business_id = b.id {where}", params
    ).fetchone()[0]

    rows = db.execute(
        f"""SELECT e.id, e.email, e.source, e.created_at, e.business_id,
                   b.name AS business_name, b.source_query
            FROM emails e
            LEFT JOIN businesses b ON e.business_id = b.id
            {where}
            ORDER BY e.created_at DESC LIMIT ? OFFSET ?""",
        params + [per_page, offset],
    ).fetchall()

    emails = [dict(r) for r in rows]

    # Distinct source queries for filter
    queries = [r[0] for r in db.execute(
        "SELECT DISTINCT b.source_query FROM emails e JOIN businesses b ON e.business_id = b.id WHERE b.source_query IS NOT NULL ORDER BY b.source_query"
    ).fetchall()]

    db.close()

    return jsonify({
        "emails": emails,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page if per_page else 1,
        "source_queries": queries,
    })


@main_bp.route("/api/emails/<int:email_id>/delete", methods=["POST"])
def delete_email(email_id):
    """Delete a single email."""
    db = get_db()
    db.execute("DELETE FROM emails WHERE id = ?", (email_id,))
    db.commit()
    db.close()
    return jsonify({"ok": True})


@main_bp.route("/api/business-locations")
def api_business_locations():
    """Return distinct countries and cities from businesses table."""
    db = get_db()
    country_filter = request.args.get("country", "").strip()

    countries = [r[0] for r in db.execute(
        "SELECT DISTINCT country FROM businesses WHERE country IS NOT NULL AND country != '' ORDER BY country"
    ).fetchall()]

    city_q = "SELECT DISTINCT city FROM businesses WHERE city IS NOT NULL AND city != ''"
    city_params = []
    if country_filter:
        city_q += " AND country = ?"
        city_params.append(country_filter)
    city_q += " ORDER BY city"
    cities = [r[0] for r in db.execute(city_q, city_params).fetchall()]

    db.close()
    return jsonify({"countries": countries, "cities": cities})


@main_bp.route("/api/email-scrape-tasks")
def api_email_scrape_tasks():
    """Return email scrape operations from operations_log."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM operations_log WHERE operation_type = 'email_scrape' ORDER BY started_at DESC"
    ).fetchall()
    db.close()
    return jsonify({"tasks": [dict(r) for r in rows]})


@main_bp.route("/campaigns")
def tab_campaigns():
    db = get_db()
    campaigns = db.execute("SELECT * FROM campaigns ORDER BY created_at DESC").fetchall()
    campaigns_list = []
    for c in campaigns:
        d = dict(c)
        total = db.execute("SELECT COUNT(*) FROM campaign_emails WHERE campaign_id = ?", (c["id"],)).fetchone()[0]
        sent = db.execute("SELECT COUNT(*) FROM campaign_emails WHERE campaign_id = ? AND status = 'sent'", (c["id"],)).fetchone()[0]
        failed = db.execute("SELECT COUNT(*) FROM campaign_emails WHERE campaign_id = ? AND status = 'failed'", (c["id"],)).fetchone()[0]
        d["total"] = total
        d["sent"] = sent
        d["failed"] = failed
        campaigns_list.append(d)
    categories = db.execute(
        "SELECT DISTINCT category FROM businesses WHERE category IS NOT NULL AND category != '' ORDER BY category"
    ).fetchall()
    categories_list = [r["category"] for r in categories]
    db.close()
    return render_template("tabs/campaigns.html", active_tab="campaigns", campaigns=campaigns_list, categories=categories_list)


@main_bp.route("/campaigns/create", methods=["POST"])
def create_campaign():
    name = request.form.get("name", "").strip()
    subject = request.form.get("subject", "").strip()
    body = request.form.get("body", "").strip()
    target_city = request.form.get("target_city", "").strip() or None
    target_country = request.form.get("target_country", "").strip() or None
    target_category = request.form.get("target_category", "").strip() or None

    if not name or not subject or not body:
        return jsonify({"error": "Wypełnij wszystkie pola"}), 400

    db = get_db()

    # If another campaign is already active, queue this one
    active = db.execute("SELECT id FROM campaigns WHERE status = 'active'").fetchone()
    status = "queued" if active else "active"

    cursor = db.execute(
        "INSERT INTO campaigns (name, subject, body_template, status, target_city, target_country, target_category) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, subject, body, status, target_city, target_country, target_category),
    )
    campaign_id = cursor.lastrowid

    # Add only emails not yet successfully sent in any previous campaign
    # Optionally filter by city and/or country
    query = """SELECT e.id FROM emails e
               JOIN businesses b ON e.business_id = b.id
               WHERE e.id NOT IN (
                   SELECT email_id FROM campaign_emails WHERE status IN ('sent', 'failed')
               )"""
    params = []
    if target_country:
        query += " AND b.country = ?"
        params.append(target_country)
    if target_city:
        query += " AND b.city = ?"
        params.append(target_city)
    if target_category:
        query += " AND b.category = ?"
        params.append(target_category)
    emails = db.execute(query, params).fetchall()
    for e in emails:
        db.execute(
            "INSERT INTO campaign_emails (campaign_id, email_id, status) VALUES (?, ?, 'pending')",
            (campaign_id, e["id"]),
        )

    db.commit()
    count = len(emails)
    db.close()

    return jsonify({"ok": True, "campaign_id": campaign_id, "emails_queued": count, "status": status})


@main_bp.route("/campaigns/<int:campaign_id>/stop", methods=["POST"])
def stop_campaign(campaign_id):
    db = get_db()
    db.execute("UPDATE campaigns SET status = 'stopped' WHERE id = ?", (campaign_id,))
    db.commit()
    db.close()
    return jsonify({"ok": True})


@main_bp.route("/campaigns/<int:campaign_id>/resume", methods=["POST"])
def resume_campaign(campaign_id):
    db = get_db()
    # If another campaign is active, queue this one; otherwise activate
    active = db.execute(
        "SELECT id FROM campaigns WHERE status = 'active' AND id != ?", (campaign_id,)
    ).fetchone()
    new_status = "queued" if active else "active"
    db.execute("UPDATE campaigns SET status = ? WHERE id = ?", (new_status, campaign_id))
    db.commit()
    db.close()
    return jsonify({"ok": True, "status": new_status})


@main_bp.route("/campaigns/<int:campaign_id>/delete", methods=["POST"])
def delete_campaign(campaign_id):
    db = get_db()
    db.execute("DELETE FROM campaign_emails WHERE campaign_id = ?", (campaign_id,))
    db.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))
    db.commit()
    db.close()
    return jsonify({"ok": True})



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



@main_bp.route("/settings/email-scraping", methods=["POST"])
def save_email_scraping_settings():
    max_pages = request.form.get("email_max_pages", "10").strip()
    try:
        max_pages = str(max(1, min(100, int(max_pages))))
    except ValueError:
        max_pages = "10"
    db = get_db()
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('email_max_pages', ?)", (max_pages,))
    db.commit()
    db.close()
    return jsonify({"ok": True})


@main_bp.route("/settings/daily-email-limit", methods=["POST"])
def save_daily_email_limit():
    limit = request.form.get("daily_email_limit", "0").strip()
    try:
        limit = str(max(0, int(limit)))
    except ValueError:
        limit = "0"
    db = get_db()
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('daily_email_limit', ?)", (limit,))
    db.commit()
    db.close()
    return jsonify({"ok": True})


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
            (email, encrypt(password), smtp_server, smtp_port),
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
