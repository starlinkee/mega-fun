import sqlite3
from config import DATABASE

def init_db():
    conn = sqlite3.connect(DATABASE, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS workspaces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Insert default workspace (Starlinkee) if not exists
    c.execute("INSERT OR IGNORE INTO workspaces (id, name) VALUES (1, 'Starlinkee')")

    c.execute("""
        CREATE TABLE IF NOT EXISTS businesses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            address TEXT,
            city TEXT,
            country TEXT,
            phone TEXT,
            website TEXT,
            category TEXT,
            category_google TEXT,
            source_query TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Osobna tabela, bo jeden biznes moze miec wiele adresow email
    # (np. kontakt@, biuro@, info@ - scrapowane z roznych zrodel)
    c.execute("""
        CREATE TABLE IF NOT EXISTS emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE,
            business_id INTEGER,
            source TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (business_id) REFERENCES businesses(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS mailboxes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            smtp_server TEXT NOT NULL DEFAULT 'smtp.purelymail.com',
            smtp_port INTEGER NOT NULL DEFAULT 587,
            password TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            daily_sent INTEGER DEFAULT 0,
            total_sent INTEGER DEFAULT 0,
            last_sent_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            subject TEXT,
            body_template TEXT,
            status TEXT DEFAULT 'draft',
            target_city TEXT,
            target_country TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
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
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS operations_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_type TEXT NOT NULL,
            status TEXT DEFAULT 'running',
            details TEXT,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS scrape_areas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_query TEXT,
            sw_lat REAL NOT NULL,
            sw_lng REAL NOT NULL,
            ne_lat REAL NOT NULL,
            ne_lng REAL NOT NULL,
            results_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Migrations — add columns if they don't exist yet (safe to re-run)
    for col, table in [
        ("city", "businesses"), ("country", "businesses"),
        ("place_id", "businesses"),
        ("target_city", "campaigns"), ("target_country", "campaigns"),
        ("target_category", "campaigns"),
        ("workspace_id", "businesses"),
        ("workspace_id", "campaigns"),
        ("workspace_id", "mailboxes"),
        ("workspace_id", "scrape_areas"),
    ]:
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists

    # Assign all existing data to workspace 1 (Starlinkee)
    c.execute("UPDATE businesses SET workspace_id = 1 WHERE workspace_id IS NULL")
    c.execute("UPDATE campaigns SET workspace_id = 1 WHERE workspace_id IS NULL")
    c.execute("UPDATE mailboxes SET workspace_id = 1 WHERE workspace_id IS NULL")
    c.execute("UPDATE scrape_areas SET workspace_id = 1 WHERE workspace_id IS NULL")

    try:
        c.execute("ALTER TABLE mailboxes ADD COLUMN total_sent INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    try:
        c.execute("ALTER TABLE mailboxes ADD COLUMN daily_limit INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    try:
        c.execute("ALTER TABLE emails ADD COLUMN is_primary INTEGER DEFAULT 0")
        # Mark the first email per business as primary for existing data
        c.execute("""
            UPDATE emails SET is_primary = 1
            WHERE id IN (
                SELECT MIN(id) FROM emails WHERE business_id IS NOT NULL GROUP BY business_id
            )
        """)
    except sqlite3.OperationalError:
        pass  # column already exists

    try:
        c.execute("ALTER TABLE businesses ADD COLUMN email_scraped_at TIMESTAMP")
    except sqlite3.OperationalError:
        pass

    try:
        c.execute("ALTER TABLE businesses ADD COLUMN email_scraped_website TEXT")
    except sqlite3.OperationalError:
        pass

    try:
        c.execute("ALTER TABLE businesses ADD COLUMN email_scrape_pending INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    try:
        c.execute("ALTER TABLE businesses ADD COLUMN not_interesting INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # Tracking pixel columns
    for col_def in [
        "open_token TEXT",
        "opened_at TIMESTAMP",
        "open_count INTEGER DEFAULT 0",
    ]:
        try:
            c.execute(f"ALTER TABLE campaign_emails ADD COLUMN {col_def}")
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()
    print(f"Database initialized: {DATABASE}")

if __name__ == "__main__":
    init_db()
