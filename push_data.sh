#!/bin/sh
# Заливає transactions.beancount і cash.beancount у базу. Ключі беремо з .env (у git його немає).
cd "$(dirname "$0")" || exit 1
[ -f .env ] || { echo "Немає файлу .env."; exit 1; }
set -a
. ./.env
set +a
exec uv run python push_data.py
