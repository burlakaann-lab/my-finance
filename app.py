"""Вебзастосунок для телефона: вхід, звіт за місяць і додавання готівки.

Запуск: ./start.sh. Паролі й ключ беремо зі змінних середовища (див. .env.example).
Звіт будує report.py, готівку застосунок дописує в cash.beancount і одразу перевіряє облік.
"""

import hashlib
import hmac
import html
import os
import re
import secrets
import sys
import tempfile
import threading
import time
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps
from pathlib import Path

from beancount import loader
from beancount.core.data import Open
from flask import Flask, abort, make_response, redirect, render_template_string, request, send_file, session
from markupsafe import Markup
from werkzeug.middleware.proxy_fix import ProxyFix

import report
import store

ROOT = Path(__file__).parent
LEDGER = ROOT / "main.beancount"
CASH = ROOT / "cash.beancount"
if store.REMOTE:  # на Vercel дані лежать у базі, а працюємо з копією в тимчасовій папці
    store.prepare(ROOT)
    LEDGER = store.WORKDIR / "main.beancount"
    CASH = store.WORKDIR / "cash.beancount"
    report.LEDGER = LEDGER
CASH_HEADER = "; Готівкові витрати, додані з телефона через app.py. У git не потрапляє.\n"
CASH_ACCOUNT = "Assets:Готівка"
START_DATE = date(2026, 8, 1)  # виписка починається з 1 серпня
UNCATEGORIZED = "Expenses:Без-Категорії"
ICON_SIZES = {"180", "192", "512"}

MAX_FAILS, FAIL_WINDOW = 5, 600  # 5 невдалих входів за 10 хвилин блокують IP
LOCK = threading.Lock()
FAILS = {}


def require_env(name, min_len):
    value = os.environ.get(name, "")
    if len(value) < min_len:
        raise SystemExit(f"Змінна середовища {name} не задана або закоротка (треба мінімум {min_len} символів). "
                         "Див. .env.example")
    return value


USERS = {"Оля": require_env("OLYA_PASSWORD", 12), "Марко": require_env("MARKO_PASSWORD", 12)}

SYNC_TOKEN = require_env("SYNC_TOKEN", 32) if store.REMOTE else ""
SYNC_LIMIT = 2 * 1024 * 1024

app = Flask(__name__, static_folder=None)
app.secret_key = require_env("TRACKER_SECRET_KEY", 32)
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=14),
    MAX_CONTENT_LENGTH=8 * 1024,
)
# Сервер слухає лише 127.0.0.1, зовні його відкриває тунель, який і додає ці заголовки.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

LAYOUT = """<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }}</title>
<meta name="theme-color" content="#1f2937">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/icon-180.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Фінанси">
<style>
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:6px 4px;border-bottom:1px solid #8884}
:root { color-scheme: light dark; }
body { font: 16px/1.5 system-ui, sans-serif; margin: 0; padding: 16px; max-width: 28rem; margin-inline: auto; }
h1 { font-size: 1.4rem; margin: 0 0 1rem; }
label { display: block; margin: 1rem 0 .25rem; font-weight: 600; }
input, select, button { font: inherit; padding: .7rem; width: 100%; box-sizing: border-box; border-radius: 8px;
  border: 1px solid #8888; }
button { background: #0f766e; color: #fff; border: 0; font-weight: 600; margin-top: 1.25rem; }
.radios label { display: inline-block; font-weight: 400; margin-right: 1rem; }
.radios input { width: auto; margin-right: .3rem; }
.err { color: #b91c1c; font-weight: 600; }
.ok { color: #0f766e; font-weight: 600; }
a { color: #0f766e; }
</style>
</head>
<body>
{{ body }}
</body>
</html>
"""

APPBAR_CSS = """<style>
.appbar { position: sticky; top: 0; z-index: 10; display: flex; gap: 12px; align-items: center; padding: 8px 12px;
  background: #1f2937; color: #fff; font: 15px system-ui, sans-serif; }
.appbar span { flex: 1; }
.appbar a { color: #5eead4; font-weight: 600; text-decoration: none; }
.appbar form { margin: 0; }
.appbar button { background: none; border: 1px solid #fff6; color: #fff; border-radius: 6px; padding: 4px 10px;
  font: inherit; }
</style>
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/icon-180.png">
<meta name="theme-color" content="#1f2937">
<meta name="apple-mobile-web-app-capable" content="yes">
"""


