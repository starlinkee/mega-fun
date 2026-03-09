from flask import Flask
from config import SECRET_KEY

def create_app():
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.secret_key = SECRET_KEY

    from app.routes import main_bp
    app.register_blueprint(main_bp)

    return app
