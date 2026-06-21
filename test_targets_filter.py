"""
Полный анализ скина (перчатки/ножа) по всем под-диапазонам износа экстерьера.

Для каждого бакета floatPartValue (например BS-0…BS-4 для Battle-Scarred):
  🎯 таргеты (buy orders) — топ цена покупки
  🏷️ офферы (продажи)     — мин. цена на маркете
  💰 last-sales           — частота, свежесть, средняя цена
  📊 прибыль              — арбитраж: купить по таргету, продать на маркете (−комиссия)

ВАЖНО: у перчаток/ножей title начинается со звезды «★ ».
Запуск: python test_targets_filter.py
"""
import sys
import os
import re
import time
import requests
from urllib.parse import quote
from collections import defaultdict
from datetime import datetime, timezone
from dotenv import load_dotenv
from auth import BASE_URL, GAME_ID, generate_headers
from dmarket import get_customized_fees, place_target
import fees

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()
JWT = os.getenv("DMARKET_JWT", "").strip()

TITLE = "★ Sport Gloves | Nocts (Battle-Scarred)"
MIN_PROFIT = 5.0       # минимальная прибыль ($) чтобы поставить таргет с фильтром
PLACE_TARGETS = True   # True — реально ставит таргеты (тратит/резервирует деньги!); False — только превью

EXTERIOR_PREFIX = {
    "Factory New": "FN",
    "Minimal Wear": "MW",
    "Field-Tested": "FT",
    "Well-Worn": "WW",
    "Battle-Scarred": "BS",
}


def get_targets(title: str) -> list:
    url = f"{BASE_URL}/marketplace-api/v1/targets-by-title/{GAME_ID}/{quote(title)}"
    r = requests.get(url, headers={"Authorization": JWT, "User-Agent": "Mozilla/5.0"}, timeout=10)
    if r.status_code != 200:
        print(f"Ошибка targets {r.status_code}: {r.text[:200]}")
        return []
    return r.json().get("orders", [])


def get_offers(title: str, limit: int = 100) -> list:
    """Офферы (продажи), отсортированы по цене ↑ (HMAC). Максимум 100 за запрос."""
    path = "/exchange/v1/market/items"
    params = (
        f"?side=market&orderBy=price&orderDir=asc&title={quote(title)}&priceFrom=0&priceTo=0"
        f"&treeFilters=&gameId={GAME_ID}&types=dmarket&myFavorites=false"
        f"&cursor=&limit={limit}&currency=USD&platform=browser&isLoggedIn=true"
    )
    r = requests.get(BASE_URL + path + params, headers=generate_headers("GET", path + params), timeout=10)
    if r.status_code != 200:
        print(f"Ошибка офферов {r.status_code}: {r.text[:200]}")
        return []
    return r.json().get("objects", [])


def get_last_sales(title: str, wear_bucket: str | None = None, limit: int = 100) -> list:
    """Последние продажи. wear_bucket — серверный фильтр по диапазону износа (floatPartValue)."""
    enc = quote(title, safe="()")
    url = (
        f"{BASE_URL}/trade-aggregator/v1/last-sales?title={enc}&gameId={GAME_ID}"
        f"&txOperationType=Target&txOperationType=Offer&limit={limit}"
    )
    if wear_bucket:
        url += f"&filters=floatPartValue%5B%5D={wear_bucket}"  # floatPartValue[]=<bucket>
    r = requests.get(url, headers={"Authorization": JWT, "User-Agent": "Mozilla/5.0"}, timeout=10)
    if r.status_code != 200:
        print(f"Ошибка last-sales {r.status_code}: {r.text[:200]}")
        return []
    return r.json().get("sales", [])


def exterior_buckets(title: str) -> list:
    """Список бакетов износа по экстерьеру в title, напр. BS-0..BS-4."""
    m = re.search(r"\(([^)]+)\)\s*$", title)
    prefix = EXTERIOR_PREFIX.get(m.group(1)) if m else None
    return [f"{prefix}-{i}" for i in range(5)] if prefix else []


