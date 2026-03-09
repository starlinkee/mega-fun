import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "mega_fun.db")
SECRET_KEY = os.urandom(24)
