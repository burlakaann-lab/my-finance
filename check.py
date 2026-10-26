"""Одна команда перевірки: bean-check і звірка з контрольними цифрами Олі.

Запуск: uv run python check.py
Готівку з cash.beancount не враховуємо: контрольні цифри Олі рахувалися без неї.
"""

import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from beancount import loader
from beancount.core.data import Transaction

import report

ROOT = Path(__file__).parent

# (рік, місяць): дохід, витрати, відкладено, норма %, фіксовані, змінні
EXPECTED = {
    (2026, 8): ("9800.00", "5703.70", "4096.30", "41.8", "3508.49", "2195.21"),
    (2026, 9): ("9800.00", "6090.91", "3709.09", "37.8", "3508.49", "2582.42"),
    (2026, 10): ("9800.00", "4978.05", "4821.95", "49.2", "3463.49", "1514.56"),
}
OPERATIONS = 142

print("1. bean-check main.beancount")
result = subprocess.run(["uv", "run", "bean-check", "main.beancount"], cwd=ROOT, capture_output=True, text=True)
print(result.stdout + result.stderr, end="")
ok = result.returncode == 0 and not (result.stdout + result.stderr).strip()
print("   мовчить, ок (разом із трьома звірками залишку з банком)" if ok else "   ПОМИЛКА")

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    (tmp / "main.beancount").write_text((ROOT / "main.beancount").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp / "transactions.beancount").write_text((ROOT / "transactions.beancount").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp / "cash.beancount").write_text("", encoding="utf-8")
    report.LEDGER = tmp / "main.beancount"
    months = report.build_months(report.run_saved_queries())
    entries, _, _ = loader.load_file(str(tmp / "transactions.beancount"))
    operations = sum(isinstance(e, Transaction) for e in entries)

print("\n2. Контрольні цифри Олі (зл, без готівки)")
print(f"   {'Місяць':<16}{'Дохід':>10}{'Витрати':>10}{'Відкладено':>12}{'Норма':>8}{'Фікс.':>10}{'Змінні':>10}")
for m in months:
    got = (m["income"], m["expenses"], m["saved"], Decimal(str(m["rate"])).quantize(Decimal("0.1")), m["fixed"], m["variable"])
    want = tuple(Decimal(x) for x in EXPECTED[(m["year"], m["month"])])
    good = all(abs(g - w) < Decimal("0.005") for g, w in zip(got, want)) and len(got) == len(want)
    ok = ok and good
    label = f"{m['year']}-{m['month']:02d}" + (", до 15.10" if m["partial"] else "")
    print(f"   {label:<16}{got[0]:>10,.2f}{got[1]:>10,.2f}{got[2]:>12,.2f}{got[3]:>7}%{got[4]:>10,.2f}{got[5]:>10,.2f}  {'збігається' if good else 'НЕ ЗБІГАЄТЬСЯ, мало бути ' + str(want)}")
if len(months) != len(EXPECTED):
    ok = False
    print("   Кількість місяців не збігається")

print(f"\n3. Операцій у виписці: {operations} (мало бути {OPERATIONS})")
ok = ok and operations == OPERATIONS

print("\nУСЕ ЗБІГАЄТЬСЯ" if ok else "\nЄ РОЗБІЖНОСТІ")
sys.exit(0 if ok else 1)
