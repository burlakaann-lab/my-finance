"""Виконує збережені запити 1-3 з main.beancount і будує report.html (без зовнішніх бібліотек і інтернету)."""

import calendar
import html
from datetime import date
from pathlib import Path

import beanquery
from beancount import loader
from beancount.core.data import Query

ROOT = Path(__file__).parent
LEDGER = ROOT / "main.beancount"
OUTPUT = ROOT / "report.html"

MONTH_NOM = {8: "Серпень", 9: "Вересень", 10: "Жовтень", 11: "Листопад", 12: "Грудень",
             1: "Січень", 2: "Лютий", 3: "Березень", 4: "Квітень", 5: "Травень", 6: "Червень", 7: "Липень"}
MONTH_GEN = {8: "серпня", 9: "вересня", 10: "жовтня", 11: "листопада", 12: "грудня",
             1: "січня", 2: "лютого", 3: "березня", 4: "квітня", 5: "травня", 6: "червня", 7: "липня"}
TARGET_MONTHS = 6  # мета Олі: подушка на пів року життя

Q_CATEGORIES = "Витрати-за-категоріями"
Q_SAVINGS = "Норма-заощаджень"
Q_FIXED = "Фіксовані-і-змінні"


def money(x):
    return f"{x:,.2f}".replace(",", " ").replace(".", ",") + " зл"


def num(x, digits=1):
    return f"{x:.{digits}f}".replace(".", ",")


def category_name(account):
    return account.removeprefix("Expenses:").replace("-", " ")


def run_saved_queries():
    entries, errors, _ = loader.load_file(str(LEDGER))
    if errors:
        raise SystemExit(f"main.beancount має помилки, спершу виправте їх: {errors[0]}")
    sql = {e.name: e.query_string for e in entries if isinstance(e, Query)}
    conn = beanquery.connect(f"beancount:{LEDGER}")
    result = {}
    for name in (Q_CATEGORIES, Q_SAVINGS, Q_FIXED):
        cursor = conn.execute(sql[name])
        columns = [c.name for c in cursor.description]
        result[name] = [dict(zip(columns, row)) for row in cursor.fetchall()]
    return result


def build_months(data):
    months = []
    for row in data[Q_SAVINGS]:
        key = (row["рік"], row["місяць"])
        fixed_row = next(r for r in data[Q_FIXED] if (r["рік"], r["місяць"]) == key)
        categories = sorted(
            ((category_name(r["категорія"]), r["сума"]) for r in data[Q_CATEGORIES]
             if (r["рік"], r["місяць"]) == key),
            key=lambda c: -c[1],
        )
        last_day = calendar.monthrange(*key)[1]
        months.append({
            "year": key[0], "month": key[1],
            "income": row["дохід"], "expenses": row["витрати"], "saved": row["відкладено"],
            "rate": row["відсоток"], "last_date": row["остання_операція"],
            "fixed": fixed_row["фіксовані"], "variable": fixed_row["змінні"],
            "categories": categories,
            "partial": row["остання_операція"] < date(key[0], key[1], last_day),
        })
    return months


def day_label(d):
    return f"{d.day} {MONTH_GEN[d.month]}"


# ---------- графіки (вбудований SVG) ----------

def bars_svg(rows, total):
    label_w, bar_w, row_h = 150, 290, 34
    height = row_h * len(rows)
    biggest = max(v for _, v in rows)
    out = [f'<svg class="chart" viewBox="0 0 640 {height}" role="img" aria-label="Витрати за категоріями">']
    for i, (name, value) in enumerate(rows):
        y = i * row_h
        w = max(2, float(value / biggest) * bar_w)
        out.append(f'<text class="lbl" x="0" y="{y + 23}">{html.escape(name)}</text>')
        out.append(f'<rect class="bar-a" x="{label_w}" y="{y + 7}" width="{w:.1f}" height="20" rx="3"/>')
        out.append(f'<text class="val" x="{label_w + w + 8:.1f}" y="{y + 23}">{money(value)} · {num(float(value / total) * 100, 0)}%</text>')
    out.append("</svg>")
    return "".join(out)


