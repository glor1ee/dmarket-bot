"""API-клиент DMarket.

Авторизация — официальный Trading API: пара ключей DMARKET_PUBLIC_KEY /
DMARKET_SECRET_KEY из .env, каждый запрос подписывается Ed25519
(заголовки X-Api-Key / X-Request-Sign / X-Sign-Date). Ключи генерируются в
настройках аккаунта DMarket и НЕ протухают — прежний короткоживущий JWT
(DMARKET_JWT) больше не используется.

Строка подписи: method + path(+query) + body + timestamp.
ВАЖНО: для роутов с параметром в пути (targets-by-title) шлюз проверяет
подпись по ДЕКОДИРОВАННОМУ пути — подписываем сырой title, шлём закодированный.
Query, наоборот, подписывается ровно в том виде, в котором уходит.
"""
import json
import os
import time
from urllib.parse import quote, urlencode

import requests
from dotenv import load_dotenv
from nacl.signing import SigningKey

from auth import BASE_URL, GAME_ID

load_dotenv()
_PUBLIC_KEY = os.getenv("DMARKET_PUBLIC_KEY", "").strip()
_SECRET_KEY = os.getenv("DMARKET_SECRET_KEY", "").strip()
# Секрет DMarket — 128 hex-символов (seed || pubkey); SigningKey нужен 32-байтный seed.
_signing_key = SigningKey(bytes.fromhex(_SECRET_KEY)[:32]) if _SECRET_KEY else None


def _log_fail(where: str, resp=None, exc: Exception | None = None) -> None:
    """Не глотаем ошибки молча: пишем в консоль статус/исключение."""
    if exc is not None:
        print(f"⚠️ {where}: {type(exc).__name__}: {exc}")
    elif resp is not None:
        print(f"⚠️ {where}: HTTP {resp.status_code}: {resp.text[:600]}")


def _signed_headers(method: str, sign_path: str, body: str = "") -> dict:
    """Заголовки Trading API: Ed25519-подпись строки method+path+body+timestamp."""
    ts = str(int(time.time()))
    sig = _signing_key.sign((method + sign_path + body + ts).encode("utf-8")).signature.hex()
    return {
        "X-Api-Key": _PUBLIC_KEY,
        "X-Request-Sign": "dmar ed25519 " + sig,
        "X-Sign-Date": ts,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
    }


def _get(path: str, params: dict | None = None, timeout: int = 10, sign_path: str | None = None):
    """GET с подписью. Query собирается здесь же, чтобы подписанная строка
    байт в байт совпадала с отправленной. sign_path — если подписывать надо
    другую строку (декодированный путь для path-параметров)."""
    if params:
        path = path + "?" + urlencode(params, quote_via=quote)
    return requests.get(BASE_URL + path, headers=_signed_headers("GET", sign_path or path), timeout=timeout)


def _post(path: str, body: str, timeout: int = 10):
    """POST с подписью (body — уже сериализованный JSON, он входит в подпись)."""
    return requests.post(BASE_URL + path, data=body, headers=_signed_headers("POST", path, body), timeout=timeout)


def _parse_float_part(fp: str | None) -> str | None:
    """v2 'FLOAT_PART_BS_1' → 'BS-1'. FLOAT_PART_UNSPECIFIED/None → None."""
    if not fp or fp == "FLOAT_PART_UNSPECIFIED" or not fp.startswith("FLOAT_PART_"):
        return None
    parts = fp[len("FLOAT_PART_"):].rsplit("_", 1)  # 'BS_1' → ['BS','1']
    return "-".join(parts) if len(parts) == 2 else None


def _parse_lock_seconds(v) -> float:
    """v2 tradeLockDuration '480923.14s' → 480923.14; None → 0."""
    if not v:
        return 0.0
    try:
        return float(str(v).rstrip("s"))
    except (TypeError, ValueError):
        return 0.0

# v2 orderBy: price | title | float | createdAt (v1 'updated'/'personal' убраны)
_SORT_OPTIONS = [
    ("price", "asc"),
    ("price", "desc"),
    ("createdAt", "desc"),
    ("title", "asc"),
]
_sort_idx = 0


