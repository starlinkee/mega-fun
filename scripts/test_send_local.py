"""
Test lokalny wysyłki kampanii na własne adresy Gmail (+alias).

Co robi:
  1. Tworzy osobną bazę test_mega_fun.db (nie dotyka produkcji)
  2. Usuwa i wstawia na nowo 10 testowych firm z emailami vikbobinski+one..+ten@gmail.com
  3. Kopiuje mailboxy z produkcyjnej bazy (lub możesz podać własne poniżej)
  4. Tworzy kampanię testową i uruchamia logikę send_campaign w pętli
  5. Na końcu drukuje raport

Użycie:
    python scripts/test_send_local.py
"""

import sys
import os
import sqlite3
import smtplib
import time
import random
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DATABASE
from app.crypto import decrypt, encrypt

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DB = os.path.join(BASE_DIR, "test_mega_fun.db")

# ── Testowe emaile (Gmail + aliasy) ──────────────────────────────────────────
TEST_EMAILS = [
    ("vikbobinski+one@gmail.com",   "Test Firma One",   "Warszawa"),
    ("vikbobinski+two@gmail.com",   "Test Firma Two",   "Kraków"),
    ("vikbobinski+three@gmail.com", "Test Firma Three", "Gdańsk"),
    ("vikbobinski+four@gmail.com",  "Test Firma Four",  "Wrocław"),
    ("vikbobinski+five@gmail.com",  "Test Firma Five",  "Poznań"),
    ("vikbobinski+six@gmail.com",   "Test Firma Six",   "Łódź"),
    ("vikbobinski+seven@gmail.com", "Test Firma Seven", "Szczecin"),
    ("vikbobinski+eight@gmail.com", "Test Firma Eight", "Lublin"),
    ("vikbobinski+nine@gmail.com",  "Test Firma Nine",  "Katowice"),
    ("vikbobinski+ten@gmail.com",   "Test Firma Ten",   "Białystok"),
]

TEST_CAMPAIGN_SUBJECT = "[TEST] Kampania testowa - proszę zignorować"
TEST_CAMPAIGN_BODY = """\
Cześć,

To jest testowa wiadomość kampanii.
Proszę zignorować — testowanie lokalnego środowiska.

Pozdrawiam,
Mega Fun Test
"""


# ── Helpers DB ───────────────────────────────────────────────────────────────

def get_test_db():
    conn = sqlite3.connect(TEST_DB, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    return conn


def init_test_schema(conn):
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS businesses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT, address TEXT, city TEXT, country TEXT,
            phone TEXT, website TEXT, category TEXT,
            category_google TEXT, source_query TEXT, place_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE,
            business_id INTEGER,
            source TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (business_id) REFERENCES businesses(id)
        );
        CREATE TABLE IF NOT EXISTS mailboxes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            smtp_server TEXT NOT NULL DEFAULT 'smtp.purelymail.com',
            smtp_port INTEGER NOT NULL DEFAULT 587,
            password TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            daily_sent INTEGER DEFAULT 0,
            daily_limit INTEGER DEFAULT 0,
            total_sent INTEGER DEFAULT 0,
            last_sent_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            subject TEXT,
            body_template TEXT,
            status TEXT DEFAULT 'draft',
            target_city TEXT, target_country TEXT, target_category TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS campaign_emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            email_id INTEGER NOT NULL,
            mailbox_id INTEGER,
            status TEXT DEFAULT 'pending',
            sent_at TIMESTAMP,
            error TEXT,
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id),
            FOREIGN KEY (email_id) REFERENCES emails(id),
            FOREIGN KEY (mailbox_id) REFERENCES mailboxes(id)
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    conn.commit()


def seed_test_data(conn):
    c = conn.cursor()

    # Usuń stare dane testowe
    c.execute("DELETE FROM campaign_emails WHERE campaign_id IN (SELECT id FROM campaigns WHERE name LIKE '[TEST]%')")
    c.execute("DELETE FROM campaigns WHERE name LIKE '[TEST]%'")
    c.execute("DELETE FROM emails WHERE source = 'test_local'")
    c.execute("DELETE FROM businesses WHERE source_query = 'test_local'")
    conn.commit()
    print("Usunięto stare dane testowe z test DB.")

    # Wstaw 10 firm + emaile
    for email, name, city in TEST_EMAILS:
        c.execute(
            "INSERT INTO businesses (name, city, country, source_query) VALUES (?, ?, 'PL', 'test_local')",
            (name, city),
        )
        biz_id = c.lastrowid
        c.execute(
            "INSERT OR IGNORE INTO emails (email, business_id, source) VALUES (?, ?, 'test_local')",
            (email, biz_id),
        )

    conn.commit()
    print(f"Wstawiono {len(TEST_EMAILS)} testowych firm/emaili.")


