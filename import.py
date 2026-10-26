"""Читає всі CSV з data/ і правила з rules.csv та повністю перезаписує transactions.beancount.

Два формати виписок: англомовна виписка Олі (data/statement.csv) і польська виписка Марка
(починається з рядка «Historia operacji», рахунок Assets:Банк-Марко, операції з тегом #marko).
"""

import csv
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
RULES_FILE = ROOT / "rules.csv"
OUTPUT_FILE = ROOT / "transactions.beancount"

BANK = "Assets:Банк"
BANK_MARKO = "Assets:Банк-Марко"
OTHER_INCOME = "Income:Інше"
UNCATEGORIZED = "Expenses:Без-Категорії"


def load_rules():
    with open(RULES_FILE, encoding="utf-8", newline="") as f:
        return [
            (row["шаблон"].lower(), row["рахунок"], row["фіксована"].strip().lower() == "так")
            for row in csv.DictReader(f)
        ]


def read_olya(path):
    """Виписка Олі. Рядки в порядку банку (від старих до нових)."""
    rows = []
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["State"] != "COMPLETED":
                continue
            if Decimal(row["Fee"]) != 0:
                raise ValueError(f"Комісія не підтримується: {row}")
            rows.append({"day": date.fromisoformat(row["Completed Date"][:10]), "description": row["Description"],
                         "amount": Decimal(row["Amount"]), "balance": Decimal(row["Balance"]),
                         "account": BANK, "owner_tag": ""})
    return rows


def pl_number(text):
    return Decimal(re.sub(r"\s", "", text).replace(",", "."))


def read_marko(path):
    """Виписка Марка: роздільник «;», числа з комою, найновіші операції зверху.

    Беремо дату księgowania. Порядок банку читаємо знизу вгору, а колонку залишку звіряємо
    з початковим залишком із шапки: якщо хоч один рядок не сходиться, імпорт зупиняється.
    """
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    opening, first = None, None
    for i, line in enumerate(lines):
        if line.startswith("Saldo początkowe;"):
            opening = pl_number(line.split(";")[1])
        if line.startswith("Data operacji;"):
            first = i + 1
            break
    if opening is None or first is None:
        raise ValueError(f"{path.name}: не знайшов початковий залишок або шапку таблиці")
    rows = []
    for fields in csv.reader(lines[first:], delimiter=";"):
        if not fields:
            continue
        rows.append({"day": datetime.strptime(fields[1], "%d.%m.%Y").date(), "description": fields[3],
                     "amount": pl_number(fields[4]), "balance": pl_number(fields[5]),
                     "account": BANK_MARKO, "owner_tag": " #marko"})
    rows.reverse()
    running = opening
    for row in rows:
        running += row["amount"]
        if running != row["balance"]:
            raise ValueError(f"{path.name}: залишок не сходиться на {row['day']} {row['description']}: "
                             f"маю {running}, у виписці {row['balance']}")
    return rows


def load_rows():
    """Рядки кожної виписки в порядку банку. Однакові рядки з різних виписок рахуємо один раз."""
    rows, seen = [], set()
    for path in sorted(DATA_DIR.glob("*.csv")):
        first_line = path.read_text(encoding="utf-8-sig").splitlines()[0]
        batch = read_marko(path) if first_line.startswith("Historia operacji") else read_olya(path)
        for row in batch:
            key = (row["account"], row["day"], row["description"], row["amount"], row["balance"])
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    return rows


def categorize(description, amount, rules):
    """Повертає (рахунок, фіксована). Перемагає перше правило, що підійшло."""
    text = description.lower()
    for pattern, account, fixed in rules:
        if pattern in text:
            return account, fixed
    return (OTHER_INCOME if amount > 0 else UNCATEGORIZED), False


def month_start(d):
    return d.replace(day=1)


def next_month_start(d):
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1)


def main():
    rules = load_rules()
    rows = load_rows()
    lines = [
        "; Файл генерує import.py з data/*.csv і rules.csv. Руками не правити:",
        "; він перезаписується при кожному імпорті.",
        "",
    ]

    last_balance = {}  # (рахунок, місяць) -> (останній залишок за порядком банку, остання дата)
    for row in rows:
        day, amount = row["day"], row["amount"]
        account, fixed = categorize(row["description"], amount, rules)
        narration = row["description"].replace("\\", "\\\\").replace('"', '\\"')
        tag = (" #fixed" if fixed else "") + row["owner_tag"]
        lines += [
            f'{day} * "{narration}"{tag}',
            f"  {row['account']}  {amount} PLN",
            f"  {account}  {-amount} PLN",
            "",
        ]
        last_balance[(row["account"], month_start(day))] = (row["balance"], day)

    for bank in sorted({a for a, _ in last_balance}):
        months = sorted(m for a, m in last_balance if a == bank)
        for month in months:
            balance, last_day = last_balance[(bank, month)]
            # Для останнього місяця звіряємо на день після останньої операції,
            # для решти — на перше число наступного місяця.
            check_day = last_day + timedelta(days=1) if month == months[-1] else next_month_start(month)
            lines.append(f"{check_day} balance {bank}  {balance} PLN")

    OUTPUT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Записано {len(rows)} операцій і {len(last_balance)} balance-тверджень у {OUTPUT_FILE.name}")


if __name__ == "__main__":
    main()