def analyze_bucket(bucket: str, by_wear: dict, offers_by_wear: dict, title: str, any_top: float | None = None) -> None:
    targets = sorted(by_wear.get(bucket, []), key=lambda o: int(o["price"]), reverse=True)
    offs = sorted(offers_by_wear.get(bucket, []), key=lambda o: int(o["price"]["USD"]))
    sales = get_last_sales(title, bucket, limit=20)

    bucket_top = int(targets[0]["price"]) / 100 if targets else None
    # «any»-таргеты принимают любой float этого экстерьера — тоже buy-order для бакета
    top_target = max([t for t in (bucket_top, any_top) if t], default=None)
    min_offer = int(offs[0]["price"]["USD"]) / 100 if offs else None
    avg_sale = sum(float(s["price"]) for s in sales) / len(sales) if sales else None

    print(f"\n═══════════════ {bucket} ═══════════════")

    # Таргеты (покупка): сравниваем фильтр (бакет) vs без фильтра (any)
    if bucket_top is not None and any_top is not None:
        if bucket_top >= any_top:
            cnt = sum(int(o.get("amount", 1)) for o in targets)
            print(f"  🎯 Таргет:   ${bucket_top:.2f} (с фильтром {bucket}, {len(targets)}ур/{cnt}шт)")
        else:
            print(f"  🎯 Таргет:   ${any_top:.2f} (без фильтра; с фильтром {bucket} есть, но дешевле ${bucket_top:.2f})")
    elif bucket_top is not None:
        cnt = sum(int(o.get("amount", 1)) for o in targets)
        print(f"  🎯 Таргет:   ${bucket_top:.2f} (с фильтром {bucket}, {len(targets)}ур/{cnt}шт)")
    elif any_top is not None:
        print(f"  🎯 Таргет:   ${any_top:.2f} (без фильтра; с фильтром {bucket} нету)")
    else:
        print("  🎯 Таргет:   нет")

    # Офферы (продажа)
    if offs:
        floats = [o["extra"]["floatValue"] for o in offs if o.get("extra", {}).get("floatValue") is not None]
        frange = f", float {min(floats):.4f}–{max(floats):.4f}" if floats else ""
        print(f"  🏷️  Офферы:   {len(offs)} шт, мин ${min_offer:.2f}{frange}")
    else:
        print("  🏷️  Офферы:   нет продавцов")

    # Продажи (last-sales) — частота / свежесть / средняя
    if sales:
        prices = [float(s["price"]) for s in sales]
        dates = sorted(int(s["date"]) for s in sales)
        span_days = max((dates[-1] - dates[0]) / 86400, 1)
        per_week = len(sales) / span_days * 7
        last_dt = datetime.fromtimestamp(dates[-1], tz=timezone.utc).strftime("%Y-%m-%d")
        offer_cnt = sum(1 for s in sales if s.get("txOperationType") == "Offer")
        print(f"  💰 Продажи:  {len(sales)} за {span_days:.0f}д (~{per_week:.1f}/нед), "
              f"ср ${avg_sale:.2f}, ${min(prices):.2f}–${max(prices):.2f}, посл. {last_dt}")
        print(f"     куплено по Offer: {offer_cnt} из {len(sales)} (Target: {len(sales) - offer_cnt})")
        for s in sorted(sales, key=lambda s: int(s["date"]), reverse=True):
            dt = datetime.fromtimestamp(int(s["date"]), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            attrs = s.get("offerAttributes", {})
            fv = attrs.get("floatValue", 0)
            op = s.get("txOperationType", "?")
            print(f"       {op:7} ${float(s['price']):>8.2f}  float={fv:.4f}  seed={attrs.get('paintSeed')}  {dt}")
    else:
        print("  💰 Продажи:  нет")

    # Прибыль: купить по таргету → продать на маркете (или по средней продаже, если офферов нет)
    sell_ref = min_offer if min_offer else avg_sale
    if top_target and sell_ref:
        fee = fees.fee_fraction(title, sell_ref)
        net = sell_ref * (1 - fee)
        spread = net - top_target
        mark = "🟢" if spread > 0 else "🔴"
        src = "оффер" if min_offer else "ср.продажа"
        print(f"  📊 Прибыль:  ${sell_ref:.2f} ({src}) −{fee * 100:.0f}% = ${net:.2f}  −  таргет ${top_target:.2f}  =  {mark} ${spread:.2f}")

        # Постановка таргета с фильтром (перебить лучший buy-order на 1¢)
        place_price = round(top_target + 0.01, 2)
        place_profit = net - place_price
        if place_profit >= MIN_PROFIT:
            if PLACE_TARGETS:
                ok, err, tid = place_target(title, place_price, float_bucket=bucket)
                status = f"✅ ПОСТАВЛЕН id={tid}" if ok else f"❌ {err}"
                print(f"  💡 Таргет:   {bucket} @ ${place_price:.2f} → прибыль ${place_profit:.2f}  {status}")
                time.sleep(2)  # DMarket лимитит частые создания на один предмет
            else:
                print(f"  💡 План:     таргет с фильтром {bucket} @ ${place_price:.2f} → прибыль ${place_profit:.2f}  (dry-run)")
    else:
        print("  📊 Прибыль:  недостаточно данных (нужны таргет + цена продажи)")


# Комиссия на продажу (реальная, из customized-fees)
fees.update(get_customized_fees())

# Таргеты и офферы — по одному запросу, группируем по бакету износа
orders = get_targets(TITLE)
by_wear = defaultdict(list)
for o in orders:
    by_wear[o.get("attributes", {}).get("floatPartValue", "?")].append(o)

offers = get_offers(TITLE)
offers_by_wear = defaultdict(list)
for o in offers:
    offers_by_wear[o.get("extra", {}).get("floatPartValue", "?")].append(o)

buckets = exterior_buckets(TITLE)
print(f"Анализ: {TITLE}")
print(f"Таргетов всего: {len(orders)}  |  офферов (топ-100 по цене): {len(offers)}")
if not buckets:
    print("Это не перчатка/нож (нет под-диапазонов износа).")
else:
    print(f"Бакеты износа: {', '.join(buckets)}")
    any_top = max((int(o["price"]) for o in by_wear.get("any", [])), default=0) / 100 or None
    for bucket in buckets:
        analyze_bucket(bucket, by_wear, offers_by_wear, TITLE, any_top)
