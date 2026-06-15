"""Кэш кастомных комиссий DMarket (sell fee).

Дефолт — 10%. Часть скинов имеет сниженную комиссию (промо) в пределах
ценового диапазона и до expiresAt. Таблицу (~12.5к позиций, ~2 МБ) тянем
редко и держим в памяти — поиск по title тогда O(1).
"""
import time
import threading

_lock = threading.Lock()
_default_fraction = 0.10
_reduced: dict[str, dict] = {}
_updated_at = 0.0


def update(raw: dict) -> int:
    """Обновляет кэш из ответа /exchange/v1/customized-fees.
    Возвращает количество льготных позиций (0 — если ответ пустой, кэш не трогаем)."""
    global _default_fraction, _reduced, _updated_at
    if not raw:
        return 0
    with _lock:
        df = raw.get("defaultFee") or {}
        try:
            _default_fraction = float(df.get("fraction", 0.10))
        except (TypeError, ValueError):
            _default_fraction = 0.10
        _reduced = {
            item["title"]: item
            for item in raw.get("reducedFees", [])
            if item.get("title")
        }
        _updated_at = time.time()
        return len(_reduced)


def is_stale(max_age_seconds: float = 3600) -> bool:
    """True, если кэш ни разу не грузили или он старше max_age_seconds."""
    return time.time() - _updated_at > max_age_seconds


def fee_fraction(title: str, price_usd: float) -> float:
    """Доля комиссии для скина при данной цене (напр. 0.02 или 0.10).
    Учитывает ценовой диапазон и срок действия льготы."""
    with _lock:
        item = _reduced.get(title)
        default = _default_fraction
    if not item:
        return default
    price_cents = price_usd * 100
    min_p = item.get("minPrice") or 0
    max_p = item.get("maxPrice") or 0
    if price_cents < min_p or (max_p and price_cents > max_p):
        return default
    expires = item.get("expiresAt") or 0
    if expires and time.time() > expires:
        return default
    try:
        return float(item.get("fraction", default))
    except (TypeError, ValueError):
        return default


def net_after_fee(title: str, price_usd: float) -> float:
    """Сколько получишь на руки после комиссии."""
    return price_usd * (1 - fee_fraction(title, price_usd))
