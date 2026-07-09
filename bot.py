import discord
import os
import re
import json
import asyncio
import requests.utils
from collections import Counter
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

KYIV = timezone(timedelta(hours=3))
from dmarket import get_recommended_skins, get_aggregated_prices, get_last_sales, place_target, delete_target, get_user_targets, get_user_offers, get_user_inventory, get_balance, create_offer, update_offer, get_closed_targets, jwt_is_valid, get_customized_fees, get_market_offers, get_closed_offers, get_targets_by_title, get_offers_by_bucket
from embed import target_embed, my_targets_embed, inventory_embed, rebid_embed, offer_update_embed, offer_create_embed, offer_create_no_buy_embed, offer_sold_embed
from store import get_buy_price, sync_closed_targets
import fees

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
CONTROL_CHANNEL_ID = 1510646017865810024
PROFIT_CHANNEL_ID = 1511801054918869196
REVIEW_CHANNEL_ID = 1511801444825698404
LIQUID_PROFIT_CHANNEL_ID = 1512393920078680145
PREMIUM_CHANNEL_ID = 1518712682960781374

MY_INVENTORY_CHANNEL_ID = 1513469237308559410
MY_TARGETS_CHANNEL_ID = 1513236279905616054
MY_OFFERS_CHANNEL_ID = 1513236353251414186
TARTGET_UPDATE_CHANNEL_ID = 1513236453138890762
OFFER_UPDATE_CHANNEL_ID = 1515027974301290616
TARTGETS_CHANNEL_ID = 1513234981269278750
CLOSED_TARGETS_CHANNEL_ID = 1516105280499224768
CLOSED_OFFERS_CHANNEL_ID = 1516208442967331039

OWNER_USER_ID = 421320508986884096


min_balance = 111 # если баланс меньше, не ставим авто-таргет (даже если прибыльный), чтобы не блокировать деньги в оффере при ошибке
placed_targets = []


# TargetID закрытых таргетов, уже синхронизированных в JSON (заполняется в on_ready).
_synced_closed_ids: set[str] = set()
# OfferID закрытых офферов (продаж), уже обработанных (заполняется в on_ready).
_synced_closed_offer_ids: set[str] = set()

# Панель управления: счётчик сбросов seen в scan_loop + отложенные действия по нему
_seen_reset_count = 0
_stop_bot_at_resets: int | None = None   # остановить бота при достижении этого числа сбросов
_shutdown_at_resets: int | None = None   # выключить устройство при достижении этого числа сбросов
_stop_bot_task: "asyncio.Task | None" = None    # таймер остановки бота (для отмены)
_shutdown_task: "asyncio.Task | None" = None     # таймер выключения ПК (для отмены)
_scan_active = True                              # активен ли scan_loop (пауза/возобновление)
_scan_toggle_task: "asyncio.Task | None" = None  # таймер вкл/выкл скана (для отмены)

# Депонированные на DMarket предметы имеют UUID в attributes.id (Steam-предметы — нет).
# Только для них доступен batchCreate/batchUpdate.
DEPOSITED_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