def get_recommended_skins() -> list:
    """Маркет-офферы (v2/offers). Ротация сортировки. Возвращает [{title, price}]."""
    global _sort_idx
    order_by, order_dir = _SORT_OPTIONS[_sort_idx % len(_SORT_OPTIONS)]
    _sort_idx += 1
    response = _get("/marketplace-api/v2/offers",
                    {"gameId": GAME_ID, "limit": "100", "orderBy": order_by, "orderDir": order_dir})
    if response.status_code != 200:
        raise ValueError(f"v2/offers статус {response.status_code}: {response.text[:200]}")
    result = []
    for o in response.json().get("items", []):
        title = o.get("attributes", {}).get("title")
        price = int(o.get("priceCents", 0)) / 100
        if title and price > 0:
            result.append({"title": title, "price": price})
    return result


def get_aggregated_prices(title: str) -> tuple[float | None, float | None]:
    """Возвращает (min_offer, max_target) за один запрос."""
    body = json.dumps(
        {"filter": {"game": GAME_ID, "titles": [title]}, "limit": 1},
        separators=(",", ":"),
    )
    try:
        r = _post("/marketplace-api/v1/aggregated-prices", body, timeout=5)
        if r.status_code != 200:
            _log_fail("get_aggregated_prices", r)
            return None, None
        items = r.json().get("aggregatedPrices", [])
        if not items:
            return None, None
        item = items[0]
        min_offer = int(item["offerBestPrice"]["Amount"]) / 100 if item.get("offerBestPrice") else None
        max_target = int(item["orderBestPrice"]["Amount"]) / 100 if item.get("orderBestPrice") else None
        return min_offer, max_target
    except Exception as e:
        _log_fail("get_aggregated_prices", exc=e)
        return None, None


def get_market_offers(title: str, limit: int = 50) -> list:
    """Офферы по конкретному скину, отсортированы по цене ↑ (v2/offers).
    Возвращает [(price_usd, offer_id, trade_lock_seconds), ...] — для поиска минимума
    среди чужих (trade_lock_seconds = сколько ещё под trade-protection; 0 = уже торгуется)."""
    try:
        r = _get("/marketplace-api/v2/offers",
                 {"gameId": GAME_ID, "title": title, "limit": str(limit), "orderBy": "price", "orderDir": "asc"})
        if r.status_code != 200:
            _log_fail("get_market_offers", r)
            return []
        result = []
        for o in r.json().get("items", []):
            attrs = o.get("attributes", {})
            if attrs.get("title") != title:
                continue  # только точный title
            price = int(o.get("priceCents", 0)) / 100
            offer_id = o.get("offerId")
            lock = _parse_lock_seconds(attrs.get("tradeLockDuration"))
            if price > 0 and offer_id:
                result.append((price, offer_id, lock))
        return result
    except Exception as e:
        _log_fail("get_market_offers", exc=e)
        return []


def get_targets_by_title(title: str) -> list:
    """Все таргеты (buy orders) по скину с разбивкой по floatPartValue.
    Каждый order: {price (центы), amount, attributes.floatPartValue}.
    Шлюз сверяет подпись по декодированному пути → подписываем сырой title."""
    enc_path = f"/marketplace-api/v1/targets-by-title/{GAME_ID}/{quote(title)}"
    sign_path = f"/marketplace-api/v1/targets-by-title/{GAME_ID}/{title}"
    try:
        r = _get(enc_path, sign_path=sign_path)
        if r.status_code != 200:
            _log_fail("get_targets_by_title", r)
            return []
        return r.json().get("orders", [])
    except Exception as e:
        _log_fail("get_targets_by_title", exc=e)
        return []


