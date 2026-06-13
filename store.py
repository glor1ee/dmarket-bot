"""Простое хранилище cost-basis: title -> цена таргета, по которой куплен скин.

DMarket не отдаёт цену покупки в оффере, поэтому запоминаем цену таргета
в момент его постановки. При обновлении офферов читаем её для проверки дохода.
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


def record_buy_price(title: str, price: float) -> None:
    """Запомнить цену таргета для скина (перезаписывает прошлую)."""
    if not title:
        return
    with _lock:
        data = _read()
        data[title] = round(float(price), 2)
        tmp = _PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _PATH)


def get_buy_price(title: str) -> float | None:
    """Цена покупки скина или None, если не записана."""
    with _lock:
        val = _read().get(title)
    return float(val) if val is not None else None
