import requests
import os
from dotenv import load_dotenv
from auth import BASE_URL, GAME_ID, generate_headers

load_dotenv()
_JWT = os.getenv("DMARKET_JWT", "").strip()


def _log_fail(where: str, resp=None, exc: Exception | None = None) -> None:
    """Не глотаем ошибки молча: пишем в консоль статус/исключение."""
    if exc is not None:
        print(f"⚠️ {where}: {type(exc).__name__}: {exc}")
    elif resp is not None:
        print(f"⚠️ {where}: HTTP {resp.status_code}: {resp.text[:600]}")

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


def get_market_offers(title: str, limit: int = 50) -> list:
    """Офферы по конкретному скину, отсортированы по цене ↑ (HMAC).
    Возвращает [(price_usd, offer_id), ...] — для поиска минимума среди чужих.

    ВАЖНО: фильтр title= в API подстроночный (ловит и StatTrak™-вариант),
    поэтому оставляем только объекты с ТОЧНЫМ совпадением title."""
    from urllib.parse import quote
    path = "/exchange/v1/market/items"
    params = (
        f"?side=market&orderBy=price&orderDir=asc"
        f"&title={quote(title)}&priceFrom=0&priceTo=0"
        f"&treeFilters=&gameId={GAME_ID}&types=dmarket&myFavorites=false"
        f"&cursor=&limit={limit}&currency=USD&platform=browser&isLoggedIn=true"
    )
    try:
        r = requests.get(BASE_URL + path + params, headers=generate_headers("GET", path + params), timeout=10)
        if r.status_code != 200:
            _log_fail("get_market_offers", r)
            return []
        result = []
        for o in r.json().get("objects", []):
            if o.get("title") != title:
                continue  # подстроночный матч API — отсекаем чужие title
            price = int(o.get("price", {}).get("USD", 0)) / 100
            offer_id = (o.get("extra") or {}).get("offerId")
            if price > 0 and offer_id:
                result.append((price, offer_id))
        return result
    except Exception as e:
        _log_fail("get_market_offers", exc=e)
        return []


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