class DeleteTargetView(discord.ui.View):
    def __init__(self, target_id: str):
        super().__init__(timeout=None)
        self.target_id = target_id

    @discord.ui.button(label="🗑 Удалить таргет", style=discord.ButtonStyle.danger)
    async def delete_btn(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        ok, err = await asyncio.to_thread(delete_target, self.target_id)
        if ok:
            _button.disabled = True
            _button.label = "✅ Таргет удалён"
            await interaction.message.edit(view=self)
            await interaction.followup.send("✅ Таргет удалён.", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ Ошибка: {err}", ephemeral=True)


class PlaceTargetModal(discord.ui.Modal, title="Поставить таргет"):
    price_input = discord.ui.TextInput(
        label="Цена таргета (USD)",
        placeholder="например: 45.50",
        required=True,
        max_length=10,
    )

    def __init__(self, skin_title: str):
        super().__init__()
        self.skin_title = skin_title

    async def on_submit(self, interaction: discord.Interaction):
        try:
            price_val = float(self.price_input.value.replace(",", "."))
        except ValueError:
            await interaction.response.send_message("❌ Неверный формат цены.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        ok, err, target_id = await asyncio.to_thread(place_target, self.skin_title, price_val)
        if ok:
            await interaction.followup.send(f"✅ Таргет поставлен: **${price_val:.2f}**", ephemeral=True)
            targets_channel = interaction.client.get_channel(TARTGETS_CHANNEL_ID)
            placed_targets.append(self.skin_title)  # добавляем в кэш выставленных таргетов, чтобы не предлагать ставить второй раз в scan_loop
            if targets_channel and target_id:
                await targets_channel.send(
                    embed=target_embed(self.skin_title, price_val, interaction.user),
                    view=DeleteTargetView(target_id=target_id),
                )
        else:
            await interaction.followup.send(f"❌ Ошибка: {err}", ephemeral=True)


class LinkOnlyView(discord.ui.View):
    def __init__(self, title: str, placed_price: float | None = None):
        super().__init__(timeout=None)
        url = (
            "https://dmarket.com/ingame-items/item-list/csgo-skins"
            f"?title={requests.utils.quote(title)}"
        )
        self.add_item(discord.ui.Button(
            label="🔗 Открыть на DMarket",
            url=url,
            style=discord.ButtonStyle.link,
        ))
        if placed_price is not None:
            self.add_item(discord.ui.Button(
                label=f"🎯 Таргет поставлен: ${placed_price:.2f}",
                style=discord.ButtonStyle.secondary,
                disabled=True,
            ))


class LinkButton(discord.ui.View):
    def __init__(self, title: str, max_target: float):
        super().__init__(timeout=None)
        self.title = title
        self.max_target = max_target

        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.label == "🎯 Авто-таргет":
                child.label = f"🎯 Авто-таргет (${max_target + 0.02:.2f})"
                break

        url = (
            "https://dmarket.com/ingame-items/item-list/csgo-skins"
            f"?title={requests.utils.quote(title)}"
        )
        self.add_item(discord.ui.Button(
            label="🔗 Открыть на DMarket",
            url=url,
            style=discord.ButtonStyle.link,
        ))

    @discord.ui.button(label="🎯 Авто-таргет", style=discord.ButtonStyle.success)
    async def auto_target_btn(self, interaction: discord.Interaction, _button: discord.ui.Button):
        auto_price = round(self.max_target + 0.02, 2)
        await interaction.response.defer(ephemeral=True)
        ok, err, target_id = await asyncio.to_thread(place_target, self.title, auto_price)
        if ok:
            await interaction.followup.send(f"✅ Авто-таргет поставлен: **${auto_price:.2f}**", ephemeral=True)
            targets_channel = interaction.client.get_channel(TARTGETS_CHANNEL_ID)
            placed_targets.append(self.title)  # добавляем в кэш выставленных таргетов, чтобы не предлагать ставить второй раз в scan_loop
            if targets_channel and target_id:
                await targets_channel.send(
                    embed=target_embed(self.title, auto_price, interaction.user),
                    view=DeleteTargetView(target_id=target_id),
                )
        else:
            await interaction.followup.send(f"❌ Ошибка: {err}", ephemeral=True)

    @discord.ui.button(label="🎯 Свой таргет", style=discord.ButtonStyle.primary)
    async def place_target_btn(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_modal(PlaceTargetModal(skin_title=self.title))


class SkinInfoView(discord.ui.View):
    """Кнопка с актуальной информацией о скине (цены, последние продажи) + ссылка."""
    def __init__(self, title: str):
        super().__init__(timeout=None)
        self.title = title
        url = (
            "https://dmarket.com/ingame-items/item-list/csgo-skins"
            f"?title={requests.utils.quote(title)}"
        )
        self.add_item(discord.ui.Button(
            label="🔗 Открыть на DMarket",
            url=url,
            style=discord.ButtonStyle.link,
        ))

    @discord.ui.button(label="ℹ️ Инфо о скине", style=discord.ButtonStyle.secondary)
    async def info_btn(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        (min_offer, max_target), sales = await asyncio.gather(
            asyncio.to_thread(get_aggregated_prices, self.title),
            asyncio.to_thread(get_last_sales, self.title, 30),
        )
        if not sales:
            await interaction.followup.send(f"ℹ️ **{self.title}** — нет данных о продажах.", ephemeral=True)
            return
        info = build_skin_info(self.title, sales, min_offer, max_target, get_buy_price(self.title))
        await interaction.followup.send("\n".join(info["lines"]), ephemeral=True)


class PremiumTargetView(discord.ui.View):
    """Премиум-сигнал: ссылка + авто-таргет С ФИЛЬТРОМ по конкретному бакету износа."""
    def __init__(self, title: str, bucket: str, top_target: float):
        super().__init__(timeout=None)
        self.title = title
        self.bucket = bucket
        self.top_target = top_target
        url = (
            "https://dmarket.com/ingame-items/item-list/csgo-skins"
            f"?title={requests.utils.quote(title)}"
        )
        self.add_item(discord.ui.Button(
            label="🔗 Открыть на DMarket",
            url=url,
            style=discord.ButtonStyle.link,
        ))

        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.label == "🎯 Авто-таргет":
                child.label = f"🎯 Авто-таргет (${top_target + 0.02:.2f})" 
                break

    @discord.ui.button(label="🎯 Авто-таргет", style=discord.ButtonStyle.success)
    async def auto_target_btn(self, interaction: discord.Interaction, _button: discord.ui.Button):
        auto_price = round(self.top_target + 0.02, 2)
        await interaction.response.defer(ephemeral=True)
        ok, err, target_id = await asyncio.to_thread(place_target, self.title, auto_price, self.bucket)
        if ok:
            await interaction.followup.send(
                f"✅ Авто-таргет **{self.bucket}** на **{self.title}**: **${auto_price:.2f}**", ephemeral=True)
            placed_targets.append(self.title)
            targets_channel = interaction.client.get_channel(TARTGETS_CHANNEL_ID)
            if targets_channel and target_id:
                await targets_channel.send(
                    embed=target_embed(f"{self.title} [{self.bucket}]", auto_price, interaction.user),
                    view=DeleteTargetView(target_id=target_id),
                )
        else:
            await interaction.followup.send(f"❌ Ошибка: {err}", ephemeral=True)


class TargetsControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📋 Все таргеты", style=discord.ButtonStyle.secondary, custom_id="targets_all")
    async def all_targets(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        items = await asyncio.to_thread(get_user_targets)
        await interaction.followup.send(embed=my_targets_embed(items, "all"), ephemeral=True)

    @discord.ui.button(label="🟢 Активные", style=discord.ButtonStyle.success, custom_id="targets_active")
    async def active_targets(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        items = await asyncio.to_thread(get_user_targets, "TARGET_STATUS_ACTIVE")
        await interaction.followup.send(embed=my_targets_embed(items, "active"), ephemeral=True)

    @discord.ui.button(label="🔴 Неактивные", style=discord.ButtonStyle.danger, custom_id="targets_inactive")
    async def inactive_targets(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        items = await asyncio.to_thread(get_user_targets, "TARGET_STATUS_INACTIVE")
        await interaction.followup.send(embed=my_targets_embed(items, "inactive"), ephemeral=True)


class OffersControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📦 Мои офферы", style=discord.ButtonStyle.secondary, custom_id="offers_all")
    async def all_offers(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        items, balance, closed = await asyncio.gather(
            asyncio.to_thread(get_user_offers),
            asyncio.to_thread(get_balance),
            asyncio.to_thread(get_closed_offers),
        )
        await interaction.followup.send(
            f"📦 Офферы ({len(items)}):\n{format_offers(items, balance, closed)}",
            ephemeral=True,
        )


class InventoryControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎒 Инвентарь", style=discord.ButtonStyle.secondary, custom_id="inventory_all")
    async def show_inventory(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        items, balance = await asyncio.gather(
            asyncio.to_thread(get_user_inventory),
            asyncio.to_thread(get_balance),
        )
        await interaction.followup.send(embed=inventory_embed(items, balance), ephemeral=True)


async def _stop_bot_after(seconds: int) -> None:
    await asyncio.sleep(seconds)
    print(f"⏹ Остановка бота по таймеру ({seconds // 60} мин)")
    await client.close()


async def _shutdown_after(seconds: int) -> None:
    await asyncio.sleep(seconds)
    print(f"⏻ Выключение устройства по таймеру ({seconds // 60} мин)")
    os.system("shutdown /s /t 0")


async def _on_stop_minutes(interaction: discord.Interaction, n: int) -> None:
    global _stop_bot_task
    if _stop_bot_task and not _stop_bot_task.done():
        _stop_bot_task.cancel()
    _stop_bot_task = asyncio.ensure_future(_stop_bot_after(n * 60))
    await interaction.response.send_message(f"⏹ Бот будет остановлен через **{n} мин**.", ephemeral=True)


async def _on_stop_resets(interaction: discord.Interaction, n: int) -> None:
    global _stop_bot_at_resets
    _stop_bot_at_resets = _seen_reset_count + n
    await interaction.response.send_message(
        f"♻️ Бот остановится после **{n}** сбросов seen (счётчик сейчас {_seen_reset_count}).", ephemeral=True)


async def _on_shutdown_minutes(interaction: discord.Interaction, n: int) -> None:
    global _shutdown_task
    if _shutdown_task and not _shutdown_task.done():
        _shutdown_task.cancel()
    _shutdown_task = asyncio.ensure_future(_shutdown_after(n * 60))
    await interaction.response.send_message(f"⏻ Устройство выключится через **{n} мин**.", ephemeral=True)


async def _on_shutdown_resets(interaction: discord.Interaction, n: int) -> None:
    global _shutdown_at_resets
    _shutdown_at_resets = _seen_reset_count + n
    await interaction.response.send_message(
        f"♻️ Устройство выключится после **{n}** сбросов seen (счётчик сейчас {_seen_reset_count}).", ephemeral=True)


async def _on_cancel(interaction: discord.Interaction) -> None:
    """Отменяет все запланированные действия: таймеры и цели по сбросам seen."""
    global _stop_bot_at_resets, _shutdown_at_resets, _stop_bot_task, _shutdown_task, _scan_toggle_task
    cancelled = []
    if _stop_bot_task and not _stop_bot_task.done():
        _stop_bot_task.cancel()
        cancelled.append("остановка бота по таймеру")
    _stop_bot_task = None
    if _shutdown_task and not _shutdown_task.done():
        _shutdown_task.cancel()
        cancelled.append("выключение ПК по таймеру")
    _shutdown_task = None
    if _scan_toggle_task and not _scan_toggle_task.done():
        _scan_toggle_task.cancel()
        cancelled.append("вкл/выкл скана по таймеру")
    _scan_toggle_task = None
    if _stop_bot_at_resets is not None:
        cancelled.append("остановка бота (сбросы seen)")
        _stop_bot_at_resets = None
    if _shutdown_at_resets is not None:
        cancelled.append("выключение ПК (сбросы seen)")
        _shutdown_at_resets = None
    msg = "✅ Отменено: " + ", ".join(cancelled) if cancelled else "Нечего отменять."
    await interaction.response.send_message(msg, ephemeral=True)


async def _scan_set_after(seconds: int, active: bool) -> None:
    await asyncio.sleep(seconds)
    global _scan_active
    _scan_active = active
    print(f"{'▶️ scan_loop включён' if active else '⏸ scan_loop на паузе'} по таймеру ({seconds // 60} мин)")


async def _on_scan_on(interaction: discord.Interaction) -> None:
    global _scan_active
    _scan_active = True
    await interaction.response.send_message("▶️ scan_loop активирован.", ephemeral=True)


async def _on_scan_off(interaction: discord.Interaction) -> None:
    global _scan_active
    _scan_active = False
    await interaction.response.send_message("⏸ scan_loop поставлен на паузу.", ephemeral=True)


async def _on_scan_on_minutes(interaction: discord.Interaction, n: int) -> None:
    global _scan_toggle_task
    if _scan_toggle_task and not _scan_toggle_task.done():
        _scan_toggle_task.cancel()
    _scan_toggle_task = asyncio.ensure_future(_scan_set_after(n * 60, True))
    await interaction.response.send_message(f"▶️ scan_loop включится через **{n} мин**.", ephemeral=True)


async def _on_scan_off_minutes(interaction: discord.Interaction, n: int) -> None:
    global _scan_toggle_task
    if _scan_toggle_task and not _scan_toggle_task.done():
        _scan_toggle_task.cancel()
    _scan_toggle_task = asyncio.ensure_future(_scan_set_after(n * 60, False))
    await interaction.response.send_message(f"⏸ scan_loop встанет на паузу через **{n} мин**.", ephemeral=True)


class ValueModal(discord.ui.Modal):
    """Универсальная модалка: одно положительное число → передаётся в handler."""
    def __init__(self, title: str, label: str, handler):
        super().__init__(title=title)
        self.handler = handler
        self.value_input = discord.ui.TextInput(label=label, placeholder="число", required=True, max_length=6)
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            n = int(self.value_input.value.strip())
            if n <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("❌ Нужно положительное целое число.", ephemeral=True)
            return
        await self.handler(interaction, n)


class ControlPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="⏹ Стоп бота (мин)", style=discord.ButtonStyle.danger, custom_id="ctrl_stop_min")
    async def stop_min(self, interaction: discord.Interaction, _b: discord.ui.Button):
        await interaction.response.send_modal(ValueModal("Стоп бота через N минут", "Минут", _on_stop_minutes))

    @discord.ui.button(label="⏹ Стоп бота (сбросы seen)", style=discord.ButtonStyle.danger, custom_id="ctrl_stop_resets")
    async def stop_resets(self, interaction: discord.Interaction, _b: discord.ui.Button):
        await interaction.response.send_modal(ValueModal("Стоп бота через N сбросов seen", "Кол-во сбросов", _on_stop_resets))

    @discord.ui.button(label="⏻ Выключить ПК (мин)", style=discord.ButtonStyle.secondary, custom_id="ctrl_shutdown_min")
    async def shutdown_min(self, interaction: discord.Interaction, _b: discord.ui.Button):
        await interaction.response.send_modal(ValueModal("Выключить ПК через N минут", "Минут", _on_shutdown_minutes))

    @discord.ui.button(label="⏻ Выключить ПК (сбросы seen)", style=discord.ButtonStyle.secondary, custom_id="ctrl_shutdown_resets")
    async def shutdown_resets(self, interaction: discord.Interaction, _b: discord.ui.Button):
        await interaction.response.send_modal(ValueModal("Выключить ПК через N сбросов seen", "Кол-во сбросов", _on_shutdown_resets))

    @discord.ui.button(label="▶️ Скан вкл", style=discord.ButtonStyle.success, custom_id="ctrl_scan_on")
    async def scan_on(self, interaction: discord.Interaction, _b: discord.ui.Button):
        await _on_scan_on(interaction)

    @discord.ui.button(label="⏸ Скан выкл", style=discord.ButtonStyle.secondary, custom_id="ctrl_scan_off")
    async def scan_off(self, interaction: discord.Interaction, _b: discord.ui.Button):
        await _on_scan_off(interaction)

    @discord.ui.button(label="▶️ Скан вкл (мин)", style=discord.ButtonStyle.success, custom_id="ctrl_scan_on_min")
    async def scan_on_min(self, interaction: discord.Interaction, _b: discord.ui.Button):
        await interaction.response.send_modal(ValueModal("Включить скан через N минут", "Минут", _on_scan_on_minutes))

    @discord.ui.button(label="⏸ Скан выкл (мин)", style=discord.ButtonStyle.secondary, custom_id="ctrl_scan_off_min")
    async def scan_off_min(self, interaction: discord.Interaction, _b: discord.ui.Button):
        await interaction.response.send_modal(ValueModal("Выключить скан через N минут", "Минут", _on_scan_off_minutes))

    @discord.ui.button(label="🚫 Отменить всё", style=discord.ButtonStyle.primary, custom_id="ctrl_cancel")
    async def cancel_all(self, interaction: discord.Interaction, _b: discord.ui.Button):
        await _on_cancel(interaction)


MIN_OFFER_SALES = 12       # минимум продаж через офферы в выборке (из ~30)
MIN_SALES_PER_DAY = 1      # минимальная скорость продаж-офферов в день
MAX_LAST_AGE_HOURS = 36    # не старше: часов с последней продажи
BURST_WINDOW_S = 300       # окно всплеска, секунд (5 минут)
MAX_SALES_PER_BURST = 3    # больше стольких продаж в окне → подозрительный всплеск, не ликвид
TARGET_TOP_K = 5           # не-премиум: сколько верхних таргет-уровней сравниваем
TARGET_MAX_SPREAD = 0.01   # верхний таргет не выше нижнего из топ-K больше чем на 1%


def _has_burst(ts: list, window_s: int = BURST_WINDOW_S, max_in_window: int = MAX_SALES_PER_BURST) -> bool:
    """True, если в каком-то окне window_s секунд продаж больше max_in_window
    (скользящее окно по отсортированным таймстампам)."""
    j = 0
    for i in range(len(ts)):
        while ts[i] - ts[j] > window_s:
            j += 1
        if i - j + 1 >= max_in_window:
            return True
    return False


def _targets_top_clustered(orders: list, top_k: int = TARGET_TOP_K, max_spread: float = TARGET_MAX_SPREAD) -> tuple[bool, list]:
    """Верхние таргеты примерно одной цены? Берём top_k РАЗНЫХ ценовых уровней (по убыванию)
    и проверяем, что самый высокий не выше нижнего из них больше чем на max_spread.
    Возвращает (ok, top_levels). ok=False если уровней меньше top_k (нельзя судить)."""
    prices = sorted({int(o.get("price", 0)) / 100 for o in orders if int(o.get("price", 0)) > 0}, reverse=True)
    top = prices[:top_k]
    if len(top) < top_k:
        return False, top
    return top[0] <= top[-1] * (1 + max_spread), top


def liquidity_score(sales: list, min_offer_sales: int = MIN_OFFER_SALES,
                    min_rate: float = MIN_SALES_PER_DAY, max_age_h: float = MAX_LAST_AGE_HOURS) -> dict:
    """Простая ликвидность: достаточно продаж через офферы + скорость + свежесть,
    и без подозрительного всплеска (>3 продаж за 5 минут). Пороги параметризуемы
    (для по-бакетного премиум-анализа — менее строгие).

    offers     — продаж через офферы в выборке
    rate       — продаж-офферов в день за период выборки
    last_age_h — часов с последней продажи (любого типа)
    burst      — был ли всплеск >3 продаж за 5 минут
    ok         — прошёл ли пороги
    """
    now = datetime.now(tz=KYIV).timestamp()
    ts = sorted(int(s.get("date", 0)) for s in sales if s.get("date"))
    offer_sales = [s for s in sales if s.get("txOperationType") == "Offer"]
    if len(offer_sales) < min_offer_sales or len(ts) < 2:
        return {"ok": False, "rate": 0.0, "last_age_h": 999.0, "n": len(ts), "offers": len(offer_sales), "burst": False}
    span_days = max((ts[-1] - ts[0]) / 86400, 1)
    rate = len(offer_sales) / span_days
    last_age_h = (now - ts[-1]) / 3600
    burst = _has_burst(ts)
    ok = rate >= min_rate and last_age_h <= max_age_h and not burst
    return {"ok": ok, "rate": rate, "last_age_h": last_age_h, "n": len(ts), "offers": len(offer_sales), "burst": burst}


# Диапазоны float по экстерьеру (для отсева премиального низкого float — бакета "-0").
EXTERIOR_FLOAT_RANGE = {
    "Minimal Wear": (0.07, 0.15),
    "Field-Tested": (0.15, 0.38),
    "Well-Worn": (0.38, 0.45),
    "Battle-Scarred": (0.45, 1.00),
}


def _premium_float_threshold(title: str) -> float | None:
    """Граница «премиального» низкого float (бакет -0) для экстерьера из title.
    None — для Factory New (исключаем) или если экстерьер не распознан/нет износа."""
    m = re.search(r"\(([^)]+)\)\s*$", title)
    ext = m.group(1) if m else ""
    premium_frac = 0.20  
    if ext == "Field-Tested" or ext == "Well-Worn":
        premium_frac = 0.3 
    rng = EXTERIOR_FLOAT_RANGE.get(ext)  # FN намеренно отсутствует → None
    if not rng:
        return None
    lo, hi = rng
    return lo + (hi - lo) * premium_frac


# Префиксы бакетов износа по экстерьеру (для по-бакетного премиум-анализа ножей/перчаток).
EXTERIOR_PREFIX = {
    "Minimal Wear": "MW", "Field-Tested": "FT",
    "Battle-Scarred": "BS",
}
PREMIUM_MIN_PROFIT = 5.0     # минимальная прибыль ($) по бакету, чтобы кинуть в premium-канал
# Менее строгая ликвидность для бакетов (продаж по конкретному износу мало)
BUCKET_MIN_OFFER_SALES = 12
BUCKET_MIN_RATE = 0.2        # продаж-офферов в день
BUCKET_MAX_AGE_H = 100       # свежесть последней продажи, часов (5 дней)


def _exterior_buckets(title: str) -> list:
    """Бакеты износа по экстерьеру из title, напр. BS-0..BS-4. [] если экстерьер не распознан."""
    m = re.search(r"\(([^)]+)\)\s*$", title)
    prefix = EXTERIOR_PREFIX.get(m.group(1)) if m else None
    return [f"{prefix}-{i}" for i in range(3)] if prefix else []


async def analyze_premium(title: str) -> list | None:
    """По-бакетный анализ для ножей/перчаток.
    По каждому диапазону износа берём фильтрованные last-sales, считаем ликвидность
    (мягкие пороги) и прибыль = СРЕДНЯЯ цена продаж·(1−комиссия) − мин. оффер бакета.
    В канал — только бакеты, прошедшие ликвидность И прибыль > PREMIUM_MIN_PROFIT,
    со статистикой строго по бакету.

    Запросы: targets-by-title + offers-by-bucket + по 1 last-sales на бакет с оффером."""
    buckets = _exterior_buckets(title)
    if not buckets:
        return None
    orders, offers = await asyncio.gather(
        asyncio.to_thread(get_targets_by_title, title),
        asyncio.to_thread(get_offers_by_bucket, title),
    )
    if not offers:
        return None

    target_top: dict[str, float] = {}
    any_top = 0.0
    for o in orders:
        b = o.get("attributes", {}).get("floatPartValue")
        p = int(o.get("price", 0)) / 100
        if b == "any":
            any_top = max(any_top, p)
        elif b:
            target_top[b] = max(target_top.get(b, 0.0), p)

    blocks = []
    for b in buckets:
        min_offer = offers.get(b)
        if not min_offer:
            continue

        # фильтрованные last-sales по бакету
        sales = await asyncio.to_thread(get_last_sales, title, 30, b)
        liq = liquidity_score(sales, BUCKET_MIN_OFFER_SALES, BUCKET_MIN_RATE, BUCKET_MAX_AGE_H)
        if not liq["ok"]:
            continue

        # прибыль от СРЕДНЕЙ цены продаж по фильтру: avg·(1−комиссия) − мин. оффер
        prices = [float(s["price"]) for s in sales if s.get("price")]
        if not prices:
            continue
        avg_sale = sum(prices) / len(prices)
        fee = fees.fee_fraction(title, avg_sale)
        top_t = max(target_top.get(b, 0.0), any_top)
        net = min_offer * (1 - fee) - top_t
        profit = avg_sale * (1 - fee) - top_t
        if profit < PREMIUM_MIN_PROFIT or net < PREMIUM_MIN_PROFIT:
            continue
        recent = sorted(sales, key=lambda s: int(s.get("date", 0)), reverse=True)[:15]
        sale_lines = []
        for s in recent:
            dt = datetime.fromtimestamp(int(s.get("date", 0)), tz=KYIV).strftime("%d %b %H:%M")
            op = s.get("txOperationType", "?")
            fv = s.get("offerAttributes", {}).get("floatValue", 0)
            sale_lines.append(f"      `{op:<7}` ${float(s.get('price', 0)):<8.2f} float {fv:.4f}  {dt}")

        text = (
            f"💎 **{title}** · 🟢 **{b}** — прибыль **${net:.2f}**\n"
            f"   купить оффер **${min_offer:.2f}** → ср.продажа **${avg_sale:.2f} −{fee * 100:.0f}% - {top_t}** = **${profit:.2f}**\n"
            f"   таргет (ref): **${top_t:.2f}** | ликвидность 🟢: офферов **{liq['offers']}** | **{liq['rate']:.2f}/день** | свежесть **{liq['last_age_h']:.0f}ч** | выборка **{liq['n']}**\n"
            f"   последние продажи ({b}):\n" + "\n".join(sale_lines)
        )
        blocks.append((text, b, top_t))  # текст + бакет + топ-таргет (для кнопки авто-таргета с фильтром)

    return blocks or None


def build_skin_info(title: str, sales: list, min_offer: float | None = None,
                    max_target: float | None = None, buy_price: float | None = None) -> dict:
    """Инфо-блок о скине (строки + метрики) из УЖЕ полученных данных.

    Сетевых вызовов нет — sales/цены передаёт вызывающий, поэтому переиспользование
    в scan_loop не добавляет запросов. Возвращает {lines, liq, avg_offer, profit, net,
    offer_prices, target_prices}; фильтры остаются на стороне вызывающего."""
    day_counts = Counter()
    for s in sales:
        day = datetime.fromtimestamp(int(s.get("date", 0)), tz=KYIV).strftime("%d %b %Y")
        day_counts[day] += 1

    offer_sales = [s for s in sales if s.get("txOperationType") == "Offer" and s.get("price")]
    offer_prices = [float(s["price"]) for s in offer_sales]  # полный список — для отображения/счёта
    target_prices = [float(s["price"]) for s in sales if s.get("txOperationType") == "Target" and s.get("price")]

    # средняя цена БЕЗ премиальных низко-float продаж (бакет -0), чтобы они не надували профит
    premium_thr = _premium_float_threshold(title)

    def _is_premium_low_float(s: dict) -> bool:
        if premium_thr is None:
            return False
        fv = s.get("offerAttributes", {}).get("floatValue")
        return fv is not None and fv <= premium_thr

    avg_prices = [float(s["price"]) for s in offer_sales if not _is_premium_low_float(s)]
    premium_excluded = len(offer_prices) - len(avg_prices)
    avg_offer = sum(avg_prices) / len(avg_prices) if avg_prices else None
    liq = liquidity_score(sales)
    # реальная комиссия на продажу (2% для льготных скинов, иначе 10%)
    sell_ref = min_offer if min_offer is not None else avg_offer
    fee = fees.fee_fraction(title, sell_ref) if sell_ref is not None else 0.10
    keep = 1 - fee
    net = (min_offer * keep - max_target) if (min_offer is not None and max_target is not None) else None
    profit = (avg_offer * keep - max_target) if (avg_offer is not None and max_target is not None) else None

    emoji = "🟢" if (profit is not None and profit > 0) else "🔴"
    mo = f"${min_offer:.2f}" if min_offer is not None else "—"
    mt = f"${max_target:.2f}" if max_target is not None else "—"
    net_s = f"${net:.2f}" if net is not None else "—"
    fee_s = f" · комиссия {fee * 100:.0f}%" if net is not None else ""
    lines = [
        f"{emoji} **{title}**",
        f"   ОФФЕР: **{mo}** | ТАРГЕТ: **{mt}** | Прибыль: **{net_s}**{fee_s}",
    ]
    if buy_price is not None:
        lines.append(f"   Цена покупки: **${buy_price:.2f}**")
    for s in sales:
        dt = datetime.fromtimestamp(int(s.get("date", 0)), tz=KYIV).strftime("%d %b %Y %H:%M")
        lines.append(f"   `{s.get('txOperationType', '?'):<7}` ${s.get('price', '?'):<8} {dt}")
    if offer_prices:
        lines.append(f"   Минимальный оффер:  **${min(offer_prices):.2f}**")
        if avg_offer is not None:
            extra = f" (без {premium_excluded} премиум-флоат)" if premium_excluded else ""
            lines.append(f"   Сред оффер:  **${avg_offer:.2f}**{extra}")
        else:
            lines.append("   Сред оффер:  — (все продажи — премиум-флоат)")
    if target_prices:
        lines.append(f"   Макс таргет: **${max(target_prices):.2f}**")
    if profit is not None:
        lines.append(f"   Прибыль (сред−{fee * 100:.0f}%−таргет): **${profit:.2f}**")
    if day_counts:
        avg_per_day = sum(day_counts.values()) / len(day_counts)
        lines.append(f"   Сред продаж в день: **{avg_per_day:.2f}**")
        lines.append(f"   Макс продаж за день: **{max(day_counts.values())}**")
    lines.append(
        f"   Ликвидность {'🟢' if liq['ok'] else '🔴'}: "
        f"офферов **{liq['offers']}** | **{liq['rate']:.2f}/день** | "
        f"свежесть **{liq['last_age_h']:.0f}ч** | выборка {liq['n']}"
        f"{' | ⚠️ всплеск >3/5мин' if liq.get('burst') else ''}"
    )
    return {
        "lines": lines, "liq": liq, "avg_offer": avg_offer,
        "profit": profit, "net": net,
        "offer_prices": offer_prices, "target_prices": target_prices,
    }


async def scan_loop():
    global _seen_reset_count
    await client.wait_until_ready()
    profit_channel = client.get_channel(PROFIT_CHANNEL_ID)
    review_channel = client.get_channel(REVIEW_CHANNEL_ID)
    liquid_profit_channel = client.get_channel(LIQUID_PROFIT_CHANNEL_ID)
    seen: set[str] = set()
    seen_reset_at = asyncio.get_event_loop().time()

    while not client.is_closed():
        if not _scan_active:
            await asyncio.sleep(5)  # пауза скана — ждём активации
            continue
        if asyncio.get_event_loop().time() - seen_reset_at >= 7200:
            seen.clear()
            seen_reset_at = asyncio.get_event_loop().time()
            _seen_reset_count += 1
            print(f"♻️ seen сброшен (#{_seen_reset_count})")
            if _stop_bot_at_resets is not None and _seen_reset_count >= _stop_bot_at_resets:
                print("⏹ Достигнут лимит сбросов seen — остановка бота")
                await client.close()
                return
            if _shutdown_at_resets is not None and _seen_reset_count >= _shutdown_at_resets:
                print("⏻ Достигнут лимит сбросов seen — выключение устройства")
                os.system("shutdown /s /t 0")
                return
        try:
            skins = await asyncio.to_thread(get_recommended_skins)
        except Exception as e:
            print(f"Ошибка: {e}")
            await asyncio.sleep(30)
            continue

        for item in skins:
            title = item.get("title", "N/A")
            price = item.get("price", 0.0)

            if price > 1000:
                continue

            if title in seen:
                continue

            seen.add(title)

            # Премиум: по-бакетный арбитраж по износу (ножи/перчатки ★) → premium-канал
            if title.startswith("★"):
                premium_blocks = await analyze_premium(title)
                if premium_blocks:
                    premium_channel = client.get_channel(PREMIUM_CHANNEL_ID)
                    if premium_channel:
                        for text, bucket, top_t in premium_blocks:  # отдельное сообщение на бакет (лимит 2000)
                            await premium_channel.send(content=text, view=PremiumTargetView(title, bucket, top_t))

            min_offer, max_target = await asyncio.to_thread(get_aggregated_prices, title)
            if min_offer is None or max_target is None or max_target == 0:
                continue

            fee = fees.fee_fraction(title, min_offer) if min_offer is not None else 0.10
            net = min_offer * (1 - fee) - max_target
            if net < 3:
                continue

            sales = await asyncio.to_thread(get_last_sales, title, 30)
            if not sales:
                continue

            info = build_skin_info(title, sales, min_offer, max_target, get_buy_price(title))
            if info["avg_offer"] is None:
                continue

            profit = info["profit"]
            liquid = info["liq"]["ok"]
            lines = info["lines"]

            # не-премиум: верхние таргеты должны быть примерно одной цены (топ не выброс)
            if liquid and not title.startswith("★"):
                orders = await asyncio.to_thread(get_targets_by_title, title)
                orders = [o for o in orders if o.get("attributes", {}).get("floatPartValue") == "any"] 
                clustered, top_levels = _targets_top_clustered(orders)
                top_s = " / ".join(f"${p:.2f}" for p in top_levels)
                lines.append(f"   Топ-таргеты: {'🟢' if clustered else '🔴'} {top_s or '—'}")
                if not clustered:
                    liquid = False

            # Маршрутизация сигнала — взаимоисключающая, без дабл-постов:
            #   profit>=2 и ликвид  → авто-таргет + liquid_profit
            #   profit>0 (иначе)    → profit-канал
            #   ликвид, profit<=0   → review (пограничные); неликвид-мусор не постим
            if profit >= 2 and liquid:
                auto_price = round(max_target + 0.02, 2)
                balance = await asyncio.to_thread(get_balance)
                within_limit = balance is not None and auto_price <= (balance if auto_price <= min_balance else balance * 0.70)

                if within_limit and auto_price > 4:
                    ok, _, new_id = await asyncio.to_thread(place_target, title, auto_price)
                    placed = auto_price if (ok and new_id) else None
                else:
                    ok, placed, new_id = False, None, None

                # поставлен авто-таргет → только неактивная кнопка с ценой; иначе — рабочие кнопки
                liquid_view = (
                    LinkOnlyView(title=title, placed_price=placed)
                    if placed
                    else LinkButton(title=title, max_target=max_target)
                )
                await liquid_profit_channel.send(content="\n".join(lines), view=liquid_view)

                if placed and new_id:
                    placed_targets.append(title)  # добавляем в кэш выставленных таргетов, чтобы не предлагать ставить второй раз в scan_loop
                    targets_channel = client.get_channel(TARTGETS_CHANNEL_ID)
                    if targets_channel:
                        await targets_channel.send(
                            embed=target_embed(title, auto_price, client.user),
                            view=DeleteTargetView(target_id=new_id),
                        )
            elif profit > 0:
                await profit_channel.send(content="\n".join(lines), view=LinkButton(title=title, max_target=max_target))
            elif liquid:
                await review_channel.send(content="\n".join(lines), view=LinkButton(title=title, max_target=max_target))




async def rebid_loop():
    await client.wait_until_ready()
    while not client.is_closed():
        await asyncio.sleep(910)

        # Новые закрытые (купленные) таргеты → пишем цену покупки в JSON + сигнал
        all_closed = await asyncio.to_thread(get_closed_targets)
        new_closed = [t for t in all_closed if t.get("TargetID", "") not in _synced_closed_ids]
        if new_closed:
            await asyncio.to_thread(sync_closed_targets, new_closed)
            closed_channel = client.get_channel(CLOSED_TARGETS_CHANNEL_ID)
            for t in new_closed:
                _synced_closed_ids.add(t.get("TargetID", ""))
                title = t.get("Title", "")
                price = float(t.get("Price", {}).get("Amount", 0))
                if closed_channel and title and price > 0:
                    embed = discord.Embed(title="🛒 Куплен по таргету", color=discord.Color.green())
                    embed.add_field(name="Скин", value=f"**{title}**", inline=False)
                    embed.add_field(name="Цена покупки", value=f"**${price:.2f}**", inline=True)
                    await closed_channel.send(embed=embed, view=SkinInfoView(title=title))

        active = await asyncio.to_thread(get_user_targets, "TARGET_STATUS_ACTIVE")
        for t in active:
            title = t.get("title", "")
            my_price = int(t.get("priceCents", 0)) / 100
            target_id = t.get("id") or t.get("targetId") or t.get("TargetID")
            if not title or not target_id or title in placed_targets:
                continue

            min_offer, max_target = await asyncio.to_thread(get_aggregated_prices, title)
            if min_offer is None or max_target is None or max_target == 0:
                continue

            if max_target <= my_price:
                continue  # меня не перебили

            fee = fees.fee_fraction(title, min_offer) if min_offer is not None else 0.10
            net = min_offer * (1 - fee) - max_target
            if net < 2:
                await asyncio.to_thread(delete_target, target_id)
                continue # уже не выгодно перебивать

            new_price = round(max_target + 0.02, 2)
            ok_del, _ = await asyncio.to_thread(delete_target, target_id)
            if not ok_del:
                continue

            ok_place, place_err, new_id = await asyncio.to_thread(place_target, title, new_price)
            if ok_place and new_id:
                channel = client.get_channel(TARTGET_UPDATE_CHANNEL_ID)
                if channel:
                    await channel.send(
                        embed=rebid_embed(title, my_price, new_price, net),
                        view=DeleteTargetView(target_id=new_id),
                    )

        placed_targets.clear()  # сбрасываем кэш выставленных таргетов, чтобы не мешал в следующем цикле


def _undercut_cents(min_offer: float) -> int:
    """Цена для оффера: на 1 цент ниже минимального оффера на маркете."""
    return int(round(min_offer * 100)) - 1


TRADE_PROTECT_SECONDS = 72 * 3600   # 72 часов: дольше — мой предмет «залип» под защитой
# Ручной skip-список репрайса (правится вручную, читается свежим при каждом вызове).
REPRICE_SKIP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reprice_skip.json")


def _reprice_skip() -> set:
    """Скины, которые НЕ репрайсим — ручной список точных title в reprice_skip.json."""
    try:
        with open(REPRICE_SKIP_PATH, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return set()


async def _reprice_offer(channel, title, offer_id, asset_id, my_price, my_lock=0.0):
    """Подгоняет цену оффера под минимум среди чужих офферов (competitor−1¢):
    вниз если меня перебили, вверх если я стою дешевле рынка. Только если выгодно.

    Если мой предмет под trade-protection дольше 60 часов — ориентируемся только на
    конкурентов с МЕНЬШЕЙ защитой (их предметы привлекательнее, надо быть дешевле их)."""
    if title in _reprice_skip():
        return  # скин в ручном skip-списке — не трогаем
    buy_price = get_buy_price(title)
    if buy_price is None:
        return  # неизвестна цена последнего таргета — не трогаем

    # минимальная цена среди чужих офферов (свой исключаем по offer_id)
    offers = await asyncio.to_thread(get_market_offers, title)
    lock_distance = 32 * 3600 
    if my_lock > TRADE_PROTECT_SECONDS:
        if my_lock > 120 * 3600:
           lock_distance = 48 * 3600 
        competitor = next((p for p, oid, lock in offers if oid != offer_id and lock + lock_distance < my_lock), None)
    else:
        competitor = next((p for p, oid, lock in offers if oid != offer_id), None)
    if competitor is None or competitor <= 0:
        return

    price_cents = _undercut_cents(competitor)
    new_price = price_cents / 100
    if new_price <= 0 or new_price == my_price:
        return

    frac = fees.fee_fraction(title, new_price)
    net = new_price * (1 - frac) - buy_price
    if net <= 0:
        return  # доход против последнего таргета отрицательный — не двигаем

    ok, err, _ = await asyncio.to_thread(update_offer, offer_id, asset_id, price_cents)
    if ok:
        if channel:
            await channel.send(embed=offer_update_embed(
                title, my_price, new_price, buy_price, net, my_lock / 3600, frac,
                competitor=competitor, market=offers, my_offer_id=offer_id,
            ))
    else:
        print(f"⚠️ offer_update: '{title}': {err}")


async def _list_unlisted(channel, title, asset_id):
    """Выставляет незалистенный предмет на продажу по min_offer−1¢, если выгодно."""
    buy_price = get_buy_price(title)

    min_offer, _ = await asyncio.to_thread(get_aggregated_prices, title)
    if min_offer is None or min_offer <= 0.01:
        return

    price_cents = _undercut_cents(min_offer)
    new_price = price_cents / 100
    if new_price <= 0:
        return

    frac = fees.fee_fraction(title, new_price)

    if buy_price is None:
        ok, err, _ = await asyncio.to_thread(create_offer, asset_id, price_cents)
        if ok:
            if channel:
                await channel.send(embed=offer_create_no_buy_embed(title, new_price, new_price * (1 - frac), frac))
        else:
            print(f"⚠️ offer_create: '{title}': {err}")
        return

    net = new_price * (1 - frac) - buy_price
    if net <= 0:
        return

    ok, err, _ = await asyncio.to_thread(create_offer, asset_id, price_cents)
    if ok:
        if channel:
            await channel.send(embed=offer_create_embed(title, new_price, buy_price, net, frac))
    else:
        print(f"⚠️ offer_create: '{title}': {err}")


async def offer_update_loop():
    await client.wait_until_ready()
    while not client.is_closed():
        await asyncio.sleep(905)  # 15 минут
        channel = client.get_channel(OFFER_UPDATE_CHANNEL_ID)

        # Кэш комиссий обновляем не чаще раза в час (таблица большая, меняется редко)
        if fees.is_stale():
            count = fees.update(await asyncio.to_thread(get_customized_fees))
            if count:
                print(f"♻️ Кэш комиссий обновлён: {count} льготных")

        # Новые закрытые офферы (продажи) → сигнал «💸 Скин продан» с фактической комиссией
        all_closed = await asyncio.to_thread(get_closed_offers)
        new_closed = [t for t in all_closed if t.get("OfferID", "") not in _synced_closed_offer_ids]
        if new_closed:
            sold_channel = client.get_channel(CLOSED_OFFERS_CHANNEL_ID)
            for t in new_closed:
                _synced_closed_offer_ids.add(t.get("OfferID", ""))
                title = t.get("Title", "")
                price = float(t.get("Price", {}).get("Amount", 0))
                fee_obj = t.get("Fee") or {}
                fee_amount = float((fee_obj.get("Amount") or {}).get("Amount", 0))
                try:
                    fee_percent = float(fee_obj.get("Percent", 0))
                except (TypeError, ValueError):
                    fee_percent = 0.0
                net = price - fee_amount
                buy_price = get_buy_price(title)
                if sold_channel and title and price > 0:
                    await sold_channel.send(embed=offer_sold_embed(title, price, fee_amount, fee_percent, net, t.get("Status", ""), buy_price))

        # 1) обновляем цены уже выставленных офферов
        offers = await asyncio.to_thread(get_user_offers)
        for offer in offers:
            title = offer.get("title", "")
            offer_id = offer.get("offerId")
            asset_id = offer.get("assetId")
            my_lock = offer.get("lock", 0.0)   # сколько мой предмет ещё под защитой, сек
            my_price = offer.get("price", 0.0)
            if not title or not offer_id or not asset_id or my_price <= 0:
                continue
            await _reprice_offer(channel, title, offer_id, asset_id, my_price, my_lock)

        # 2) выставляем задепонированные, но ещё не выставленные предметы
        inventory = await asyncio.to_thread(get_user_inventory)
        for it in inventory:
            if it.get("inMarket"):
                attr = it.get("attributes", {})
                asset_id = attr.get("id", "")
                title = attr.get("title", "")
                if not title or not DEPOSITED_RE.match(asset_id):
                    continue  # Steam-предмет / не депонирован — batchCreate недоступен
                await _list_unlisted(channel, title, asset_id)


async def _notify_owner(text: str) -> None:
    """Сообщение владельцу: сначала ЛС, при неудаче — пинг в первом доступном канале."""
    if not OWNER_USER_ID:
        return
    try:
        owner = await client.fetch_user(OWNER_USER_ID)
        await owner.send(text)
        print("📩 Владельцу отправлено в ЛС")
        return
    except Exception as e:
        print(f"⚠️ ЛС не прошло ({e}); пробую упомянуть в канале")


@client.event
async def on_ready():
    global _synced_closed_ids, _synced_closed_offer_ids
    print(f"✅ Бот запущен как {client.user}")

    # Уведомление владельцу при старте: сначала ЛС, при неудаче — пинг в канале
    await _notify_owner("Иди к успеху мужик")

    # Проверка JWT при старте — токен короткоживущий, протухает периодически
    if await asyncio.to_thread(jwt_is_valid):
        print("🔑 JWT валиден")
    else:
        print("🔑 ВНИМАНИЕ: JWT протух/невалиден (401). Обнови DMARKET_JWT в .env и перезапусти бота — приватные функции (офферы, таргеты, инвентарь) работать не будут.")

    # Стартовая синхронизация цен покупки из закрытых таргетов
    closed = await asyncio.to_thread(get_closed_targets)
    count = await asyncio.to_thread(sync_closed_targets, closed)
    _synced_closed_ids = {t.get("TargetID", "") for t in closed}
    print(f"📥 Синхронизировано закрытых таргетов: {count}")

    # Запоминаем уже закрытые офферы (продажи), чтобы не слать сигналы по старым
    closed_offers = await asyncio.to_thread(get_closed_offers)
    _synced_closed_offer_ids = {t.get("OfferID", "") for t in closed_offers}
    print(f"📥 Закрытых офферов (продаж) при старте: {len(_synced_closed_offer_ids)}")

    # Кэш комиссий на продажу (для реального net в офферах)
    fee_count = fees.update(await asyncio.to_thread(get_customized_fees))
    print(f"📥 Загружено льготных комиссий: {fee_count}")

    client.add_view(TargetsControlView())
    client.add_view(OffersControlView())
    client.add_view(InventoryControlView())
    client.add_view(ControlPanelView())

    my_targets_channel = client.get_channel(MY_TARGETS_CHANNEL_ID)
    if my_targets_channel:
        async for msg in my_targets_channel.history(limit=2):
            if msg.author == client.user and msg.content == "🎯 **Управление таргетами**":
                await msg.delete()
                break
        await my_targets_channel.send("🎯 **Управление таргетами**", view=TargetsControlView())

    my_offers_channel = client.get_channel(MY_OFFERS_CHANNEL_ID)
    if my_offers_channel:
        async for msg in my_offers_channel.history(limit=2):
            if msg.author == client.user and msg.content == "📦 **Мои офферы**":
                await msg.delete()
                break
        await my_offers_channel.send("📦 **Мои офферы**", view=OffersControlView())

    control_channel = client.get_channel(CONTROL_CHANNEL_ID)
    if control_channel:
        async for msg in control_channel.history(limit=5):
            if msg.author == client.user and msg.content == "🎛 **Управление**":
                await msg.delete()
                break
        await control_channel.send("🎛 **Управление**", view=ControlPanelView())

    my_inventory_channel = client.get_channel(MY_INVENTORY_CHANNEL_ID)
    if my_inventory_channel:
        async for msg in my_inventory_channel.history(limit=10):
            if msg.author == client.user:
                await msg.delete()
        await my_inventory_channel.send("🎒 **Инвентарь**", view=InventoryControlView())
        items, balance = await asyncio.gather(
            asyncio.to_thread(get_user_inventory),
            asyncio.to_thread(get_balance),
        )
        await my_inventory_channel.send(embed=inventory_embed(items, balance))

    asyncio.ensure_future(scan_loop())
    asyncio.ensure_future(rebid_loop())
    asyncio.ensure_future(offer_update_loop())


def format_targets(items: list) -> str:
    if not items:
        return "Нет таргетов."
    lines = []
    for t in items:
        price = int(t.get("priceCents", 0)) / 100
        status = t.get("status", "")
        status_emoji = "🟢" if status == "TARGET_STATUS_ACTIVE" else "🔴"
        lines.append(f"{status_emoji} **{t.get('title')}** — **${price:.2f}**")
    return "\n".join(lines)


def _closed_offer_net(t: dict) -> float:
    """Чистыми с закрытого оффера (продажи): Price − фактический Fee."""
    price = float(t.get("Price", {}).get("Amount", 0))
    fee = float(((t.get("Fee") or {}).get("Amount") or {}).get("Amount", 0))
    return price - fee


def format_offers(items: list, balance: float | None = None, closed: list | None = None) -> str:
    lines = []
    total_net = 0.0
    if not items:
        lines.append("Нет активных офферов.")
    else:
        total_price = 0.0
        for item in items:
            title = item.get("title", "N/A")
            price = item.get("price", 0.0)
            frac = fees.fee_fraction(title, price)
            net = price * (1 - frac)
            total_price += price
            total_net += net
            lines.append(f"🟡 **{title}** — **${price:.2f}** → чистыми **${net:.2f}** (fee {frac * 100:.0f}%)")
        lines.append("")
        lines.append(f"💰 **Итого офферы:** **${total_price:.2f}** → чистыми **${total_net:.2f}**")

    pending = sum(_closed_offer_net(t) for t in (closed or []) if t.get("Status") == "trade_protected")
    if closed is not None:
        lines.append(f"🔒 **На trade-protected:** **${pending:.2f}** (с продаж)")
    if balance is not None:
        lines.append(f"🏦 **Баланс:** **${balance:.2f}**")
    if balance is not None and closed is not None:
        lines.append(f"📊 **Всего ({total_net:.2f} + {pending:.2f} + {balance:.2f}):  ** **${total_net + pending + balance:.2f}**")
    return "\n".join(lines)


@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.content == "!стоп":
        await message.channel.send("⏹ Остановлен")
        await client.close()


client.run(TOKEN)