def split_svg(parts, total, aria):
    """Одна смуга з двох частин. parts: [(клас, значення)]."""
    out = [f'<svg class="chart" viewBox="0 0 640 44" role="img" aria-label="{html.escape(aria)}">']
    x = 0.0
    for cls, value in parts:
        w = float(value / total) * 640
        if w > 0:
            out.append(f'<rect class="{cls}" x="{x:.1f}" y="4" width="{w:.1f}" height="36"/>')
        x += w
    out.append("</svg>")
    return "".join(out)


def legend(items):
    return '<ul class="legend">' + "".join(
        f'<li><span class="sw {cls}"></span>{html.escape(label)}: <b>{value}</b></li>' for cls, label, value in items
    ) + "</ul>"


# ---------- блоки ----------

def sentence_categories(m):
    (n1, v1), (n2, v2) = m["categories"][0], m["categories"][1]
    share = num(float(v1 / m["expenses"]) * 100, 0)
    return f"Найбільше йде на {n1.lower()}: {money(v1)} ({share}% витрат), далі {n2.lower()}: {money(v2)}."


def cushion(months, upto_index):
    done = [m for m in months if not m["partial"]] or months
    avg = sum(m["expenses"] for m in done) / len(done)
    saved = sum(m["saved"] for m in months[: upto_index + 1])
    return saved, avg, float(saved / avg)


def month_section(months, i):
    m = months[i]
    mid = f'm-{m["year"]}-{m["month"]:02d}'
    name = MONTH_NOM[m["month"]]
    notice = ""
    if m["partial"]:
        notice = (f'<p class="notice">Місяць неповний: дані до {day_label(m["last_date"])} включно. '
                  f'Цифри нижче ще можуть змінитися.</p>')

    saved_now, avg, months_cov = cushion(months, i)
    savings_note = f"Відкладено {money(m['saved'])} із {money(m['income'])} доходу."
    if m["partial"]:
        savings_note += " Витрат за місяць ще не всі, тож відсоток поки завищений."
    cushion_line = (f"Відкладене з серпня ({money(saved_now)}) покриває {num(months_cov)} міс. витрат "
                    f"(середні витрати повного місяця {money(avg)}), мета — {TARGET_MONTHS}.")

    fixed_share = float(m["fixed"] / m["expenses"]) * 100
    fixed_note = (f"Фіксовані витрати становлять {money(m['fixed'])} ({num(fixed_share, 0)}%), "
                  f"керувати можна {money(m['variable'])}.")
    if m["partial"]:
        fixed_note += " Частина фіксованих платежів ще не списана."

    return f"""
<section class="month" id="{mid}" data-month="{mid}">
  <h2 class="sr">{name} {m["year"]}</h2>
  {notice}
  <article class="card">
    <h3>1. Куди йдуть гроші</h3>
    <p class="big">{money(m["expenses"])}</p>
    <p class="sub">витрачено за {MONTH_GEN[m["month"]]}</p>
    {bars_svg(m["categories"], m["expenses"])}
    <p class="concl">{sentence_categories(m)}</p>
  </article>
  <article class="card">
    <h3>2. Скільки відкладено</h3>
    <p class="big">{num(float(m["rate"]))}%</p>
    <p class="sub">доходу відкладено (норма заощаджень)</p>
    {split_svg([("bar-a", m["expenses"]), ("bar-b", m["saved"])], m["income"], "Дохід: витрати і відкладене")}
    {legend([("bar-a", "Витрати", money(m["expenses"])), ("bar-b", "Відкладено", money(m["saved"]))])}
    <p class="concl">{savings_note}</p>
    <p class="note">{cushion_line}</p>
  </article>
  <article class="card">
    <h3>3. Фіксовані і керовані</h3>
    <p class="big">{num(fixed_share, 0)}%</p>
    <p class="sub">витрат фіксовані (оренда, проїзний, підписки, телефон)</p>
    {split_svg([("bar-a", m["fixed"]), ("bar-b", m["variable"])], m["expenses"], "Фіксовані і керовані витрати")}
    {legend([("bar-a", "Фіксовані", money(m["fixed"])), ("bar-b", "Керовані", money(m["variable"]))])}
    <p class="concl">{fixed_note}</p>
  </article>
</section>"""