def jwt_is_valid() -> bool:
    """Проверяет JWT лёгким запросом баланса. True — токен живой, False — 401/ошибка."""
    try:
        r = requests.get(
            f"{BASE_URL}/account/v1/balance",
            headers={"Authorization": _JWT, "User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def get_balance() -> float | None:
    try:
        r = requests.get(
            f"{BASE_URL}/account/v1/balance",
            headers={"Authorization": _JWT, "User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        if r.status_code != 200:
            return None
        return int(r.json().get("usd", 0)) / 100
    except Exception:
        return None


def get_customized_fees() -> dict:
    """Таблица комиссий на продажу: {defaultFee, reducedFees}. {} при ошибке.
    reducedFees ~12.5к позиций (~2 МБ), отдаётся одним ответом без пагинации."""
    url = f"{BASE_URL}/exchange/v1/customized-fees"
    try:
        r = requests.get(
            url,
            params={"gameId": GAME_ID, "limit": "100000"},
            headers={"Authorization": _JWT, "User-Agent": "Mozilla/5.0"},
            timeout=30,
        )
        if r.status_code != 200:
            _log_fail("get_customized_fees", r)
            return {}
        return r.json()
    except Exception as e:
        _log_fail("get_customized_fees", exc=e)
        return {}


def get_user_inventory() -> list:
    url = f"{BASE_URL}/marketplace-api/v2/user/inventory"
    all_items = []
    cursor = ""
    while True:
        params = {"gameId": GAME_ID, "limit": "100", "currency": "USD"}
        if cursor:
            params["cursor"] = cursor
        try:
            r = requests.get(url, params=params, headers={"Authorization": _JWT, "User-Agent": "Mozilla/5.0"}, timeout=10)
            if r.status_code != 200:
                break
            data = r.json()
            all_items.extend(data.get("items", []))
            cursor = data.get("cursor", "")
            if not cursor:
                break
        except Exception:
            break
    return all_items


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


def get_closed_targets() -> list:
    """Возвращает все закрытые (купленные) таргеты. Поддерживает пагинацию.

    ВНИМАНИЕ: эндпоинт возвращает один и тот же курсор и те же записи на каждой
    странице, поэтому останавливаемся, как только новых TargetID не приходит.
    """
    url = f"{BASE_URL}/marketplace-api/v1/user-targets/closed"
    all_trades = []
    seen_ids: set[str] = set()
    cursor = ""
    while True:
        params = {"gameId": GAME_ID, "limit": "100"}
        if cursor:
            params["cursor"] = cursor
        try:
            r = requests.get(url, params=params, headers={"Authorization": _JWT, "User-Agent": "Mozilla/5.0"}, timeout=10)
            if r.status_code != 200:
                _log_fail("get_closed_targets", r)
                break
            data = r.json()
            trades = data.get("Trades", [])
            new = [t for t in trades if t.get("TargetID") not in seen_ids]
            if not new:
                break  # новых записей нет — конец (курсор не продвигается)
            for t in new:
                seen_ids.add(t.get("TargetID"))
            all_trades.extend(new)
            next_cursor = data.get("Cursor", "")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        except Exception as e:
            _log_fail("get_closed_targets", exc=e)
            break
    return all_trades


def get_closed_offers() -> list:
    """Возвращает все закрытые офферы (мои продажи). Та же защита от не-продвигающегося
    курсора, что и в get_closed_targets — дедуп по OfferID.

    У каждой записи есть фактический Fee: {Amount: {Amount}, Percent} и Status
    (successful / trade_protected)."""
    url = f"{BASE_URL}/marketplace-api/v1/user-offers/closed"
    all_trades = []
    seen_ids: set[str] = set()
    cursor = ""
    while True:
        params = {"gameId": GAME_ID, "limit": "100"}
        if cursor:
            params["cursor"] = cursor
        try:
            r = requests.get(url, params=params, headers={"Authorization": _JWT, "User-Agent": "Mozilla/5.0"}, timeout=10)
            if r.status_code != 200:
                _log_fail("get_closed_offers", r)
                break
            data = r.json()
            trades = data.get("Trades", [])
            new = [t for t in trades if t.get("OfferID") not in seen_ids]
            if not new:
                break
            for t in new:
                seen_ids.add(t.get("OfferID"))
            all_trades.extend(new)
            next_cursor = data.get("Cursor", "")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        except Exception as e:
            _log_fail("get_closed_offers", exc=e)
            break
    return all_trades


def place_target(title: str, price_usd: float, float_bucket: str | None = None) -> tuple[bool, str, str | None]:
    """Ставит таргет. float_bucket (напр. 'BS-0') — фильтр по диапазону износа
    для перчаток/ножей (Attrs.floatPartValue). Без него — таргет на любой float."""
    import json
    url = f"{BASE_URL}/marketplace-api/v1/user-targets/create"
    target = {
        "Title": title,
        "Amount": 1,
        "Price": {"Currency": "USD", "Amount": str(price_usd)},
    }
    if float_bucket:
        target["Attrs"] = {"floatPartValue": float_bucket}
    body_data = {"GameID": GAME_ID, "Targets": [target]}
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
            # HTTP 200, но не Successful — вытаскиваем реальную причину
            reason = ""
            if result:
                reason = result[0].get("Error") or result[0].get("Message") or ""
            _log_fail("place_target", r)
            return False, (reason or r.text[:300]), None
        _log_fail("place_target", r)
        return False, r.text[:300], None
    except Exception as e:
        _log_fail("place_target", exc=e)
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
        _log_fail("delete_target", r)
        return False, r.text[:300]
    except Exception as e:
        _log_fail("delete_target", exc=e)
        return False, str(e)


def _post_offer_batch(url: str, body: dict, where: str) -> tuple[bool, str, str | None]:
    """Общий POST для v2 offers:batchCreate / batchUpdate.

    Оба отвечают 200 с {"offers":[...], "failed":[{"code","message",...}]}.
    Возвращает (ok, err, offer_id)."""
    import json
    headers = {
        "Authorization": _JWT,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
    }
    try:
        r = requests.post(url, data=json.dumps(body, separators=(",", ":")), headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            failed = data.get("failed") or []
            if failed:
                f0 = failed[0]
                err = f0.get("code") or "failed"
                if f0.get("message"):
                    err += f": {f0['message']}"
                _log_fail(where, r)
                return False, err, None
            offers = data.get("offers") or []
            new_id = offers[0].get("offerId") if offers else None
            return True, "", new_id
        _log_fail(where, r)
        return False, r.text[:300], None
    except Exception as e:
        _log_fail(where, exc=e)
        return False, str(e), None


def create_offer(asset_id: str, price_cents: int) -> tuple[bool, str, str | None]:
    """Выставляет предмет на продажу (v2 batchCreate). price в центах."""
    url = f"{BASE_URL}/marketplace-api/v2/offers:batchCreate"
    body = {"requests": [{"assetId": asset_id, "priceCents": int(price_cents)}]}
    return _post_offer_batch(url, body, "create_offer")


def update_offer(offer_id: str, asset_id: str, price_cents: int) -> tuple[bool, str, str | None]:
    """Меняет цену активного оффера (v2 batchUpdate). price в центах."""
    url = f"{BASE_URL}/marketplace-api/v2/offers:batchUpdate"
    body = {"requests": [{"offerId": offer_id, "assetId": asset_id, "priceCents": int(price_cents)}]}
    return _post_offer_batch(url, body, "update_offer")


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