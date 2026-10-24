"""Заливає локальні transactions.beancount і cash.beancount у базу (Upstash Redis), яку читає Vercel.

Запуск після кожного імпорту: ./push_data.sh
"""

import sys
from pathlib import Path

import store

ROOT = Path(__file__).parent

if not store.REMOTE:
    sys.exit("Немає KV_REST_API_URL і KV_REST_API_TOKEN у .env. Див. .env.example")

for name in store.KEYS:
    path = ROOT / name
    if not path.exists():
        print(f"Пропускаю {name}: файлу немає")
        continue
    text = path.read_text(encoding="utf-8")
    store.push(name, text)
    print(f"Залито {name}: {len(text.encode())} байт")
