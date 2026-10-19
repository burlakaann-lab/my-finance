#!/bin/sh
# Запускає трекер на 127.0.0.1:8000. Паролі беремо з файлу .env (у git його немає).
cd "$(dirname "$0")" || exit 1
[ -f .env ] || { echo "Немає файлу .env. Скопіюйте .env.example у .env і заповніть."; exit 1; }
set -a
. ./.env
set +a
exec uv run python app.py
