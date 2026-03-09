"""
Campaign sender — designed to be run by cron every minute.
Usage: python scripts/send_campaign.py

Each invocation:
1. Finds the current active campaign (oldest first)
2. Sends one email per active mailbox
3. If campaign is done, marks it 'done' and promotes next queued campaign to 'active'
4. Exits

Cron entry example:  * * * * *  cd /path/to/mega-fun && python scripts/send_campaign.py
"""

import sys
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
from config import DATABASE
from app.crypto import decrypt


def get_db():
    conn = sqlite3.connect(DATABASE, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    return conn


def send_email(smtp_server, smtp_port, sender_email, sender_password, to_email, subject, body):
    """Send a single email via SMTP. Returns (success, error)."""
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


def promote_next_queued(db):
    """Promote the oldest queued campaign to active."""
    queued = db.execute(
        "SELECT id FROM campaigns WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
    ).fetchone()
    if queued:
        db.execute("UPDATE campaigns SET status = 'active' WHERE id = ?", (queued["id"],))
        db.commit()
        print(f"Promoted campaign {queued['id']} to active")


def main():
    db = get_db()

    # Find the active campaign (oldest first)
    campaign = db.execute(
        "SELECT * FROM campaigns WHERE status = 'active' ORDER BY created_at ASC LIMIT 1"
    ).fetchone()

    if not campaign:
        db.close()
        return  # Nothing to do

    campaign_id = campaign["id"]
    subject = campaign["subject"]
    body = campaign["body_template"]

    # Get all active mailboxes
    mailboxes = db.execute("SELECT * FROM mailboxes WHERE active = 1").fetchall()

    if not mailboxes:
        db.close()
        print("No active mailboxes")
        return

    sent_count = 0
    for mb in mailboxes:
        # Pick one pending email for this campaign
        pending = db.execute(
            """SELECT ce.id AS ce_id, e.email AS recipient
               FROM campaign_emails ce
               JOIN emails e ON ce.email_id = e.id
               WHERE ce.campaign_id = ? AND ce.status = 'pending'
               LIMIT 1""",
            (campaign_id,),
        ).fetchone()

        if not pending:
            # No more pending — campaign is done
            db.execute("UPDATE campaigns SET status = 'done' WHERE id = ?", (campaign_id,))
            db.commit()
            promote_next_queued(db)
            db.close()
            print(f"Campaign {campaign_id} done")
            return

        ce_id = pending["ce_id"]
        recipient = pending["recipient"]

        # Mark as sending to avoid double-pick
        db.execute(
            "UPDATE campaign_emails SET status = 'sending', mailbox_id = ? WHERE id = ?",
            (mb["id"], ce_id),
        )
        db.commit()

        success, error = send_email(
            mb["smtp_server"], mb["smtp_port"],
            mb["email"], decrypt(mb["password"]),
            recipient, subject, body,
        )

        if success:
            db.execute(
                "UPDATE campaign_emails SET status = 'sent', sent_at = CURRENT_TIMESTAMP WHERE id = ?",
                (ce_id,),
            )
            db.execute(
                "UPDATE mailboxes SET daily_sent = daily_sent + 1, last_sent_at = CURRENT_TIMESTAMP WHERE id = ?",
                (mb["id"],),
            )
            sent_count += 1
            print(f"Sent to {recipient} from {mb['email']}")
        else:
            db.execute(
                "UPDATE campaign_emails SET status = 'failed', error = ? WHERE id = ?",
                (error, ce_id),
            )
            print(f"Failed {recipient} from {mb['email']}: {error}")

        db.commit()

    # Check if there are still pending emails after this round
    remaining = db.execute(
        "SELECT COUNT(*) FROM campaign_emails WHERE campaign_id = ? AND status = 'pending'",
        (campaign_id,),
    ).fetchone()[0]

    if remaining == 0:
        db.execute("UPDATE campaigns SET status = 'done' WHERE id = ?", (campaign_id,))
        db.commit()
        promote_next_queued(db)
        print(f"Campaign {campaign_id} done")

    db.close()
    print(f"Round complete: sent {sent_count} emails, {remaining} remaining")


if __name__ == "__main__":
    main()