def page(title, template, status=200, **context):
    body = Markup(render_template_string(template, **context))
    return render_template_string(LAYOUT, title=title, body=body), status


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def check_csrf():
    sent = request.form.get("csrf", "")
    if not hmac.compare_digest(sent.encode(), session.get("csrf", "").encode()):
        abort(400)


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if session.get("user") not in USERS:
            return redirect("/login")
        return view(*args, **kwargs)
    return wrapper


def is_blocked(ip):
    record = FAILS.get(ip)
    if record and time.time() - record[1] > FAIL_WINDOW:
        del FAILS[ip]
        return False
    return bool(record) and record[0] >= MAX_FAILS


def note_fail(ip):
    record = FAILS.setdefault(ip, [0, time.time()])
    record[0] += 1


def log(message):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}", file=sys.stderr, flush=True)


@app.before_request
def pull_data():
    if store.REMOTE and not request.path.startswith("/icon-") and request.path not in ("/manifest.webmanifest", "/api/sync"):
        store.pull(CASH_HEADER)


@app.before_request
def force_https():
    if request.is_secure or request.host.split(":")[0] in ("localhost", "127.0.0.1"):
        return None
    return redirect(request.url.replace("http://", "https://", 1), code=301)


@app.after_request
def security_headers(response):
    response.headers["Strict-Transport-Security"] = "max-age=31536000"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self'; "
        "manifest-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
    if request.path not in ("/manifest.webmanifest",) and not request.path.startswith("/icon-"):
        response.headers["Cache-Control"] = "no-store"
    return response


LOGIN_FORM = """
<h1>Фінанси</h1>
{% if error %}<p class="err">{{ error }}</p>{% endif %}
<form method="post" action="/login">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <div class="radios">
    {% for name in users %}<label><input type="radio" name="user" value="{{ name }}" {{ 'checked' if loop.first }}>{{ name }}</label>{% endfor %}
  </div>
  <label for="password">Пароль</label>
  <input id="password" type="password" name="password" autocomplete="current-password" required>
  <button type="submit">Увійти</button>
</form>
"""


@app.get("/login")
def login_form():
    return page("Вхід", LOGIN_FORM, csrf=csrf_token(), users=list(USERS), error=None)


@app.post("/login")
def login():
    check_csrf()
    ip = request.remote_addr or "?"
    if is_blocked(ip):
        log(f"вхід заблоковано: {ip}")
        return page("Вхід", LOGIN_FORM, 429, csrf=csrf_token(), users=list(USERS),
                    error="Забагато спроб. Спробуйте через 10 хвилин.")
    user = request.form.get("user", "")
    password = request.form.get("password", "")
    expected = USERS.get(user, secrets.token_urlsafe(16))
    if hmac.compare_digest(password.encode(), expected.encode()) and user in USERS:
        FAILS.pop(ip, None)
        session.clear()
        session["user"] = user
        session.permanent = True
        csrf_token()
        log(f"вхід: {user} з {ip}")
        return redirect("/")
    note_fail(ip)
    log(f"невдалий вхід з {ip}")
    time.sleep(0.5)
    return page("Вхід", LOGIN_FORM, 401, csrf=csrf_token(), users=list(USERS), error="Невірне імʼя або пароль.")


@app.post("/logout")
@login_required
def logout():
    check_csrf()
    session.clear()
    return redirect("/login")


def appbar(user):
    return (f'<div class="appbar"><span>{html.escape(user)}</span><a href="/add">+ Готівка</a><a href="/cash">Готівка: список</a>'
            f'<form method="post" action="/logout"><input type="hidden" name="csrf" value="{csrf_token()}">'
            '<button type="submit">Вийти</button></form></div>')


@app.get("/")
@login_required
def home():
    try:
        months = report.build_months(report.run_saved_queries())
    except SystemExit as exc:
        log(f"помилка обліку: {exc}")
        return page("Помилка", "<h1>Облік має помилку</h1><p>Запустіть bean-check і виправте її.</p>", 500)
    doc = report.build_html(months)
    doc = doc.replace("</head>", APPBAR_CSS + "</head>", 1).replace("<main>", appbar(session["user"]) + "<main>", 1)
    return doc


