"""
Синхронизирует закрытые (купленные) таргеты с DMarket в cost_basis.json.
Запуск вручную: python sync_closed.py
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

from dmarket import get_closed_targets
from store import sync_closed_targets, get_buy_price

trades = get_closed_targets()
print(f"Закрытых таргетов получено: {len(trades)}")

count = sync_closed_targets(trades)
print(f"Записано в cost_basis.json: {count}\n")

for t in sorted(trades, key=lambda x: int(x.get("ClosedAt", 0)), reverse=True):
    title = t.get("Title", "N/A")
    price = float(t.get("Price", {}).get("Amount", 0))
    status = t.get("Status", "")
    saved = get_buy_price(title)
    print(f"  ${price:>7.2f}  {title}  [{status}]  -> JSON ${saved}")
