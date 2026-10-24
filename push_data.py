"""Відправляє локальний transactions.beancount на бекенд (Vercel), який перевіряє його і кладе в базу.

Пароля до бази тут немає: бекенд приймає файл лише за токеном SYNC_TOKEN.
cash.beancount не заливаємо: готівку веде застосунок, і її джерело правди в базі.
Запуск після кожного імпорту: ./push_data.sh
"""

import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
site = os.environ.get("SITE_URL", "").rstrip("/")
token = os.environ.get("SYNC_TOKEN", "")
if not site.startswith("https://") or not token:
    sys.exit("Потрібні SITE_URL (https://…) і SYNC_TOKEN у .env. Див. .env.example")

path = ROOT / "transactions.beancount"
if not path.exists():
    sys.exit("Немає transactions.beancount. Спершу запустіть import.py")

request = urllib.request.Request(site + "/api/sync", data=path.read_bytes(), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "text/plain; charset=utf-8"})
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        print(response.read().decode(), end="")
except urllib.error.HTTPError as exc:
    sys.exit(f"Сервер відмовив ({exc.code}): {exc.read().decode(errors='replace')}")