def expense_accounts():
    entries, _, _ = loader.load_file(str(LEDGER))
    return sorted(e.account for e in entries
                  if isinstance(e, Open) and e.account.startswith("Expenses:") and e.account != UNCATEGORIZED)


ADD_FORM = """
<p><a href="/">← До звіту</a></p>
<h1>{{ heading }}</h1>
{% if error %}<p class="err">{{ error }}</p>{% endif %}
<form method="post" action="{{ action }}">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <label for="amount">Сума, зл</label>
  <input id="amount" name="amount" inputmode="decimal" autocomplete="off" required placeholder="45,00" value="{{ form.amount }}">
  <label for="category">Категорія</label>
  <select id="category" name="category" required>
    {% for account, name in categories %}<option value="{{ account }}" {{ 'selected' if account == form.category }}>{{ name }}</option>{% endfor %}
  </select>
  <label for="description">Що це (необовʼязково)</label>
  <input id="description" name="description" maxlength="60" autocomplete="off" value="{{ form.description }}">
  <label for="day">Дата</label>
  <input id="day" type="date" name="day" min="{{ start }}" max="{{ today }}" value="{{ form.day }}" required>
  <button type="submit">{{ button }}</button>
</form>
{% if delete_action %}
<form method="post" action="{{ delete_action }}" onsubmit="return confirm('Видалити цю витрату?')">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <button type="submit" style="background:#b91c1c">Видалити</button>
</form>
{% endif %}
"""

CASH_LIST = """
<p><a href="/">← До звіту</a> · <a href="/add">+ Додати</a></p>
<h1>Готівкові витрати</h1>
{% if rows %}
<p>Разом: <b>{{ total }} зл</b> ({{ rows|length }})</p>
<table>
  <tr><th>Дата</th><th>Що</th><th>Категорія</th><th>Хто</th><th>Сума</th><th></th></tr>
  {% for r in rows %}<tr><td>{{ r.day }}</td><td>{{ r.description }}</td><td>{{ r.category }}</td><td>{{ r.user }}</td><td>{{ r.amount }}</td><td><a href="/cash/{{ r.id }}/edit">Змінити</a></td></tr>{% endfor %}
</table>
{% else %}<p>Поки що немає жодної готівкової витрати.</p>{% endif %}
"""

ADDED = """
<h1>Додано</h1>
<p class="ok">{{ amount }} зл, {{ category }}, {{ day }}</p>
<p><a href="/">До звіту</a> · <a href="/add">Додати ще</a></p>
"""


def add_page(error=None, form=None, status=200, heading="Додати готівку", action="/add", button="Додати", delete_action=None):
    accounts = expense_accounts()
    form = form or {"amount": "", "category": accounts[0], "description": "", "day": date.today().isoformat()}
    return page(heading, ADD_FORM, status, csrf=csrf_token(), error=error, form=form,
                heading=heading, action=action, button=button, delete_action=delete_action,
                categories=[(a, report.category_name(a)) for a in accounts],
                start=START_DATE.isoformat(), today=date.today().isoformat())


def parse_cash_form(form, accounts):
    """Повертає (дані, помилка). Усе, що йде у файл обліку, проходить сувору перевірку."""
    raw_amount = form.get("amount", "").replace(" ", "").replace(",", ".")
    if not re.fullmatch(r"\d{1,6}(\.\d{1,2})?", raw_amount):
        return None, "Сума має бути числом, наприклад 45 або 45,50."
    amount = Decimal(raw_amount)
    if amount <= 0:
        return None, "Сума має бути більшою за нуль."
    category = form.get("category", "")
    if category not in accounts:
        return None, "Оберіть категорію зі списку."
    try:
        day = date.fromisoformat(form.get("day", ""))
    except ValueError:
        return None, "Вкажіть дату."
    if not START_DATE <= day <= date.today():
        return None, "Дата має бути від 1 серпня 2026 до сьогодні."
    description = re.sub(r'[\x00-\x1f"\\]', " ", form.get("description", "")).strip()[:60] or "Готівка"
    return {"amount": amount.quantize(Decimal("0.01")), "category": category, "day": day,
            "description": description}, None


