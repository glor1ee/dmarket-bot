import time
from dmarket import get_recommended_skins, get_market_depth, get_last_sales_raw


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

            orders = get_market_depth(title)
            if not orders:
                continue

            max_target = int(orders[0]["price"]) / 100
            if max_target == 0:
                continue

            net = price * 0.93 - max_target

            if net < 1 or net > 10:
                continue

            seen.add(title)
            diff = price - max_target
            emoji = "🟢" if diff < 2 else "🔴"
            print(
                f"{emoji} {title}\n"
                f"   ОФФЕР: ${price:.2f} | ТАРГЕТ: ${max_target:.2f} | "
                f"Прибыль: ${net:.2f}"
            )

            sales = get_last_sales_raw(title, limit=10)
            if sales:
                for s in sales:
                    print(f"   {s.get('txOperationType', '?'):<7} ${s.get('price', '?')}")
                offer_prices = [float(s["price"]) for s in sales if s.get("txOperationType") == "Offer" and s.get("price")]
                target_prices = [float(s["price"]) for s in sales if s.get("txOperationType") == "Target" and s.get("price")]
                avg_offer = sum(offer_prices) / len(offer_prices) if offer_prices else None
                max_target_hist = max(target_prices) if target_prices else None
                if avg_offer is None or max_target_hist is None:
                    continue
                if avg_offer * 0.93 - max_target_hist <= 0:
                    continue
                if offer_prices:
                    print(f"   Макс оффер:  ${max(offer_prices):.2f}")
                    print(f"   Сред оффер:  ${avg_offer:.2f}")
                if target_prices:
                    print(f"   Макс таргет: ${max_target_hist:.2f}")
                if avg_offer is not None and max_target_hist is not None:
                    profit = avg_offer * 0.93 - max_target_hist
                    print(f"   Прибыль (сред−7%−таргет): ${profit:.2f}")
            print()


if __name__ == "__main__":
    main()
