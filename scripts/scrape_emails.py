"""
Email scraper — subprocess.
Usage: python scripts/scrape_emails.py [--op-id N] [--source-query "query"] [--business-ids "1,2,3"] [--max-pages 10]

Visits business websites and extracts email addresses.
For each business: crawls the homepage + internal links (up to --max-pages limit).
Saves results to emails table and logs to operations_log.
"""

import sys
import os
import re
import json
import argparse
import time
import random
from urllib.parse import urljoin, urlparse

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from bs4 import BeautifulSoup
import sqlite3
from config import DATABASE

# Default max pages to crawl per business website
DEFAULT_MAX_PAGES = 10

# Email regex — matches common email patterns
EMAIL_RE = re.compile(
    r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}',
    re.IGNORECASE,
)

# Extensions that are NOT real emails (image files etc.)
IGNORE_EXTENSIONS = {
    'png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp', 'ico',
    'pdf', 'zip', 'rar', 'exe', 'mp3', 'mp4', 'avi', 'mov',
    'woff', 'woff2', 'ttf', 'eot', 'css', 'js',
}

# File extensions in URLs to skip (not HTML pages)
SKIP_URL_EXTENSIONS = {
    '.pdf', '.zip', '.rar', '.exe', '.mp3', '.mp4', '.avi', '.mov',
    '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.bmp', '.ico',
    '.woff', '.woff2', '.ttf', '.eot', '.css', '.js', '.xml', '.json',
    '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
}

# Common dummy/system emails to skip
IGNORE_EMAILS = {
    'example@example.com', 'test@test.com', 'email@example.com',
    'name@domain.com', 'user@example.com', 'your@email.com',
    'noreply@', 'no-reply@',
}

# Priority subpages to crawl first (contact pages usually have emails)
PRIORITY_PATHS = [
    '/kontakt', '/contact', '/kontakt.html', '/contact.html',
    '/about', '/o-nas', '/about-us', '/impressum',
    '/kontakty', '/dane-kontaktowe', '/napisz-do-nas',
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9,pl;q=0.8',
}