def copy_mailboxes_from_prod(test_conn):
    """Kopiuje mailboxy z produkcyjnej bazy do testowej."""
    if not os.path.exists(DATABASE):
        print("Produkcyjna baza nie istnieje — pomiń kopiowanie mailboxów.")
        return 0

    prod = sqlite3.connect(DATABASE)
    prod.row_factory = sqlite3.Row
    mailboxes = prod.execute("SELECT * FROM mailboxes WHERE active = 1").fetchall()
    prod.close()

    if not mailboxes:
        print("Brak aktywnych mailboxów w produkcyjnej bazie.")
        return 0

    c = test_conn.cursor()
    c.execute("DELETE FROM mailboxes")
    for mb in mailboxes:
        c.execute(
            """INSERT INTO mailboxes
               (email, smtp_server, smtp_port, password, active, daily_sent, daily_limit, total_sent)
               VALUES (?, ?, ?, ?, 1, 0, ?, 0)""",
            (mb["email"], mb["smtp_server"], mb["smtp_port"],
             mb["password"], mb["daily_limit"] if mb["daily_limit"] else 0),
        )
    test_conn.commit()
    print(f"Skopiowano {len(mailboxes)} mailbox(ów) z produkcji.")
    return len(mailboxes)


def create_test_campaign(conn):
    email_ids = [row[0] for row in conn.execute(
        "SELECT id FROM emails WHERE source = 'test_local'"
    ).fetchall()]

    c = conn.cursor()
    c.execute(
        """INSERT INTO campaigns (name, subject, body_template, status)
           VALUES ('[TEST] Kampania lokalna', ?, ?, 'active')""",
        (TEST_CAMPAIGN_SUBJECT, TEST_CAMPAIGN_BODY),
    )
    campaign_id = c.lastrowid

    for eid in email_ids:
        c.execute(
            "INSERT INTO campaign_emails (campaign_id, email_id, status) VALUES (?, ?, 'pending')",
            (campaign_id, eid),
        )
    conn.commit()
    print(f"Kampania testowa ID={campaign_id} utworzona ({len(email_ids)} odbiorców).")
    return campaign_id


# ── Logika wysyłki (identyczna z send_campaign.py, ale na test DB) ────────────

def send_email(smtp_server, smtp_port, sender_email, sender_password, to_email, subject, body):
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = sender_email
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))
        with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, to_email, msg.as_string())
        return True, None
    except Exception as e:
        return False, str(e)


