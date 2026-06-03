MIN_TOTAL_BUYERS = 10   # сколько активных buy orders суммарно
MIN_TOP_BUYERS = 2      # сколько покупателей на максимальной цене


def is_liquid(orders: list) -> tuple[bool, str]:
    """
    Проверяет ликвидность по данным order-book (market-depth).
    orders — список ордеров из /order-book/v1/market-depth.

    Критерии:
    - Суммарное кол-во активных buy orders >= MIN_TOTAL_BUYERS (10)
    - Кол-во покупателей на топ-цене >= MIN_TOP_BUYERS (2)
    """
    if not orders:
        return False, "нет buy orders"

    total_buyers = int(orders[-1].get("liquidity", 0))
    if total_buyers == 0:
        # fallback: считаем сумму amount вручную
        total_buyers = sum(int(o.get("amount", 0)) for o in orders)

    top_buyers = int(orders[0].get("amount", 0))

    if total_buyers < MIN_TOTAL_BUYERS:
        return False, f"активных покупателей: {total_buyers} (нужно ≥{MIN_TOTAL_BUYERS})"

    if top_buyers < MIN_TOP_BUYERS:
        return False, f"покупателей на топ-цене: {top_buyers} (нужно ≥{MIN_TOP_BUYERS})"

    return True, f"покупателей всего: **{total_buyers}** | на топ-цене: **{top_buyers}**"
