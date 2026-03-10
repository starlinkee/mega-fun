from flask import Flask
from config import SECRET_KEY

def create_app():
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.secret_key = SECRET_KEY

    # Migrations: add columns that may not exist in older DBs
    from app.db import get_db
    db = get_db()
    try:
        db.execute("ALTER TABLE operations_log ADD COLUMN params TEXT")
        db.commit()
    except Exception:
        pass  # column already exists
    try:
        db.execute("ALTER TABLE emails ADD COLUMN is_primary INTEGER DEFAULT 0")
        # Mark the first email per business as primary for existing data
        db.execute("""
            UPDATE emails SET is_primary = 1
            WHERE id IN (
                SELECT MIN(id) FROM emails WHERE business_id IS NOT NULL GROUP BY business_id
            ) AND is_primary = 0
        """)
        db.commit()
    except Exception:
        pass  # column already exists
    db.close()

    from app.routes import main_bp
    app.register_blueprint(main_bp)

    return app