def run_send_round(db, campaign_id, dry_run=False):
    mailboxes = db.execute("SELECT * FROM mailboxes WHERE active = 1").fetchall()
    if not mailboxes:
        print("Brak mailboxów! Dodaj mailbox w aplikacji i uruchom ponownie.")
        return 0, 0

    sent = 0
    failed = 0

    for mb in mailboxes:
        pending = db.execute(
            """SELECT ce.id AS ce_id, e.email AS recipient
               FROM campaign_emails ce
               JOIN emails e ON ce.email_id = e.id
               WHERE ce.campaign_id = ? AND ce.status = 'pending'
               LIMIT 1""",
            (campaign_id,),
        ).fetchone()

        if not pending:
            break

        ce_id = pending["ce_id"]
        recipient = pending["recipient"]

        db.execute(
            "UPDATE campaign_emails SET status = 'sending', mailbox_id = ? WHERE id = ?",
            (mb["id"], ce_id),
        )
        db.commit()

        if dry_run:
            print(f"  [DRY RUN] {mb['email']} → {recipient}")
            db.execute(
                "UPDATE campaign_emails SET status = 'sent', sent_at = CURRENT_TIMESTAMP WHERE id = ?",
                (ce_id,),
            )
            db.execute(
                "UPDATE mailboxes SET daily_sent = daily_sent + 1, total_sent = total_sent + 1 WHERE id = ?",
                (mb["id"],),
            )
            db.commit()
            sent += 1
            continue

        success, error = send_email(
            mb["smtp_server"], mb["smtp_port"],
            mb["email"], decrypt(mb["password"]),
            recipient, subject=TEST_CAMPAIGN_SUBJECT, body=TEST_CAMPAIGN_BODY,
        )

        if success:
            db.execute(
                "UPDATE campaign_emails SET status = 'sent', sent_at = CURRENT_TIMESTAMP WHERE id = ?",
                (ce_id,),
            )
            db.execute(
                "UPDATE mailboxes SET daily_sent = daily_sent + 1, total_sent = total_sent + 1, last_sent_at = CURRENT_TIMESTAMP WHERE id = ?",
                (mb["id"],),
            )
            sent += 1
            print(f"  ✓ Wysłano: {mb['email']} → {recipient}")
        else:
            db.execute(
                "UPDATE campaign_emails SET status = 'failed', error = ? WHERE id = ?",
                (error, ce_id),
            )
            failed += 1
            print(f"  ✗ Błąd: {mb['email']} → {recipient}: {error}")

        db.commit()
        if not dry_run:
            time.sleep(random.uniform(1, 2))

    return sent, failed


def print_report(db, campaign_id):
    rows = db.execute(
        """SELECT e.email, ce.status, ce.sent_at, ce.error
           FROM campaign_emails ce
           JOIN emails e ON ce.email_id = e.id
           WHERE ce.campaign_id = ?
           ORDER BY ce.id""",
        (campaign_id,),
    ).fetchall()

    print("\n" + "=" * 55)
    print(f"RAPORT KAMPANII (ID={campaign_id})")
    print("=" * 55)
    counts = {"sent": 0, "failed": 0, "pending": 0, "sending": 0}
    for r in rows:
        status = r["status"]
        counts[status] = counts.get(status, 0) + 1
        icon = {"sent": "✓", "failed": "✗", "pending": "○", "sending": "→"}.get(status, "?")
        extra = f" ({r['error']})" if r["error"] else (f" @ {r['sent_at']}" if r["sent_at"] else "")
        print(f"  {icon} [{status:8}] {r['email']}{extra}")
    print("-" * 55)
    for k, v in counts.items():
        if v:
            print(f"  {k}: {v}")
    print("=" * 55)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("=== TRYB DRY RUN (bez faktycznego wysyłania) ===\n")

    # 1. Inicjuj testową bazę
    db = get_test_db()
    init_test_schema(db)

    # 2. Seed danych testowych
    seed_test_data(db)

    # 3. Kopiuj mailboxy z produkcji
    mb_count = copy_mailboxes_from_prod(db)
    if mb_count == 0 and not dry_run:
        print("\nBrak mailboxów. Uruchom z --dry-run żeby przetestować bez SMTP:")
        print("  python scripts/test_send_local.py --dry-run")
        db.close()
        return

    # 4. Utwórz kampanię testową
    campaign_id = create_test_campaign(db)

    # 5. Wysyłaj rundy aż do wyczerpania
    print(f"\nRozpoczynanie wysyłki {'(DRY RUN) ' if dry_run else ''}...")
    total_sent = 0
    total_failed = 0
    round_num = 0

    while True:
        remaining = db.execute(
            "SELECT COUNT(*) FROM campaign_emails WHERE campaign_id = ? AND status = 'pending'",
            (campaign_id,),
        ).fetchone()[0]

        if remaining == 0:
            break

        round_num += 1
        print(f"\nRunda {round_num} (pozostało: {remaining}):")
        s, f = run_send_round(db, campaign_id, dry_run=dry_run)
        total_sent += s
        total_failed += f

        if s == 0 and f == 0:
            print("Brak postępu — przerywam.")
            break

    # 6. Oznacz kampanię jako done
    db.execute("UPDATE campaigns SET status = 'done' WHERE id = ?", (campaign_id,))
    db.commit()

    # 7. Raport
    print_report(db, campaign_id)
    print(f"\nTest DB: {TEST_DB}")
    db.close()


if __name__ == "__main__":
    main()
