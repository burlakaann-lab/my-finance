"""Одна команда перевірки: bean-check і звірка з контрольними цифрами Олі (цифри Марка лише друкуємо).

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
OPERATIONS = {"olya": 142, "marko": 109}

print("1. bean-check main.beancount")
result = subprocess.run(["uv", "run", "bean-check", "main.beancount"], cwd=ROOT, capture_output=True, text=True)
print(result.stdout + result.stderr, end="")
ok = result.returncode == 0 and not (result.stdout + result.stderr).strip()
print("   мовчить, ок (разом зі звірками залишку з банком Олі й Марка)" if ok else "   ПОМИЛКА")

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    (tmp / "main.beancount").write_text((ROOT / "main.beancount").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp / "transactions.beancount").write_text((ROOT / "transactions.beancount").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp / "cash.beancount").write_text("", encoding="utf-8")
    report.LEDGER = tmp / "main.beancount"
    views = report.build_views()
    entries, _, _ = loader.load_file(str(tmp / "transactions.beancount"))
    operations = {"marko": sum(isinstance(e, Transaction) and "marko" in e.tags for e in entries)}
    operations["olya"] = sum(isinstance(e, Transaction) for e in entries) - operations["marko"]


def row_text(m, got):
    label = f"{m['year']}-{m['month']:02d}" + (", до 15.10" if m["partial"] else "")
    rate = f"{got[3]}%" if got[3] is not None else "—"
    return f"   {label:<18}{got[0]:>10,.2f}{got[1]:>10,.2f}{got[2]:>12,.2f}{rate:>8}{got[4]:>10,.2f}{got[5]:>10,.2f}"


def figures(m):
    rate = Decimal(str(m["rate"])).quantize(Decimal("0.1")) if m["rate"] is not None else None
    return (m["income"], m["expenses"], m["saved"], rate, m["fixed"], m["variable"])


header = f"   {'Місяць':<18}{'Дохід':>10}{'Витрати':>10}{'Відкладено':>12}{'Норма':>8}{'Фікс.':>10}{'Змінні':>10}"
print("\n2. Контрольні цифри Олі (зл, без готівки)")
print(header)
for m in views["olya"]:
    got = figures(m)
    want = tuple(Decimal(x) for x in EXPECTED[(m["year"], m["month"])])
    good = all(abs(g - w) < Decimal("0.005") for g, w in zip(got, want))
    ok = ok and good
    print(row_text(m, got) + ("  збігається" if good else f"  НЕ ЗБІГАЄТЬСЯ, мало бути {want}"))
if len(views["olya"]) != len(EXPECTED):
    ok = False
    print("   Кількість місяців не збігається")

print("\n3. Марко (зл, без готівки): контрольних цифр немає, лише цифри з обліку")
print(header)
for m in views["marko"]:
    print(row_text(m, figures(m)))

print("\n4. Оля + Марко = Разом")
for key in ("income", "expenses", "fixed", "variable"):
    for o, k, j in zip(views["olya"], views["marko"], views["joint"]):
        if o[key] + k[key] != j[key]:
            ok = False
            print(f"   РОЗБІЖНІСТЬ: {key} {j['year']}-{j['month']:02d}")
print("   сходиться" if ok else "   є розбіжності вище")

print("\n5. Операцій у виписках")
for who, want in OPERATIONS.items():
    print(f"   {who}: {operations[who]} (мало бути {want})")
    ok = ok and operations[who] == want

print("\nУСЕ ЗБІГАЄТЬСЯ" if ok else "\nЄ РОЗБІЖНОСТІ")
sys.exit(0 if ok else 1)
