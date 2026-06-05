from datetime import datetime, timezone, timedelta

KYIV = timezone(timedelta(hours=3))
from dmarket import get_last_sales_raw

SKINS = [
    "USP-S | Neo-Noir (Factory New)",
    "USP-S | Neo-Noir (Minimal Wear)",
    "M4A4 | Neo-Noir (Factory New)",
    "AWP | Neo-Noir (Factory New)",
    "AWP | Neo-Noir (Minimal Wear)",
    "AWP | Neo-Noir (Field-Tested)",
]

for title in SKINS:
    print(f"\n=== {title} ===")
    sales = get_last_sales_raw(title, limit=20)
    if not sales:
        print("  нет данных")
        continue
    from collections import Counter
    day_counts = Counter()
    for s in sales:
        ts = int(s.get("date", 0))
        dt = datetime.fromtimestamp(ts, tz=KYIV)
        date_str = dt.strftime("%d %b %Y %H:%M")
        day_counts[dt.strftime("%d %b %Y")] += 1
        print(f"  {s.get('txOperationType', '?'):<7} ${s.get('price', '?'):<8} {date_str}")

    liquid = any(count > 4 for count in day_counts.values())
    print(f"  Итог: {'✅ Ликвидный' if liquid else '❌ Не ликвидный'} (макс продаж за день: {max(day_counts.values())})")