def get_offers_by_bucket(title: str, limit: int = 100) -> dict:
    """Мин. цена оффера (USD) по каждому floatPart-бакету для скина (v2/offers).
    Точный матч title. Пагинация по курсору с дедупом по offerId.
    Возвращает {bucket: min_price} (бакет вида 'BS-1')."""
    result: dict[str, float] = {}
    seen_ids: set[str] = set()
    cursor = ""
    try:
        while True:
            params = {"gameId": GAME_ID, "title": title, "limit": str(limit), "orderBy": "price", "orderDir": "asc"}
            if cursor:
                params["cursor"] = cursor
            r = _get("/marketplace-api/v2/offers", params)
            if r.status_code != 200:
                _log_fail("get_offers_by_bucket", r)
                break
            data = r.json()
            items = data.get("items", [])
            new = [o for o in items if o.get("offerId") not in seen_ids]
            if not new:
                break  # новых нет — конец (курсор не продвигается)
            for o in new:
                seen_ids.add(o.get("offerId"))
                attrs = o.get("attributes", {})
                if attrs.get("title") != title:
                    continue
                bucket = _parse_float_part((attrs.get("cs2") or {}).get("floatPart"))
                price = int(o.get("priceCents", 0)) / 100
                if bucket and price > 0 and (bucket not in result or price < result[bucket]):
                    result[bucket] = price
            next_cursor = data.get("cursor", "")
            if not next_cursor or next_cursor == cursor or len(items) < limit:
                break
            cursor = next_cursor
    except Exception as e:
        _log_fail("get_offers_by_bucket", exc=e)
    return result


def get_user_offers() -> list:
    """Мои активные офферы (v2/user/offers), нормализовано:
    [{title, offerId, assetId, price, lock}]. assetId = attributes.id (UUID для v2 batchUpdate)."""
    result = []
    cursor = ""
    try:
        while True:
            params = {"gameId": GAME_ID, "limit": "100"}
            if cursor:
                params["cursor"] = cursor
            r = _get("/marketplace-api/v2/user/offers", params)
            if r.status_code != 200:
                _log_fail("get_user_offers", r)
                break
            data = r.json()
            items = data.get("items", [])
            if not items:
                break
            for o in items:
                attrs = o.get("attributes", {})
                result.append({
                    "title": attrs.get("title", ""),
                    "offerId": o.get("offerId"),
                    "assetId": attrs.get("id"),
                    "price": int(o.get("priceCents", 0)) / 100,
                    "lock": _parse_lock_seconds(attrs.get("tradeLockDuration")),
                })
            cursor = data.get("cursor", "")
            if not cursor or len(items) < 100:
                break
    except Exception as e:
        _log_fail("get_user_offers", exc=e)
    return result


def auth_is_valid() -> bool:
    """Проверяет ключи Trading API лёгким запросом баланса.
    True — авторизация работает; False — ключи отозваны/невалидны или сеть недоступна."""
    try:
        return _get("/account/v1/balance").status_code == 200
    except Exception:
        return False


def get_balance() -> float | None:
    try:
        r = _get("/account/v1/balance", timeout=5)
        if r.status_code != 200:
            _log_fail("get_balance", r)
            return None
        return int(r.json().get("usd", 0)) / 100
    except Exception as e:
        _log_fail("get_balance", exc=e)
        return None


def get_customized_fees() -> dict:
    """Таблица комиссий на продажу: {defaultFee, reducedFees}. {} при ошибке.
    reducedFees ~12.5к позиций (~2 МБ), отдаётся одним ответом без пагинации."""
    try:
        r = _get("/exchange/v1/customized-fees", {"gameId": GAME_ID, "limit": "100000"}, timeout=30)
        if r.status_code != 200:
            _log_fail("get_customized_fees", r)
            return {}
        return r.json()
    except Exception as e:
        _log_fail("get_customized_fees", exc=e)
        return {}


def get_user_inventory() -> list:
    all_items = []
    cursor = ""
    while True:
        params = {"gameId": GAME_ID, "limit": "100", "currency": "USD"}
        if cursor:
            params["cursor"] = cursor
        try:
            r = _get("/marketplace-api/v2/user/inventory", params)
            if r.status_code != 200:
                _log_fail("get_user_inventory", r)
                break
            data = r.json()
            all_items.extend(data.get("items", []))
            cursor = data.get("cursor", "")
            if not cursor:
                break
        except Exception as e:
            _log_fail("get_user_inventory", exc=e)
            break
    return all_items


