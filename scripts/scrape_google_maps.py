"""
Google Maps scraper — subprocess.
Usage: python scripts/scrape_google_maps.py "<query>" [--coords-sw "lat,lng" --coords-ne "lat,lng"]

Uses Google Places API (New) with API key from DB settings.
Saves results to businesses table and logs to operations_log.
"""

import sys
import os
import argparse
import json
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from config import DATABASE
from app.crypto import decrypt
import sqlite3


def get_db():
    conn = sqlite3.connect(DATABASE, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    return conn


def get_api_key():
    db = get_db()
    row = db.execute("SELECT value FROM settings WHERE key = 'api_key'").fetchone()
    db.close()
    if row and row["value"]:
        return decrypt(row["value"])
    return None


def log_operation(status, details, op_id=None):
    db = get_db()
    if op_id is None:
        cursor = db.execute(
            "INSERT INTO operations_log (operation_type, status, details) VALUES ('google_maps_scrape', ?, ?)",
            (status, details),
        )
        op_id = cursor.lastrowid
    else:
        db.execute(
            "UPDATE operations_log SET status = ?, details = ?, finished_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, details, op_id),
        )
    db.commit()
    db.close()
    return op_id


def search_places(api_key, query, coords_sw=None, coords_ne=None):
    """Fetch places from Google Places API (New) — Text Search.

    Fetches all available pages (max 3 = 60 results).

    Args:
        coords_sw: optional "lat,lng" — south-west corner of bounding box
        coords_ne: optional "lat,lng" — north-east corner of bounding box
    """
    url = "https://places.googleapis.com/v1/places:searchText"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.types,"
                            "places.nationalPhoneNumber,places.websiteUri,nextPageToken",
    }

    body = {"textQuery": query, "pageSize": 20}

    if coords_sw and coords_ne:
        sw = [float(x.strip()) for x in coords_sw.split(",")]
        ne = [float(x.strip()) for x in coords_ne.split(",")]
        body["locationRestriction"] = {
            "rectangle": {
                "low": {"latitude": sw[0], "longitude": sw[1]},
                "high": {"latitude": ne[0], "longitude": ne[1]},
            }
        }

    results = []
    pages_fetched = 0

    while pages_fetched < 3:
        resp = requests.post(url, json=body, headers=headers, timeout=15)
        data = resp.json()
        pages_fetched += 1

        if "error" in data:
            err = data["error"]
            raise Exception(f"API error {err.get('code')}: {err.get('message', '')}")

        for place in data.get("places", []):
            results.append({
                "name": place.get("displayName", {}).get("text", ""),
                "address": place.get("formattedAddress", ""),
                "category_google": ", ".join(place.get("types", [])),
                "place_id": place.get("id", ""),
                "phone": place.get("nationalPhoneNumber", ""),
                "website": place.get("websiteUri", ""),
            })

        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break

        body["pageToken"] = next_page_token
        time.sleep(2)

    return results


def save_business(db, biz, query):
    """Insert business, skip if name+address already exists for this query."""
    existing = db.execute(
        "SELECT id FROM businesses WHERE name = ? AND address = ? AND source_query = ?",
        (biz["name"], biz["address"], query),
    ).fetchone()
    if existing:
        return False
    db.execute(
        "INSERT INTO businesses (name, address, phone, website, category, category_google, source_query) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (biz["name"], biz["address"], biz.get("phone", ""), biz.get("website", ""), query, biz.get("category_google", ""), query),
    )
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("query", help="Search query for Google Maps")
    parser.add_argument("--coords-sw", type=str, default=None,
                        help="SW corner: lat,lng (e.g. 48.1,16.3)")
    parser.add_argument("--coords-ne", type=str, default=None,
                        help="NE corner: lat,lng (e.g. 48.3,16.6)")
    parser.add_argument("--op-id", type=int, default=None, help="Operations log ID (set by Flask)")
    args = parser.parse_args()

    api_key = get_api_key()
    if not api_key:
        msg = "Brak klucza API. Ustaw go w Ustawieniach."
        if args.op_id:
            log_operation("error", msg, args.op_id)
        print(json.dumps({"error": msg}))
        sys.exit(1)

    op_id = args.op_id or log_operation("running", f"Query: {args.query}")

    try:
        print(json.dumps({"status": "searching", "query": args.query}), flush=True)
        places = search_places(api_key, args.query, args.coords_sw, args.coords_ne)

        db = get_db()
        saved = 0
        for i, place in enumerate(places):
            if save_business(db, place, args.query):
                saved += 1

            # Progress output
            print(json.dumps({"status": "progress", "current": i + 1, "total": len(places)}), flush=True)

        db.commit()
        db.close()

        # Save scrape area if coordinates were provided
        if args.coords_sw and args.coords_ne:
            sw = [float(x.strip()) for x in args.coords_sw.split(",")]
            ne = [float(x.strip()) for x in args.coords_ne.split(",")]
            db2 = get_db()
            db2.execute(
                "INSERT INTO scrape_areas (source_query, sw_lat, sw_lng, ne_lat, ne_lng, results_count) VALUES (?, ?, ?, ?, ?, ?)",
                (args.query, sw[0], sw[1], ne[0], ne[1], len(places)),
            )
            db2.commit()
            db2.close()

        summary = f"Znaleziono {len(places)}, zapisano {saved} nowych (query: {args.query})"
        log_operation("done", summary, op_id)
        print(json.dumps({"status": "done", "found": len(places), "saved": saved}), flush=True)

    except Exception as e:
        log_operation("error", str(e), op_id)
        print(json.dumps({"error": str(e)}), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
