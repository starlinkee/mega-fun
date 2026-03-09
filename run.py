from app import create_app
from init_db import init_db

init_db()
app = create_app()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
