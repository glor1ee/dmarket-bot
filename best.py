import time
from collections import Counter
from datetime import datetime, timezone, timedelta
from dmarket import get_recommended_skins, get_market_depth, get_last_sales_raw, get_min_offer

KYIV = timezone(timedelta(hours=3))


def main():
    seen: set[str] = set()

    while True:
        try:
            skins = get_recommended_skins()
        except Exception as e:
            print(f"Ошибка: {e}")
            time.sleep(5)
            continue

        print(f"Загружено {len(skins)} скинов, проверяю...")
        for item in skins:
            title = item.get("title", "N/A")
            price = int(item["price"].get("USD", 0)) / 100

            if price > 100:
                continue

            if title in seen:
                continue

            seen.add(title)

            orders = get_market_depth(title)
            if not orders:
                continue

            max_target = int(orders[0]["price"]) / 100
            if max_target == 0:
                continue

            net = price * 0.93 - max_target

            if net < 1 or net > 10:
                continue

            min_offer = get_min_offer(title)
            print(f"Min offer: {min_offer}")

            net = min_offer * 0.93 - max_target
            if net < 1 or net > 10:
                continue

            sales = get_last_sales_raw(title, limit=10)
            if not sales:
                continue

            day_counts = Counter()
            for s in sales:
                day = datetime.fromtimestamp(int(s.get("date", 0)), tz=KYIV).strftime("%d %b %Y")
                day_counts[day] += 1

            liquid = any(count > 3 for count in day_counts.values())
            if not liquid:
                continue

            offer_prices = [float(s["price"]) for s in sales if s.get("txOperationType") == "Offer" and s.get("price")]
            target_prices = [float(s["price"]) for s in sales if s.get("txOperationType") == "Target" and s.get("price")]
            avg_offer = sum(offer_prices) / len(offer_prices) if offer_prices else None
            if avg_offer is None:
                continue

            profit = avg_offer * 0.93 - max_target
            emoji = "🟢" if profit > 0 else "🔴"

            print(
                f"{emoji} {title}\n"
                f"   ОФФЕР: ${min_offer:.2f} | ТАРГЕТ: ${max_target:.2f} | "
                f"Прибыль: ${net:.2f}"
            )

            for s in sales:
                ts = int(s.get("date", 0))
                dt = datetime.fromtimestamp(ts, tz=KYIV)
                date_str = dt.strftime("%d %b %Y %H:%M")
                print(f"   {s.get('txOperationType', '?'):<7} ${s.get('price', '?'):<9} {date_str}")

            if offer_prices:
                print(f"   Макс оффер:  ${max(offer_prices):.2f}")
                print(f"   Сред оффер:  ${avg_offer:.2f}")
            if target_prices:
                print(f"   Макс таргет: ${max(target_prices):.2f}")
            avg_per_day = sum(day_counts.values()) / len(day_counts)
            print(f"   Прибыль (сред−7%−таргет): ${profit:.2f}")
            print(f"   Сред продаж в день: {avg_per_day:.1f}")
            print(f"   Макс продаж за день: {max(day_counts.values())}")
            print()


if __name__ == "__main__":
    main()
