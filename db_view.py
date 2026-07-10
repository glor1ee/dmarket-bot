"""Просмотр содержимого bot.db из консоли.

Запуск:
    python db_view.py            # сводка + последние 10 записей каждой таблицы
    python db_view.py 25         # то же, но последние 25 записей
"""
import sqlite3
import sys
from datetime import datetime

from db import _PATH

sys.stdout.reconfigure(encoding="utf-8")
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 10


def _dt(ts) -> str:
    return datetime.fromtimestamp(int(ts)).strftime("%d.%m.%Y %H:%M") if ts else "—"


conn = sqlite3.connect(_PATH)
conn.row_factory = sqlite3.Row

print(f"База: {_PATH}\n")

n, total = conn.execute("SELECT COUNT(*), COALESCE(SUM(price), 0) FROM closed_targets").fetchone()
print(f"🛒 closed_targets (покупки): {n} шт. на ${total:.2f}")
for r in conn.execute("SELECT * FROM closed_targets ORDER BY closed_at DESC LIMIT ?", (LIMIT,)):
    print(f"   {_dt(r['closed_at'])}  ${r['price']:>8.2f}  {r['title']}")

n, total = conn.execute("SELECT COUNT(*), COALESCE(SUM(price - fee_amount), 0) FROM closed_offers").fetchone()
print(f"\n💸 closed_offers (продажи): {n} шт., чистыми ${total:.2f}")
for r in conn.execute("SELECT * FROM closed_offers ORDER BY closed_at DESC LIMIT ?", (LIMIT,)):
    buy = f"покупка ${r['buy_price']:.2f}" if r["buy_price"] is not None else "покупка неизвестна"
    print(f"   {_dt(r['closed_at'])}  ${r['price']:>8.2f} − fee ${r['fee_amount']:.2f}  [{r['status']}]  {buy}  {r['title']}")

n = conn.execute("SELECT COUNT(*) FROM stats_reports").fetchone()[0]
print(f"\n📈 stats_reports (снапшоты отчётов): {n} шт.")
for r in conn.execute("SELECT id, created_at, period_days FROM stats_reports ORDER BY id DESC LIMIT ?", (LIMIT,)):
    print(f"   #{r['id']}  {_dt(r['created_at'])}  период {r['period_days']} дн.")

conn.close()
