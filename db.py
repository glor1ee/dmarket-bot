"""SQLite-хранилище закрытых сделок и статистики (bot.db).

Заменяет in-memory множества _synced_closed_ids/_synced_closed_offer_ids:
дедуп новых закрытых таргетов/офферов идёт по первичному ключу в БД и
переживает рестарт бота. Здесь же считается и сохраняется недельная статистика.

Таблицы:
- closed_targets  — покупки по таргетам (TargetID, title, цена, время закрытия)
- closed_offers   — продажи по офферам (OfferID, цена, фактическая комиссия,
                    статус successful/trade_protected, цена покупки на момент записи)
- stats_reports   — снапшоты сгенерированных отчётов (JSON)

Все функции потокобезопасны (вызываются из asyncio.to_thread).
"""
import json
import os
import sqlite3
import threading
import time
from contextlib import closing

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.db")
_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    """Создаёт таблицы, если их ещё нет. Вызывается один раз при старте."""
    with _lock, closing(_conn()) as c, c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS closed_targets (
                target_id TEXT PRIMARY KEY,
                title     TEXT NOT NULL,
                price     REAL NOT NULL,
                closed_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS closed_offers (
                offer_id    TEXT PRIMARY KEY,
                title       TEXT NOT NULL,
                price       REAL NOT NULL,
                fee_amount  REAL NOT NULL DEFAULT 0,
                fee_percent REAL NOT NULL DEFAULT 0,
                status      TEXT NOT NULL DEFAULT '',
                buy_price   REAL,
                closed_at   INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS stats_reports (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at  INTEGER NOT NULL,
                period_days INTEGER NOT NULL,
                payload     TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_targets_closed_at ON closed_targets(closed_at);
            CREATE INDEX IF NOT EXISTS idx_offers_closed_at  ON closed_offers(closed_at);
        """)


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def add_closed_targets(trades: list) -> list:
    """Пишет закрытые (купленные) таргеты из ответа DMarket в БД.
    Возвращает исходные записи, которых в БД ещё не было (для сигналов/cost-basis)."""
    new = []
    with _lock, closing(_conn()) as c, c:
        for t in trades:
            tid = t.get("TargetID")
            title = t.get("Title", "")
            price = _num(t.get("Price", {}).get("Amount", 0))
            closed_at = int(_num(t.get("ClosedAt", 0)))
            if not tid or not title:
                continue
            cur = c.execute(
                "INSERT OR IGNORE INTO closed_targets (target_id, title, price, closed_at) VALUES (?, ?, ?, ?)",
                (tid, title, price, closed_at),
            )
            if cur.rowcount:
                new.append(t)
    return new


def add_closed_offers(trades: list, buy_price_lookup=None) -> list:
    """Пишет закрытые офферы (мои продажи) в БД. Возвращает записи, которых ещё не было.

    buy_price_lookup(title) -> float | None — цена покупки на момент записи
    (снимок из cost-basis; позже она может перезаписаться новой покупкой, а в БД
    останется актуальная для этой продажи).

    У уже известных офферов обновляет status/fee: DMarket сначала отдаёт продажу
    как trade_protected, а позже ту же запись как successful."""
    new = []
    with _lock, closing(_conn()) as c, c:
        for t in trades:
            oid = t.get("OfferID")
            title = t.get("Title", "")
            price = _num(t.get("Price", {}).get("Amount", 0))
            fee_obj = t.get("Fee") or {}
            fee_amount = _num((fee_obj.get("Amount") or {}).get("Amount", 0))
            fee_percent = _num(fee_obj.get("Percent", 0))
            status = t.get("Status", "") or ""
            # у закрытых офферов поле называется OfferClosedAt (у таргетов — ClosedAt)
            closed_at = int(_num(t.get("OfferClosedAt") or t.get("ClosedAt") or 0))
            if not oid or not title:
                continue
            cur = c.execute(
                "INSERT OR IGNORE INTO closed_offers"
                " (offer_id, title, price, fee_amount, fee_percent, status, buy_price, closed_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (oid, title, price, fee_amount, fee_percent, status,
                 buy_price_lookup(title) if buy_price_lookup else None, closed_at),
            )
            if cur.rowcount:
                new.append(t)
            else:
                # обновляем статус/комиссию; заодно чиним closed_at=0 у ранее
                # записанных строк (старый парсер не знал про OfferClosedAt)
                c.execute(
                    "UPDATE closed_offers SET status = ?, fee_amount = ?, fee_percent = ?,"
                    " closed_at = CASE WHEN closed_at = 0 THEN ? ELSE closed_at END"
                    " WHERE offer_id = ?",
                    (status, fee_amount, fee_percent, closed_at, oid),
                )
    return new


def stats(period_days: int = 7) -> dict:
    """Статистика по сделкам за последние period_days дней (из БД, без запросов к API)."""
    since = int(time.time()) - period_days * 86400
    with _lock, closing(_conn()) as c:
        b = c.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(price), 0) AS total"
            " FROM closed_targets WHERE closed_at >= ?", (since,),
        ).fetchone()
        o = c.execute(
            "SELECT COUNT(*) AS n,"
            "       COALESCE(SUM(price), 0) AS gross,"
            "       COALESCE(SUM(fee_amount), 0) AS fees,"
            "       COALESCE(SUM(price - fee_amount), 0) AS net,"
            "       COALESCE(SUM(CASE WHEN status = 'trade_protected' THEN 1 ELSE 0 END), 0) AS protected_n,"
            "       COALESCE(SUM(CASE WHEN status = 'trade_protected' THEN price - fee_amount ELSE 0 END), 0) AS protected_net"
            " FROM closed_offers WHERE closed_at >= ?", (since,),
        ).fetchone()
        p = c.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(price - fee_amount - buy_price), 0) AS realized"
            " FROM closed_offers WHERE closed_at >= ? AND buy_price IS NOT NULL", (since,),
        ).fetchone()
        top = c.execute(
            "SELECT title, price - fee_amount AS net, price - fee_amount - buy_price AS profit"
            " FROM closed_offers WHERE closed_at >= ? AND buy_price IS NOT NULL"
            " ORDER BY profit DESC LIMIT 5", (since,),
        ).fetchall()
    return {
        "period_days": period_days,
        "since": since,
        "buys": {"count": b["n"], "sum": b["total"]},
        "sales": {
            "count": o["n"], "gross": o["gross"], "fees": o["fees"], "net": o["net"],
            "trade_protected": o["protected_n"], "pending_net": o["protected_net"],
        },
        "profit": {"count": p["n"], "realized": p["realized"]},
        "top_sales": [{"title": r["title"], "net": r["net"], "profit": r["profit"]} for r in top],
    }


def save_report(report: dict) -> None:
    """Сохраняет снапшот сгенерированного отчёта (история статистики)."""
    with _lock, closing(_conn()) as c, c:
        c.execute(
            "INSERT INTO stats_reports (created_at, period_days, payload) VALUES (?, ?, ?)",
            (int(time.time()), int(report.get("period_days", 0)), json.dumps(report, ensure_ascii=False)),
        )
