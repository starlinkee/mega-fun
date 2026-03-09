import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "mega_fun.db")
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "change-me")
