"""Reset daily_sent counter on all mailboxes. Run once at midnight via cron."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
from config import DATABASE


def main():
    conn = sqlite3.connect(DATABASE, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("UPDATE mailboxes SET daily_sent = 0")
    conn.commit()
    conn.close()
    print("Reset daily_sent for all mailboxes")


if __name__ == "__main__":
    main()