def format_entry(data, user):
    amount = f"{data['amount']:.2f}"
    return (f'{data["day"].isoformat()} * "{data["description"]}"\n'
            f'  added-by: "{user}"\n'
            f'  {data["category"]}  {amount} PLN\n'
            f'  {CASH_ACCOUNT}  -{amount} PLN\n')


def write_atomic(text):
    tmp = CASH.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, CASH)


def append_cash(entry):
    """Дописує операцію і перевіряє облік так само, як bean-check. Якщо є помилка, повертає файл як був."""
    with LOCK:
        old = CASH.read_text(encoding="utf-8") if CASH.exists() else CASH_HEADER
        write_atomic(old.rstrip("\n") + "\n\n" + entry)
        commit(old)


def commit(old):
    """Перевіряє облік, а тоді зберігає файл готівки в базі. Якщо щось не так, повертає файл як був."""
    _, errors, _ = loader.load_file(str(LEDGER))
    if errors:
        write_atomic(old)
        raise ValueError(str(errors[0]))
    if store.REMOTE:
        try:
            store.push("cash.beancount", CASH.read_text(encoding="utf-8"))
        except Exception as exc:
            write_atomic(old)
            raise ValueError(f"база не прийняла запис: {exc}")


@app.get("/add")
@login_required
def add_form():
    return add_page()


@app.post("/add")
@login_required
def add_submit():
    check_csrf()
    data, error = parse_cash_form(request.form, expense_accounts())
    if error:
        return add_page(error, {"amount": request.form.get("amount", ""), "category": request.form.get("category", ""),
                                "description": request.form.get("description", ""),
                                "day": request.form.get("day", "")}, 400)
    try:
        append_cash(format_entry(data, session["user"]))
    except ValueError as exc:
        log(f"готівку не записано, облік має помилку: {exc}")
        return add_page("Не вдалося записати: облік не пройшов перевірку. Нічого не змінено.", None, 500)
    log(f"готівка: {session['user']} {data['amount']} {data['category']}")
    return page("Додано", ADDED, amount=f"{data['amount']:.2f}".replace(".", ","),
                category=report.category_name(data["category"]), day=data["day"].isoformat())


def cash_blocks():
    """Файл готівки як (шапка, список записів). Кожен запис — текст одного блоку."""
    text = CASH.read_text(encoding="utf-8") if CASH.exists() else CASH_HEADER
    parts = [b.strip("\n") for b in re.split(r"\n\s*\n", text) if b.strip()]
    header = [b for b in parts if not re.match(r"\d{4}-\d\d-\d\d ", b)]
    return header, [b for b in parts if re.match(r"\d{4}-\d\d-\d\d ", b)]


def block_id(block):
    return hashlib.sha1(block.encode()).hexdigest()[:10]


def parse_block(block):
    m = re.match(r'(\d{4}-\d\d-\d\d) \* "(.*)"\n\s+added-by: "(.*)"\n\s+(Expenses:\S+)\s+(\d+\.\d\d) PLN', block)
    if not m:
        return None
    return {"day": date.fromisoformat(m[1]), "description": m[2], "user": m[3], "category": m[4],
            "amount": Decimal(m[5])}


def save_cash(header, blocks):
    """Записує файл і перевіряє облік. Якщо є помилка, повертає файл як був."""
    with LOCK:
        old = CASH.read_text(encoding="utf-8") if CASH.exists() else CASH_HEADER
        write_atomic("\n\n".join(header + blocks).rstrip("\n") + "\n")
        commit(old)


def find_block(bid):
    header, blocks = cash_blocks()
    for i, b in enumerate(blocks):
        if block_id(b) == bid:
            return header, blocks, i
    abort(404)


@app.get("/cash")
@login_required
def cash_list():
    rows, total = [], Decimal(0)
    for b in cash_blocks()[1]:
        d = parse_block(b)
        if not d:
            continue
        total += d["amount"]
        rows.append({"id": block_id(b), "day": d["day"].strftime("%d.%m.%Y"), "description": d["description"],
                     "category": report.category_name(d["category"]), "user": d["user"],
                     "amount": f"{d['amount']:.2f}".replace(".", ","), "d": d["day"]})
    rows.sort(key=lambda r: r["d"], reverse=True)
    return page("Готівкові витрати", CASH_LIST, rows=rows, total=f"{total:.2f}".replace(".", ","))


