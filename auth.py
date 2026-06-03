import time
import hmac
import hashlib
import os
from dotenv import load_dotenv

load_dotenv()

PUBLIC_KEY = os.getenv("DMARKET_PUBLIC_KEY")
SECRET_KEY = os.getenv("DMARKET_SECRET_KEY")

BASE_URL = "https://api.dmarket.com"
GAME_ID = "a8db"


def generate_headers(method: str, path: str, body: str = "") -> dict:
    timestamp = str(int(time.time()))
    string_to_sign = method + path + body + timestamp
    signature = hmac.new(
        SECRET_KEY.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Api-Key": PUBLIC_KEY,
        "X-Request-Sign": "dmarket " + signature,
        "X-Sign-Date": timestamp,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
    }
