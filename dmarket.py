import time as _time
import requests
import os
from dotenv import load_dotenv
from auth import BASE_URL, GAME_ID, generate_headers

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


def get_last_sales(exact_title: str, limit: int = 20) -> list:
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