def get_db():
    conn = sqlite3.connect(DATABASE, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    return conn


def log_operation(status, details, op_id=None):
    db = get_db()
    if op_id is None:
        cursor = db.execute(
            "INSERT INTO operations_log (operation_type, status, details) VALUES ('email_scrape', ?, ?)",
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


def is_valid_email(email):
    """Filter out fake/system/file emails."""
    email_lower = email.lower().strip()

    # Check extension — the TLD part
    tld = email_lower.rsplit('.', 1)[-1]
    if tld in IGNORE_EXTENSIONS:
        return False

    # Check against known dummy emails
    for ignore in IGNORE_EMAILS:
        if email_lower == ignore or email_lower.startswith(ignore):
            return False

    # Must have reasonable length
    if len(email_lower) < 5 or len(email_lower) > 254:
        return False

    return True


def extract_emails_from_html(html):
    """Extract emails from HTML string using multiple methods."""
    emails = set()
    soup = BeautifulSoup(html, 'lxml')

    # Method 1: mailto: links
    for link in soup.find_all('a', href=True):
        href = link['href']
        if href.startswith('mailto:'):
            email = href.replace('mailto:', '').split('?')[0].strip()
            if EMAIL_RE.match(email) and is_valid_email(email):
                emails.add(email.lower())

    # Method 2: Regex on visible text
    text = soup.get_text(separator=' ')
    for match in EMAIL_RE.findall(text):
        if is_valid_email(match):
            emails.add(match.lower())

    # Method 3: Regex on raw HTML (attributes, comments, etc.)
    for match in EMAIL_RE.findall(html):
        if is_valid_email(match):
            emails.add(match.lower())

    return emails, soup


def get_internal_links(soup, base_url):
    """Extract internal links from parsed HTML. Returns list of absolute URLs."""
    parsed_base = urlparse(base_url)
    base_domain = parsed_base.netloc.lower()
    links = []

    for a in soup.find_all('a', href=True):
        href = a['href'].strip()

        # Skip anchors, javascript, mailto, tel
        if href.startswith(('#', 'javascript:', 'mailto:', 'tel:')):
            continue

        # Build absolute URL
        abs_url = urljoin(base_url, href)
        parsed = urlparse(abs_url)

        # Must be same domain
        if parsed.netloc.lower() != base_domain:
            continue

        # Must be http/https
        if parsed.scheme not in ('http', 'https'):
            continue

        # Skip file downloads
        path_lower = parsed.path.lower()
        if any(path_lower.endswith(ext) for ext in SKIP_URL_EXTENSIONS):
            continue

        # Normalize: strip fragment, keep path+query
        clean_url = parsed._replace(fragment='').geturl()
        links.append(clean_url)

    return links


def crawl_website(url, max_pages):
    """Crawl a website starting from url, visiting up to max_pages internal pages.

    Returns (all_emails: set, error: str|None, pages_visited: int).
    Priority: contact-like pages are visited first.
    """
    all_emails = set()

    # Ensure URL has scheme
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    visited = set()
    to_visit_priority = []  # contact-like pages first
    to_visit_normal = [url]  # then other internal links

    try:
        while len(visited) < max_pages and (to_visit_priority or to_visit_normal):
            # Pick next URL: priority pages first
            if to_visit_priority:
                current_url = to_visit_priority.pop(0)
            else:
                current_url = to_visit_normal.pop(0)

            # Normalize for dedup
            normalized = urlparse(current_url)._replace(fragment='').geturl()
            if normalized in visited:
                continue
            visited.add(normalized)

            try:
                resp = requests.get(current_url, headers=HEADERS, timeout=10, allow_redirects=True)
                if resp.status_code != 200:
                    continue

                # Only parse HTML responses
                content_type = resp.headers.get('Content-Type', '')
                if 'text/html' not in content_type:
                    continue

                page_emails, soup = extract_emails_from_html(resp.text)
                all_emails.update(page_emails)

                # Extract internal links for further crawling
                if len(visited) < max_pages:
                    links = get_internal_links(soup, resp.url)
                    for link in links:
                        link_norm = urlparse(link)._replace(fragment='').geturl()
                        if link_norm in visited:
                            continue
                        # Check if it's a priority (contact-like) path
                        link_path = urlparse(link).path.lower().rstrip('/')
                        is_priority = any(
                            link_path == p.rstrip('/') or link_path.endswith(p.rstrip('/'))
                            for p in PRIORITY_PATHS
                        )
                        if is_priority and link_norm not in [urlparse(u)._replace(fragment='').geturl() for u in to_visit_priority]:
                            to_visit_priority.append(link)
                        elif link_norm not in [urlparse(u)._replace(fragment='').geturl() for u in to_visit_normal]:
                            to_visit_normal.append(link)

                # Small delay between requests to same site
                time.sleep(random.uniform(1.0, 2.0))

            except Exception:
                continue

    except Exception as e:
        return all_emails, str(e), len(visited)

    return all_emails, None, len(visited)


def main():
    # Read params from env vars (set by Flask to avoid [Errno 22] on Windows with non-ASCII CLI args).
    # CLI args are kept as fallback for manual usage.
    _env_op_id = os.environ.get("SCRAPE_OP_ID")
    _env_max_pages = os.environ.get("SCRAPE_MAX_PAGES")

    parser = argparse.ArgumentParser()
    parser.add_argument("--op-id", type=int,
                        default=int(_env_op_id) if _env_op_id else None)
    parser.add_argument("--source-query", type=str,
                        default=os.environ.get("SCRAPE_SOURCE_QUERY") or None)
    parser.add_argument("--business-ids", type=str,
                        default=os.environ.get("SCRAPE_BUSINESS_IDS") or None)
    parser.add_argument("--country", type=str,
                        default=os.environ.get("SCRAPE_COUNTRY") or None)
    parser.add_argument("--city", type=str,
                        default=os.environ.get("SCRAPE_CITY") or None)
    parser.add_argument("--max-pages", type=int,
                        default=int(_env_max_pages) if _env_max_pages else DEFAULT_MAX_PAGES)
    args = parser.parse_args()

    op_id = args.op_id or log_operation("running", "Rozpoczynanie scrapowania emaili...")

    try:
        db = get_db()

        # Build query for businesses with websites
        conditions = ["website IS NOT NULL", "website != ''"]
        params = []

        if args.business_ids:
            ids = [int(x.strip()) for x in args.business_ids.split(',') if x.strip()]
            placeholders = ','.join('?' * len(ids))
            conditions.append(f"id IN ({placeholders})")
            params.extend(ids)

        if args.source_query:
            conditions.append("source_query = ?")
            params.append(args.source_query)

        if args.country:
            conditions.append("country = ?")
            params.append(args.country)

        if args.city:
            conditions.append("city = ?")
            params.append(args.city)

        where = "WHERE " + " AND ".join(conditions)
        businesses = db.execute(
            f"SELECT id, name, website FROM businesses {where}", params
        ).fetchall()
        db.close()

        total = len(businesses)
        if total == 0:
            log_operation("done", "Brak biznesow z stronami www do przeskanowania", op_id)
            print(json.dumps({"status": "done", "found": 0, "saved": 0, "errors": 0}), flush=True)
            return

        log_operation("running", f"Skanowanie {total} stron www (max {args.max_pages} podstron/biznes)...", op_id)
        print(json.dumps({"status": "running", "total": total}), flush=True)

        total_found = 0
        total_saved = 0
        total_errors = 0
        total_pages_visited = 0

        for i, biz in enumerate(businesses):
            biz_id = biz['id']
            website = biz['website']

            try:
                emails, error, pages_visited = crawl_website(website, args.max_pages)
                total_pages_visited += pages_visited

                if error:
                    total_errors += 1

                total_found += len(emails)

                if emails:
                    db = get_db()
                    for email in emails:
                        cursor = db.execute(
                            "INSERT OR IGNORE INTO emails (email, business_id, source) VALUES (?, ?, ?)",
                            (email, biz_id, website),
                        )
                        if cursor.rowcount > 0:
                            total_saved += 1
                    db.commit()
                    db.close()
            except Exception as e:
                total_errors += 1
                print(json.dumps({
                    "status": "error",
                    "business_id": biz_id,
                    "website": website,
                    "error": str(e),
                }), flush=True)

            # Progress update every 3 businesses
            if (i + 1) % 3 == 0 or (i + 1) == total:
                progress = (
                    f"Postep: {i+1}/{total} biznesow, "
                    f"{total_pages_visited} podstron, "
                    f"znaleziono {total_found} emaili, zapisano {total_saved}"
                )
                log_operation("running", progress, op_id)

            print(json.dumps({
                "status": "progress",
                "current": i + 1,
                "total": total,
                "found": total_found,
                "saved": total_saved,
                "pages_visited": total_pages_visited,
            }), flush=True)

            # Delay between different business websites
            time.sleep(random.uniform(1.0, 3.0))

        summary = (
            f"Przeskanowano {total} biznesow ({total_pages_visited} podstron), "
            f"znaleziono {total_found} emaili, zapisano {total_saved} nowych, bledow: {total_errors}"
        )
        log_operation("done", summary, op_id)
        print(json.dumps({
            "status": "done",
            "scanned": total,
            "pages_visited": total_pages_visited,
            "found": total_found,
            "saved": total_saved,
            "errors": total_errors,
        }), flush=True)

    except Exception as e:
        log_operation("error", str(e), op_id)
        print(json.dumps({"error": str(e)}), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
