# DMarket Resale Bot

## Что это

Discord-бот для арбитража CS2-скинов на DMarket: ищет выгодные лоты, сравнивает минимальный оффер продавцов с максимальным таргетом (buy order) покупателей, считает чистую прибыль после комиссии 10%, постит сигналы в Discord и **автоматически** ставит таргеты, перебивает их и управляет своими офферами на продажу.

## Файлы

- `auth.py` — HMAC-SHA256 подпись (`X-Request-Sign: dmarket <hex>`), ключи из `.env`
- `dmarket.py` — API-клиент DMarket (лоты, цены, таргеты, офферы, инвентарь, закрытые таргеты)
- `store.py` — cost-basis: `title -> цена покупки`, хранится в `cost_basis.json`
- `embed.py` — сборка Discord-эмбедов
- `bot.py` — Discord-бот: UI-кнопки/модалки + три фоновых цикла
- `test_*.py` — ручные диагностики (баланс, офферы, создание оффера, совпадение title)
- `.env` — `DISCORD_TOKEN`, `DMARKET_JWT`, `DMARKET_PUBLIC_KEY`, `DMARKET_SECRET_KEY`

## Аутентификация (важно)

Два метода, оба используются:
- **HMAC** (`auth.generate_headers`) — только для `get_recommended_skins` (`/exchange/v1/market/items`). Стабильный.
- **JWT** (`DMARKET_JWT`, заголовок `Authorization`) — для всего остального (цены, таргеты, офферы, инвентарь, баланс, закрытые таргеты). **Короткоживущий — периодически протухает (HTTP 401).**

## Ключевая логика прибыли

```
net = min_offer * 0.90 - max_target      # комиссия DMarket 10%
```
Сигнал по скину: `1 <= net <= 10`, цена оффера <= $100.

## Цикл сканирования (`scan_loop`)

Перебирает рекомендованные скины (сортировка ротируется по 4 вариантам). Для прошедших фильтр прибыли тянет last-sales (10 шт.), считает ликвидность и постит сигнал в `PROFIT_CHANNEL`/`REVIEW_CHANNEL`.

**Ликвидность** (`_count_large_gaps`): был день с ≥3 продажами И не более `MAX_LARGE_GAPS` (2) разрывов между продажами длиннее `MAX_SALE_GAP_DAYS` (2 дня), считая и разрыв до «сейчас».

**Авто-таргет** ставится только если: `profit > 0` И `liquid` И офферов в истории больше, чем таргетов (`len(offer_prices) > len(target_prices)`) И цена `auto_price = max_target + 0.02` влезает в 60% баланса И `auto_price > 5`.

При ошибке API — `sleep(30)` и продолжаем (не busy-loop).

## Цикл перебивки таргетов (`rebid_loop`, каждые 905 с)

1. **Синхронизация cost-basis**: тянет закрытые (купленные) таргеты `get_closed_targets`, новые (по `TargetID`) пишет в `cost_basis.json` через `sync_closed_targets` и шлёт «🛒 Куплен по таргету» в `CLOSED_TARGETS_CHANNEL`. Уже синхронизированные ID лежат в `_synced_closed_ids` (стартово заполняется в `on_ready`).
2. **Перебивка**: по активным таргетам — если меня перекрыли (`max_target > my_price`) и всё ещё выгодно (`1 <= net <= 10`): удаляет старый таргет, ставит новый по `max_target + 0.02`. Если стало невыгодно — просто удаляет.

## Цикл управления офферами (`offer_update_loop`, каждые 905 с)

- **Шаг 1 — репрайс существующих офферов** (`_reprice_offer`): если кто-то на маркете дешевле меня (`min_offer < my_price`), снижает цену до `min_offer − 1¢` через v2 `batchUpdate`. Только если `get_buy_price(title)` известна и доход `new_price*0.90 − buy_price > 0`.
- **Шаг 2 — автолистинг** (`_list_unlisted`): по предметам инвентаря с `inMarket=True` (предмет на стороне DMarket, доступен к продаже — не требует трейда Steam→DMarket) и UUID в `attributes.id` выставляет оффер по `min_offer − 1¢` через v2 `batchCreate`. Если cost-basis известна — с проверкой дохода; если нет — выставляет с пометкой «цена покупки неизвестна».

  Примечание: `inMarket` НЕ означает «уже есть активный оффер» — это «лежит у DMarket и может быть выставлен». Предметы с активным оффером в списке свободных инвентарных не появляются, поэтому дублей `batchCreate` на практике нет.

Цена оффера всегда `_undercut_cents(min_offer) = round(min_offer*100) − 1` (на 1 цент ниже минимального оффера).

## cost-basis (`store.py`)

`cost_basis.json`: `{ "<title>": <цена покупки USD> }`. Заполняется **только** из закрытых таргетов (`sync_closed_targets`, ключ = `Trade["Title"]`, при дубле берётся более поздний по `ClosedAt`). `get_buy_price(title)` — точное совпадение по title. Файл в `.gitignore`.

Совпадение title между источниками проверено (`test_title_match.py`): офферы (`offer["Title"]`) и инвентарь (`attributes.title`) используют те же полные market-hash-имена, что и закрытые таргеты — расхождений нет.

## API DMarket

- Лоты: `GET /exchange/v1/market/items` (HMAC)
- Агрег. цены: `POST /marketplace-api/v1/aggregated-prices` → `offerBestPrice` / `orderBestPrice`
- Last-sales: `GET /trade-aggregator/v1/last-sales`
- Таргеты: `POST .../user-targets/create`, `.../delete`, `GET /v2/user/targets`, `GET .../user-targets/closed`
- Офферы: `GET /v1/user-offers`, `POST /v2/offers:batchCreate`, `POST /v2/offers:batchUpdate`
- Инвентарь: `GET /v2/user/inventory` (пагинация по `cursor`)
- Баланс: `GET /account/v1/balance`

`get_closed_targets` обходит баг пагинации DMarket (курсор не продвигается) через отслеживание `seen TargetID`.

## Каналы Discord (ID в `bot.py`)

`PROFIT` / `REVIEW` / `LIQUID_PROFIT` — сигналы сканера; `MY_TARGETS` / `MY_OFFERS` / `MY_INVENTORY` — панели управления; `TARTGETS` — поставленные таргеты; `TARTGET_UPDATE` — перебивки; `OFFER_UPDATE` — изменения офферов; `CLOSED_TARGETS` — покупки.

## Известные проблемы / TODO

- **JWT молча протухает**: GET-функции (`get_user_inventory`, `get_user_offers`, `get_aggregated_prices`, `get_user_targets`) при 401 возвращают `[]`/`None` **без сигнала** — бот тихо простаивает. Добавить `_log_fail`/Discord-алерт при 401.
- **Отладочные `print`** в `_list_unlisted` (`buy_price`, `ok/err`) — убрать.
- **Текст про «ретраи»** в логе `rebid_loop` устарел (ретраев в коде уже нет).
- `format_targets` в `bot.py` — мёртвый код.
- Нет пауз между API-вызовами внутри циклов — риск rate-limit.

## Запуск

```
python bot.py                  # Discord-бот
python test_title_match.py     # диагностика совпадения title (только GET)
```
