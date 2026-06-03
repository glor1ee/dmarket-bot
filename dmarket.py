import time as _time
import random
import requests
from auth import BASE_URL, GAME_ID, generate_headers
from liquidity import is_liquid

SORT_OPTIONS = [
    ("personal", "desc"),
    ("price", "asc"),
    ("price", "desc"),
    ("updated", "desc"),
]


def get_recommended_skins() -> list:
    order_by, order_dir = random.choice(SORT_OPTIONS)
    path = "/exchange/v1/market/items"
    params = (
        f"?side=market&orderBy={order_by}&orderDir={order_dir}"
        f"&title=&priceFrom=0&priceTo=0"
        f"&treeFilters=&gameId={GAME_ID}&types=dmarket&myFavorites=false"
        f"&cursor=&limit=100&currency=USD&platform=browser&isLoggedIn=true"
    )
    headers = generate_headers("GET", path + params)
    response = requests.get(BASE_URL + path + params, headers=headers, timeout=10)
    if not response.text:
        raise ValueError(f"Пустой ответ от API (статус {response.status_code})")
    try:
        skins = response.json().get("objects", [])
    except Exception:
        raise ValueError(f"Не JSON (статус {response.status_code}): {response.text[:200]}")
    random.shuffle(skins)
    return skins


def get_market_depth(exact_title: str) -> list:
    url = f"{BASE_URL}/order-book/v1/market-depth"
    params = {
        "title": exact_title,
        "gameId": GAME_ID,
        "filters": "paintSeed[]=any,floatPartValue[]=any",
    }
    try:
        r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        return r.json().get("orders", [])
    except Exception:
        return []


def get_last_sales(exact_title: str) -> tuple[int, int]:
    """Возвращает (total_sales_24h, target_sales_24h)."""
    url = f"{BASE_URL}/trade-aggregator/v1/last-sales"
    params = {"title": exact_title, "gameId": GAME_ID, "limit": 100}
    try:
        r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if r.status_code != 200:
            return 0, 0
        sales = r.json().get("sales", [])
        cutoff = int(_time.time()) - 86400
        recent = [s for s in sales if int(s.get("date", 0)) >= cutoff]
        target_sales = sum(1 for s in recent if s.get("txOperationType") == "Target")
        return len(recent), target_sales
    except Exception:
        return 0, 0


def format_lot_output(item: dict) -> str | None:
    title = item.get("title", "N/A")
    price = int(item["price"].get("USD", 0)) / 100

    if price > 100:
        return None

    orders = get_market_depth(title)
    if not orders:
        return None

    max_target = int(orders[0]["price"]) / 100
    if max_target == 0:
        return None

    offer_minus_fee = price * 0.93
    net = offer_minus_fee - max_target

    if net < 1 or net > 10:
        return None

    liquid, liq_info = is_liquid(orders)
    if not liquid:
        return None

    total_sales, target_sales = get_last_sales(title)
    if total_sales < 10 or target_sales < 2:
        return None

    diff = price - max_target
    total_buyers = int(orders[-1].get("liquidity", sum(int(o.get("amount", 0)) for o in orders)))
    top_buyers = int(orders[0].get("amount", 0))

    return (
        f"🟢 **{title}**\n"
        f"💰 ОФФЕР: **${price:.2f}** → ТАРГЕТ: **${max_target:.2f}**\n"
        f"📈 Разница: **${diff:.2f}** | Прибыль (−7%): **${net:.2f}**\n"
        f"👥 Покупателей: **{total_buyers}** | На топ-цене: **{top_buyers}**\n"
        f"📊 Продаж за сутки: **{total_sales}** | По таргету: **{target_sales}**"
    )
