"""Читає всі CSV з data/ і правила з rules.csv та повністю перезаписує transactions.beancount."""

import csv
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
RULES_FILE = ROOT / "rules.csv"
OUTPUT_FILE = ROOT / "transactions.beancount"

BANK = "Assets:Банк"
OTHER_INCOME = "Income:Інше"
UNCATEGORIZED = "Expenses:Без-Категорії"


def load_rules():
    with open(RULES_FILE, encoding="utf-8", newline="") as f:
        return [
            (row["шаблон"].lower(), row["рахунок"], row["фіксована"].strip().lower() == "так")
            for row in csv.DictReader(f)
        ]


def load_rows():
    """Рядки в порядку файлу (порядок банку). Однакові рядки з різних виписок рахуємо один раз."""
    rows, seen = [], set()
    for path in sorted(DATA_DIR.glob("*.csv")):
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row["State"] != "COMPLETED":
                    continue
                key = (row["Completed Date"], row["Description"], row["Amount"], row["Balance"])
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

    last_balance = {}  # місяць -> (останній залишок за порядком банку, остання дата)
    for row in rows:
        if Decimal(row["Fee"]) != 0:
            raise ValueError(f"Комісія не підтримується: {row}")
        day = date.fromisoformat(row["Completed Date"][:10])
        amount = Decimal(row["Amount"])
        account, fixed = categorize(row["Description"], amount, rules)
        narration = row["Description"].replace("\\", "\\\\").replace('"', '\\"')
        tag = " #fixed" if fixed else ""
        lines += [
            f'{day} * "{narration}"{tag}',
            f"  {BANK}  {amount} PLN",
            f"  {account}  {-amount} PLN",
            "",
        ]
        last_balance[month_start(day)] = (row["Balance"], day)

    months = sorted(last_balance)
    for month in months:
        balance, last_day = last_balance[month]
        # Для останнього місяця звіряємо на день після останньої операції,
        # для решти — на перше число наступного місяця.
        check_day = last_day + timedelta(days=1) if month == months[-1] else next_month_start(month)
        lines.append(f"{check_day} balance {BANK}  {balance} PLN")

    OUTPUT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Записано {len(rows)} операцій і {len(months)} balance-тверджень у {OUTPUT_FILE.name}")


if __name__ == "__main__":
    main()
