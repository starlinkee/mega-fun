"""
One-time migration: parse existing address field into city and country columns.
Usage: python scripts/migrate_addresses.py

Google Places formatted addresses typically end with: "..., City, Country"
This script extracts city and country from the last two comma-separated parts.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
import sqlite3
from config import DATABASE

# Pattern to strip leading postal codes like "1220", "00-123", "10115"
POSTAL_PREFIX_RE = re.compile(r'^\d[\d\s\-]{0,8}\s+', re.UNICODE)


def main():
    conn = sqlite3.connect(DATABASE, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row

    # Ensure columns exist
    for col in ("city", "country"):
        try:
            conn.execute(f"ALTER TABLE businesses ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass

    rows = conn.execute(
        "SELECT id, address, city FROM businesses WHERE address IS NOT NULL AND address != ''"
    ).fetchall()

    updated = 0
    for row in rows:
        parts = [p.strip() for p in row["address"].split(",")]
        if len(parts) >= 3:
            # Full format: "street, city, country" or "street, city, postal, country"
            country = parts[-1]
            # Find city — skip postal codes (digits/dashes)
            city = ""
            for part in reversed(parts[1:-1]):
                if not part.replace("-", "").replace(" ", "").isdigit():
                    # Strip leading postal code: "1220 Wien" -> "Wien"
                    city = POSTAL_PREFIX_RE.sub('', part).strip() or part
                    break
            conn.execute(
                "UPDATE businesses SET city = ?, country = ? WHERE id = ?",
                (city, country, row["id"]),
            )
            updated += 1
        elif len(parts) == 2:
            # Short format: "street, city" — city is last part, country unknown
            city = parts[-1]
            if not city.replace("-", "").replace(" ", "").isdigit():
                city = POSTAL_PREFIX_RE.sub('', city).strip() or city
                conn.execute(
                    "UPDATE businesses SET city = ?, country = '' WHERE id = ?",
                    (city, row["id"]),
                )
                updated += 1

    conn.commit()
    conn.close()
    print(f"Migrated {updated} / {len(rows)} businesses (city + country extracted from address)")


if __name__ == "__main__":
    main()
