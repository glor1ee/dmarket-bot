import time as _time
import requests
import os
from dotenv import load_dotenv
from auth import BASE_URL, GAME_ID, generate_headers
from liquidity import is_liquid

load_dotenv()
_JWT = os.getenv("DMARKET_JWT", "").strip()

_SORT_OPTIONS = [
    ("price", "asc"),
    ("price", "desc"),
    ("updated", "desc"),
    ("personal", "desc"),
]
_sort_idx = 0


def get_recommended_skins() -> list:
    global _sort_idx
    order_by, order_dir = _SORT_OPTIONS[_sort_idx % len(_SORT_OPTIONS)]
    _sort_idx += 1
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


def get_aggregated_prices(title: str) -> tuple[float | None, float | None]:
    """Возвращает (min_offer, max_target) за один запрос."""
    import json
    url = f"{BASE_URL}/marketplace-api/v1/aggregated-prices"
    body = json.dumps(
        {"filter": {"game": GAME_ID, "titles": [title]}, "limit": 1},
        separators=(",", ":"),
    )
    headers = {
        "Authorization": _JWT,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
    }
    try:
        r = requests.post(url, data=body, headers=headers, timeout=5)
        if r.status_code != 200:
            return None, None
        items = r.json().get("aggregatedPrices", [])
        if not items:
            return None, None
        item = items[0]
        min_offer = int(item["offerBestPrice"]["Amount"]) / 100 if item.get("offerBestPrice") else None
        max_target = int(item["orderBestPrice"]["Amount"]) / 100 if item.get("orderBestPrice") else None
        return min_offer, max_target
    except Exception:
        return None, None


def get_user_offers() -> list:
    url = f"{BASE_URL}/marketplace-api/v1/user-offers"
    params = {"gameId": GAME_ID, "limit": "100", "currency": "USD"}
    headers = {"Authorization": _JWT, "User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        if r.status_code != 200:
            return []
        return r.json().get("Items", [])
    except Exception:
        return []


def get_user_targets(status_filter: str | None = None) -> list:
    url = f"{BASE_URL}/marketplace-api/v2/user/targets"
    params = {"gameId": GAME_ID, "limit": "100"}
    headers = {"Authorization": _JWT, "User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        if r.status_code != 200:
            return []
        items = r.json().get("items", [])
        if status_filter:
            items = [i for i in items if i.get("status") == status_filter]
        return items
    except Exception:
        return []


def place_target(title: str, price_usd: float) -> tuple[bool, str, str | None]:
    import json
    url = f"{BASE_URL}/marketplace-api/v1/user-targets/create"
    body_data = {
        "GameID": GAME_ID,
        "Targets": [
            {
                "Title": title,
                "Amount": 1,
                "Price": {"Currency": "USD", "Amount": str(price_usd)},
            }
        ],
    }
    body_str = json.dumps(body_data, separators=(",", ":"))
    headers = {
        "Authorization": _JWT,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
    }
    try:
        r = requests.post(url, data=body_str, headers=headers, timeout=10)
        if r.status_code == 200:
            result = r.json().get("Result", [])
            if result and result[0].get("Successful"):
                target_id = result[0].get("TargetID")
                return True, "", target_id
        return False, r.text[:300], None
    except Exception as e:
        return False, str(e), None


def delete_target(target_id: str) -> tuple[bool, str]:
    import json
    url = f"{BASE_URL}/marketplace-api/v1/user-targets/delete"
    body_data = {"Targets": [{"TargetID": target_id}]}
    body_str = json.dumps(body_data, separators=(",", ":"))
    headers = {
        "Authorization": _JWT,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
    }
    try:
        r = requests.post(url, data=body_str, headers=headers, timeout=10)
        if r.status_code == 200:
            return True, ""
        return False, r.text[:300]
    except Exception as e:
        return False, str(e)


def get_min_offer(title: str) -> float | None:
    url = f"{BASE_URL}/exchange/v1/appraise/targets"
    body = {"objects": [{"title": title}], "gameId": GAME_ID}
    try:
        r = requests.post(
            url,
            json=body,
            headers={"Authorization": _JWT, "User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        if r.status_code != 200:
            return None
        for obj in r.json().get("objects", []):
            for price_entry in obj.get("stats", {}).get("prices", []):
                if price_entry.get("name") == "minOfferPrice":
                    amount = price_entry.get("amount", 0)
                    return int(amount) / 100 if amount else None
        return None
    except Exception:
        return None


def get_last_sales_raw(exact_title: str, limit: int = 20) -> list:
    from urllib.parse import quote
    encoded = quote(exact_title, safe="()")
    url = f"{BASE_URL}/trade-aggregator/v1/last-sales?title={encoded}&gameId={GAME_ID}&limit={limit}"
    try:
        r = requests.get(url, headers={"Authorization": _JWT, "User-Agent": "Mozilla/5.0"}, timeout=5)
        if r.status_code != 200:
            return []
        return r.json().get("sales", [])
    except Exception:
        return []


def get_last_sales(exact_title: str) -> tuple[int, int]:
    from urllib.parse import quote
    encoded = quote(exact_title, safe="()")
    url = f"{BASE_URL}/trade-aggregator/v1/last-sales?title={encoded}&gameId={GAME_ID}&limit=100"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
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