def get_user_targets(status_filter: str | None = None) -> list:
    try:
        r = _get("/marketplace-api/v2/user/targets", {"gameId": GAME_ID, "limit": "100"})
        if r.status_code != 200:
            _log_fail("get_user_targets", r)
            return []
        items = r.json().get("items", [])
        if status_filter:
            items = [i for i in items if i.get("status") == status_filter]
        return items
    except Exception as e:
        _log_fail("get_user_targets", exc=e)
        return []


def get_closed_targets() -> list:
    """Возвращает все закрытые (купленные) таргеты. Поддерживает пагинацию.

    ВНИМАНИЕ: эндпоинт возвращает один и тот же курсор и те же записи на каждой
    странице, поэтому останавливаемся, как только новых TargetID не приходит.
    """
    all_trades = []
    seen_ids: set[str] = set()
    cursor = ""
    while True:
        params = {"gameId": GAME_ID, "limit": "100"}
        if cursor:
            params["cursor"] = cursor
        try:
            r = _get("/marketplace-api/v1/user-targets/closed", params)
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
    all_trades = []
    seen_ids: set[str] = set()
    cursor = ""
    while True:
        params = {"gameId": GAME_ID, "limit": "100"}
        if cursor:
            params["cursor"] = cursor
        try:
            r = _get("/marketplace-api/v1/user-offers/closed", params)
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
    target = {
        "Title": title,
        "Amount": 1,
        "Price": {"Currency": "USD", "Amount": str(price_usd)},
    }
    if float_bucket:
        target["Attrs"] = {"floatPartValue": float_bucket}
    body_str = json.dumps({"GameID": GAME_ID, "Targets": [target]}, separators=(",", ":"))
    try:
        r = _post("/marketplace-api/v1/user-targets/create", body_str)
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
    body_str = json.dumps({"Targets": [{"TargetID": target_id}]}, separators=(",", ":"))
    try:
        r = _post("/marketplace-api/v1/user-targets/delete", body_str)
        if r.status_code == 200:
            return True, ""
        _log_fail("delete_target", r)
        return False, r.text[:300]
    except Exception as e:
        _log_fail("delete_target", exc=e)
        return False, str(e)


def _post_offer_batch(path: str, body: dict, where: str) -> tuple[bool, str, str | None]:
    """Общий POST для v2 offers:batchCreate / batchUpdate.

    Оба отвечают 200 с {"offers":[...], "failed":[{"code","message",...}]}.
    Возвращает (ok, err, offer_id)."""
    try:
        r = _post(path, json.dumps(body, separators=(",", ":")))
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
    body = {"requests": [{"assetId": asset_id, "priceCents": int(price_cents)}]}
    return _post_offer_batch("/marketplace-api/v2/offers:batchCreate", body, "create_offer")


def update_offer(offer_id: str, asset_id: str, price_cents: int) -> tuple[bool, str, str | None]:
    """Меняет цену активного оффера (v2 batchUpdate). price в центах."""
    body = {"requests": [{"offerId": offer_id, "assetId": asset_id, "priceCents": int(price_cents)}]}
    return _post_offer_batch("/marketplace-api/v2/offers:batchUpdate", body, "update_offer")


def get_last_sales(exact_title: str, limit: int = 20, wear_bucket: str | None = None) -> list:
    """Последние продажи. wear_bucket (напр. 'BS-0') — серверный фильтр по диапазону
    износа (floatPartValue); с ним тянем и Target-, и Offer-сделки."""
    encoded = quote(exact_title, safe="()")
    path = f"/trade-aggregator/v1/last-sales?title={encoded}&gameId={GAME_ID}&limit={limit}"
    if wear_bucket:
        path += f"&txOperationType=Target&txOperationType=Offer&filters=floatPartValue%5B%5D={wear_bucket}"
    try:
        r = _get(path, timeout=5)
        if r.status_code != 200:
            _log_fail("get_last_sales", r)
            return []
        return r.json().get("sales", [])
    except Exception as e:
        _log_fail("get_last_sales", exc=e)
        return []