CSS = """
:root{--bg:#f6f5f2;--card:#fff;--text:#1d2125;--muted:#5c646c;--line:#e2e0da;--a:#2f6fb0;--b:#d98324;--warn-bg:#fff4dc;--warn-text:#6b4a00;--accent-text:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#16181b;--card:#1f2226;--text:#eceae5;--muted:#a0a7ae;--line:#33383d;--a:#6aa6e0;--b:#e8a14a;--warn-bg:#3a2f14;--warn-text:#f1d9a0;--accent-text:#16181b}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:720px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:1.5rem;margin:0}
.period{color:var(--muted);margin:4px 0 20px}
.tabs{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px}
.tabs button{font:inherit;padding:8px 14px;border-radius:999px;border:1px solid var(--line);background:var(--card);color:var(--text);cursor:pointer}
.tabs button[aria-pressed=true]{background:var(--a);border-color:var(--a);color:var(--accent-text);font-weight:600}
.tabs small{opacity:.85}
.notice{background:var(--warn-bg);color:var(--warn-text);padding:10px 14px;border-radius:8px;margin:0 0 16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 18px 16px;margin-bottom:16px}
h3{font-size:1rem;margin:0 0 8px;color:var(--muted);font-weight:600}
.big{font-size:2.2rem;font-weight:700;margin:0;line-height:1.15}
.sub{color:var(--muted);margin:0 0 12px}
.concl{margin:12px 0 0;font-weight:500}
.note{margin:8px 0 0;color:var(--muted);font-size:.9rem}
.chart{width:100%;height:auto;display:block}
.chart .lbl,.chart .val{fill:var(--text);font-size:17px}
.chart .val{fill:var(--muted)}
.bar-a{fill:var(--a)}.bar-b{fill:var(--b)}
.legend{list-style:none;display:flex;flex-wrap:wrap;gap:6px 18px;padding:0;margin:6px 0 0;font-size:.9rem}
.sw{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.sw.bar-a{background:var(--a)}.sw.bar-b{background:var(--b)}
.sr{position:absolute;left:-9999px}
footer{color:var(--muted);font-size:.85rem;margin-top:24px}
"""

JS = """
(function(){
  var sections=[].slice.call(document.querySelectorAll('.month'));
  var buttons=[].slice.call(document.querySelectorAll('.tabs button'));
  function show(id){
    sections.forEach(function(s){s.hidden=s.dataset.month!==id;});
    buttons.forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.month===id));});
  }
  buttons.forEach(function(b){b.addEventListener('click',function(){show(b.dataset.month);try{history.replaceState(null,'','#'+b.dataset.month);}catch(e){}});});
  var start=location.hash.slice(1);
  if(!sections.some(function(s){return s.dataset.month===start;})){start=document.body.dataset.default;}
  show(start);
})();
"""


def build_html(months):
    first, last = date(months[0]["year"], months[0]["month"], 1), months[-1]["last_date"]
    default_i = max((i for i, m in enumerate(months) if not m["partial"]), default=len(months) - 1)
    tabs = []
    for m in months:
        mid = f'm-{m["year"]}-{m["month"]:02d}'
        label = MONTH_NOM[m["month"]]
        if m["partial"]:
            label += f' <small>· неповний, до {m["last_date"].day:02d}.{m["last_date"].month:02d}</small>'
        tabs.append(f'<button type="button" data-month="{mid}" aria-pressed="false">{label}</button>')
    default_id = f'm-{months[default_i]["year"]}-{months[default_i]["month"]:02d}'
    sections = "".join(month_section(months, i) for i in range(len(months)))
    return f"""<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Фінанси Олі</title>
<style>{CSS}</style>
</head>
<body data-default="{default_id}">
<main>
  <h1>Фінанси Олі</h1>
  <p class="period">Дані з {day_label(first)} до {day_label(last)} {last.year}. Усі суми в злотих.</p>
  <nav class="tabs" aria-label="Місяць">{''.join(tabs)}</nav>
  {sections}
  <footer>Звіт зібраний скриптом report.py зі збережених запитів у main.beancount.</footer>
</main>
<script>{JS}</script>
</body>
</html>
"""


def main():
    months = build_months(run_saved_queries())
    OUTPUT.write_text(build_html(months), encoding="utf-8")
    print(f"Записано {OUTPUT.name}: {len(months)} місяців")


if __name__ == "__main__":
    main()