def edit_page(bid, error=None, form=None, status=200):
    _, blocks, i = find_block(bid)
    d = parse_block(blocks[i])
    form = form or {"amount": f"{d['amount']:.2f}".replace(".", ","), "category": d["category"],
                    "description": "" if d["description"] == "Готівка" else d["description"],
                    "day": d["day"].isoformat()}
    return add_page(error, form, status, heading="Змінити готівку", action=f"/cash/{bid}/edit",
                    button="Зберегти", delete_action=f"/cash/{bid}/delete")


@app.get("/cash/<bid>/edit")
@login_required
def cash_edit_form(bid):
    return edit_page(bid)


@app.post("/cash/<bid>/edit")
@login_required
def cash_edit(bid):
    check_csrf()
    header, blocks, i = find_block(bid)
    old = parse_block(blocks[i])
    data, error = parse_cash_form(request.form, expense_accounts())
    if error:
        return edit_page(bid, error, {k: request.form.get(k, "") for k in ("amount", "category", "description", "day")}, 400)
    blocks[i] = format_entry(data, old["user"]).strip("\n")
    try:
        save_cash(header, blocks)
    except ValueError as exc:
        log(f"зміну не записано, облік має помилку: {exc}")
        return edit_page(bid, "Не вдалося записати: облік не пройшов перевірку. Нічого не змінено.", None, 500)
    log(f"готівка змінена: {session['user']} {data['amount']} {data['category']}")
    return redirect("/cash")


@app.post("/cash/<bid>/delete")
@login_required
def cash_delete(bid):
    check_csrf()
    header, blocks, i = find_block(bid)
    removed = blocks.pop(i)
    try:
        save_cash(header, blocks)
    except ValueError as exc:
        log(f"видалення не записано, облік має помилку: {exc}")
        abort(500)
    log(f"готівка видалена: {session['user']} {removed.splitlines()[0]}")
    return redirect("/cash")


@app.post("/api/sync")
def sync():
    """Приймає transactions.beancount від push_data.sh. Доступ лише за токеном, у базу йде тільки перевірений облік."""
    if not store.REMOTE:
        abort(404)
    ip = request.remote_addr or "?"
    if is_blocked(ip):
        abort(429)
    sent = request.headers.get("Authorization", "")
    if not hmac.compare_digest(sent.encode(), f"Bearer {SYNC_TOKEN}".encode()):
        note_fail(ip)
        log(f"sync: невірний токен з {ip}")
        time.sleep(0.5)
        abort(401)
    request.max_content_length = SYNC_LIMIT
    try:
        text = request.get_data().decode("utf-8")
    except UnicodeDecodeError:
        return "Файл не в UTF-8\n", 400
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "main.beancount").write_text((ROOT / "main.beancount").read_text(encoding="utf-8"), encoding="utf-8")
        current = store.fetch(CASH_HEADER)
        (tmp / "transactions.beancount").write_text(text, encoding="utf-8")
        (tmp / "cash.beancount").write_text(current["cash.beancount"], encoding="utf-8")
        _, errors, _ = loader.load_file(str(tmp / "main.beancount"))
    if errors:
        log(f"sync відхилено, облік має помилку: {errors[0]}")
        return f"Облік не пройшов перевірку, нічого не збережено: {errors[0]}\n", 422
    store.push("transactions.beancount", text)
    log(f"sync: transactions.beancount оновлено, {len(text.encode())} байт")
    return f"Збережено transactions.beancount: {len(text.encode())} байт\n"


@app.get("/manifest.webmanifest")
def manifest():
    response = make_response({
        "name": "Фінанси", "short_name": "Фінанси", "start_url": "/", "display": "standalone",
        "background_color": "#1f2937", "theme_color": "#1f2937",
        "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"}],
    })
    response.headers["Content-Type"] = "application/manifest+json"
    return response


@app.get("/icon-<size>.png")
def icon(size):
    if size not in ICON_SIZES:
        abort(404)
    return send_file(ROOT / "static" / f"icon-{size}.png", mimetype="image/png", max_age=86400)


def main():
    from cheroot.wsgi import Server

    port = int(os.environ.get("PORT", "8000"))
    server = Server(("127.0.0.1", port), app)
    print(f"Трекер слухає http://127.0.0.1:{port}. Назовні його відкриває тунель (HTTPS).", flush=True)
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
