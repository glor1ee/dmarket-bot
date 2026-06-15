"""Хранилище buy-price: title -> цена покупки из закрытых таргетов (USD).

Заполняется только из закрытых (купленных) таргетов — sync_closed_targets.
get_buy_price читает значение для проверки дохода при авто-занижении офферов.
"""
import json
import os
import threading

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cost_basis.json")
_lock = threading.Lock()


def _read() -> dict:
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write(data: dict) -> None:
    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _PATH)


def get_buy_price(title: str) -> float | None:
    """Цена покупки скина или None, если не записана."""
    with _lock:
        val = _read().get(title)
    return float(val) if val is not None else None


def sync_closed_targets(trades: list) -> int:
    """Записывает цену покупки из закрытых таргетов в JSON.
    Сортируем по времени закрытия — последняя покупка перезаписывает прошлую.
    Возвращает количество обновлённых записей."""
    if not trades:
        return 0
    sorted_trades = sorted(trades, key=lambda t: int(t.get("ClosedAt", 0)))
    with _lock:
        data = _read()
        count = 0
        for t in sorted_trades:
            title = t.get("Title", "")
            price = float(t.get("Price", {}).get("Amount", 0))
            if title and price > 0:
                data[title] = round(price, 2)
                count += 1
        _write(data)
    return count
