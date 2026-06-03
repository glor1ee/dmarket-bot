import time
from dmarket import get_recommended_skins, get_market_depth


def main():
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

            orders = get_market_depth(title)
            if not orders:
                continue

            max_target = int(orders[0]["price"]) / 100
            if max_target == 0:
                continue

            net = price * 0.93 - max_target

            if net < 1 or net > 10:
                continue

            diff = price - max_target
            emoji = "🟢" if diff < 2 else "🔴"
            print(
                f"{emoji} {title}\n"
                f"   ОФФЕР: ${price:.2f} | ТАРГЕТ: ${max_target:.2f} | "
                f"Прибыль: ${net:.2f}\n"
            )


if __name__ == "__main__":
    main()
