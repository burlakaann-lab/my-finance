"""Де лежать transactions.beancount і cash.beancount.

Локально це звичайні файли поруч із кодом. На Vercel диск тимчасовий, тому обидва файли
зберігаємо в безкоштовному Neon Postgres (Vercel Marketplace) і перед кожним запитом
кладемо в тимчасову папку. Працюємо через urllib зі стандартної бібліотеки, нових пакетів не потрібно.
"""

import json
import os
import shutil
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or ""
REMOTE = bool(DATABASE_URL)
# Neon приймає SQL по HTTPS на тому ж хості, що й база. NEON_SQL_URL потрібен лише для тестів.
ENDPOINT = os.environ.get("NEON_SQL_URL") or (f"https://{urlsplit(DATABASE_URL).hostname}/sql" if REMOTE else "")

NAMES = ("transactions.beancount", "cash.beancount")
WORKDIR = Path(tempfile.gettempdir()) / "ledger"
_table_ready = False


def sql(query, params=()):
    request = urllib.request.Request(
        ENDPOINT, data=json.dumps({"query": query, "params": list(params)}).encode(), method="POST",
        headers={"Neon-Connection-String": DATABASE_URL, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)["rows"]


def ensure_table():
    global _table_ready
    if not _table_ready:
        sql("CREATE TABLE IF NOT EXISTS ledger_files (name text PRIMARY KEY, body text NOT NULL)")
        _table_ready = True


def prepare(root):
    """Копіює main.beancount у тимчасову папку, де лежатимуть дані з бази."""
    WORKDIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(root) / "main.beancount", WORKDIR / "main.beancount")


def fetch(cash_header):
    """Повертає актуальний вміст обох файлів з бази."""
    ensure_table()
    stored = {row["name"]: row["body"] for row in sql("SELECT name, body FROM ledger_files")}
    return {name: stored.get(name, cash_header if name == "cash.beancount" else "") for name in NAMES}


def pull(cash_header):
    """Кладе актуальні файли з бази у тимчасову папку."""
    for name, text in fetch(cash_header).items():
        path = WORKDIR / name
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")


def push(name, text):
    ensure_table()
    sql("INSERT INTO ledger_files (name, body) VALUES ($1, $2) "
        "ON CONFLICT (name) DO UPDATE SET body = EXCLUDED.body", [name, text])
