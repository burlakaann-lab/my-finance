"""Де лежать transactions.beancount і cash.beancount.

Локально це звичайні файли поруч із кодом. На Vercel диск тимчасовий, тому обидва файли
зберігаємо в безкоштовному Redis (Upstash, Vercel Marketplace) і перед кожним запитом
кладемо в тимчасову папку. Працюємо через urllib зі стандартної бібліотеки, нових пакетів не потрібно.
"""

import json
import os
import shutil
import tempfile
import urllib.request
from pathlib import Path

URL = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL") or ""
TOKEN = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN") or ""
REMOTE = bool(URL and TOKEN)

KEYS = {"transactions.beancount": "finance:transactions", "cash.beancount": "finance:cash"}
WORKDIR = Path(tempfile.gettempdir()) / "ledger"


def command(*args):
    request = urllib.request.Request(
        URL, data=json.dumps(list(args)).encode(), method="POST",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        body = json.load(response)
    if "error" in body:
        raise RuntimeError(body["error"])
    return body["result"]


def prepare(root):
    """Копіює main.beancount у тимчасову папку, де лежатимуть дані з бази."""
    WORKDIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(root) / "main.beancount", WORKDIR / "main.beancount")


def pull(cash_header):
    """Кладе актуальні файли з бази у тимчасову папку."""
    for name, key in KEYS.items():
        text = command("GET", key)
        if text is None:
            text = cash_header if name == "cash.beancount" else ""
        path = WORKDIR / name
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")


def push(name, text):
    command("SET", KEYS[name], text)
