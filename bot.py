import discord
import os
import asyncio
import requests.utils
from collections import Counter
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

KYIV = timezone(timedelta(hours=3))
from dmarket import get_recommended_skins, get_aggregated_prices, get_last_sales, place_target, delete_target, get_user_targets, get_user_offers, get_user_inventory, get_balance
from embed import target_embed, my_targets_embed, inventory_embed, rebid_embed

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1510652051749732544
PROFIT_CHANNEL_ID = 1511801054918869196
REVIEW_CHANNEL_ID = 1511801444825698404
LIQUID_PROFIT_CHANNEL_ID = 1512393920078680145

MY_INVENTORY_CHANNEL_ID = 1513469237308559410
MY_TARGETS_CHANNEL_ID = 1513236279905616054
MY_OFFERS_CHANNEL_ID = 1513236353251414186
TARTGET_UPDATE_CHANNEL_ID = 1513236453138890762
TARTGETS_CHANNEL_ID = 1513234981269278750

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
        items = await asyncio.to_thread(get_user_offers)
        await interaction.followup.send(f"📦 Офферы ({len(items)}):\n{format_offers(items)}", ephemeral=True)


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


async def scan_loop():
    await client.wait_until_ready()
    profit_channel = client.get_channel(PROFIT_CHANNEL_ID)
    review_channel = client.get_channel(REVIEW_CHANNEL_ID)
    liquid_profit_channel = client.get_channel(LIQUID_PROFIT_CHANNEL_ID)
    seen: set[str] = set()
    seen_reset_at = asyncio.get_event_loop().time()

    while not client.is_closed():
        if asyncio.get_event_loop().time() - seen_reset_at >= 7200:
            seen.clear()
            seen_reset_at = asyncio.get_event_loop().time()
            print("♻️ seen сброшен")
        try:
            skins = await asyncio.to_thread(get_recommended_skins)
        except Exception as e:
            print(f"Ошибка: {e}")
            continue

        for item in skins:
            title = item.get("title", "N/A")
            price = int(item["price"].get("USD", 0)) / 100

            if price > 100:
                continue

            if title in seen:
                continue

            seen.add(title)

            min_offer, max_target = await asyncio.to_thread(get_aggregated_prices, title)
            if min_offer is None or max_target is None or max_target == 0:
                continue

            net = min_offer * 0.90 - max_target
            if net < 1 or net > 10:
                continue

            sales = await asyncio.to_thread(get_last_sales, title, 10)
            if not sales:
                continue

            day_counts = Counter()
            for s in sales:
                day = datetime.fromtimestamp(int(s.get("date", 0)), tz=KYIV).strftime("%d %b %Y")
                day_counts[day] += 1

            liquid = any(count >= 3 for count in day_counts.values())

            offer_prices = [float(s["price"]) for s in sales if s.get("txOperationType") == "Offer" and s.get("price")]
            target_prices = [float(s["price"]) for s in sales if s.get("txOperationType") == "Target" and s.get("price")]
            avg_offer = sum(offer_prices) / len(offer_prices) if offer_prices else None

            if avg_offer is None:
                continue

            profit = avg_offer * 0.90 - max_target
            emoji = "🟢" if profit > 0 else "🔴"

            lines = [
                f"{emoji} **{title}**",
                f"   ОФФЕР: **${min_offer:.2f}** | ТАРГЕТ: **${max_target:.2f}** | Прибыль: **${net:.2f}**",
            ]
            for s in sales:
                ts = int(s.get("date", 0))
                dt = datetime.fromtimestamp(ts, tz=KYIV)
                date_str = dt.strftime("%d %b %Y %H:%M")
                lines.append(f"   `{s.get('txOperationType', '?'):<7}` ${s.get('price', '?'):<8} {date_str}")

            if offer_prices:
                lines.append(f"   Макс оффер:  **${max(offer_prices):.2f}**")
                lines.append(f"   Сред оффер:  **${avg_offer:.2f}**")
            if target_prices:
                lines.append(f"   Макс таргет: **${max(target_prices):.2f}**")

            avg_per_day = sum(day_counts.values()) / len(day_counts)
            lines.append(f"   Прибыль (сред−10%−таргет): **${profit:.2f}**")
            lines.append(f"   Сред продаж в день: **{avg_per_day:.2f}**")
            lines.append(f"   Макс продаж за день: **{max(day_counts.values())}**")

            channel = profit_channel if profit > 0 else review_channel
            await channel.send(content="\n".join(lines), view=LinkButton(title=title, max_target=max_target))

            if profit > 0 and liquid:
                auto_price = round(max_target + 0.02, 2)
                balance = await asyncio.to_thread(get_balance)
                within_limit = balance is not None and auto_price <= balance * 0.60

                if within_limit:
                    ok, _, new_id = await asyncio.to_thread(place_target, title, auto_price)
                    placed = auto_price if (ok and new_id) else None
                else:
                    ok, placed, new_id = False, None, None

                liquid_view = LinkButton(title=title, max_target=max_target) if not within_limit else LinkOnlyView(title=title, placed_price=placed)
                await liquid_profit_channel.send(content="\n".join(lines), view=liquid_view)

                if placed and new_id:
                    targets_channel = client.get_channel(TARTGETS_CHANNEL_ID)
                    if targets_channel:
                        await targets_channel.send(
                            embed=target_embed(title, auto_price, client.user),
                            view=DeleteTargetView(target_id=new_id),
                        )


async def rebid_loop():
    await client.wait_until_ready()
    while not client.is_closed():
        await asyncio.sleep(905)  
        active = await asyncio.to_thread(get_user_targets, "TARGET_STATUS_ACTIVE")
        for t in active:
            title = t.get("title", "")
            my_price = int(t.get("priceCents", 0)) / 100
            target_id = t.get("id") or t.get("targetId") or t.get("TargetID")
            if not title or not target_id:
                continue

            min_offer, max_target = await asyncio.to_thread(get_aggregated_prices, title)
            if min_offer is None or max_target is None or max_target == 0:
                continue

            if max_target <= my_price:
                continue  # меня не перебили

            net = min_offer * 0.90 - max_target
            if net < 1 or net > 10:
                await asyncio.to_thread(delete_target, target_id)
                continue # уже не выгодно перебивать

            new_price = round(max_target + 0.02, 2)
            ok_del, _ = await asyncio.to_thread(delete_target, target_id)
            if not ok_del:
                continue

            ok_place, _, new_id = await asyncio.to_thread(place_target, title, new_price)
            if ok_place and new_id:
                channel = client.get_channel(TARTGET_UPDATE_CHANNEL_ID)
                if channel:
                    await channel.send(
                        embed=rebid_embed(title, my_price, new_price, net),
                        view=DeleteTargetView(target_id=new_id),
                    )


@client.event
async def on_ready():
    print(f"✅ Бот запущен как {client.user}")
    client.add_view(TargetsControlView())
    client.add_view(OffersControlView())
    client.add_view(InventoryControlView())

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


def format_offers(items: list) -> str:
    if not items:
        return "Нет активных офферов."
    lines = []
    for item in items:
        title = item.get("Title", "N/A")
        offer = item.get("Offer") or {}
        price = float(offer.get("Price", {}).get("Amount", 0))
        fee_raw = offer.get("Fee")
        if fee_raw is not None:
            fee = float(fee_raw.get("Amount", 0)) if isinstance(fee_raw, dict) else float(fee_raw)
            net = price - fee
        else:
            net = price * 0.90
        lines.append(f"🟡 **{title}** — **${price:.2f}** → чистыми **${net:.2f}**")
    return "\n".join(lines)


@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.content == "!стоп":
        await message.channel.send("⏹ Остановлен")
        await client.close()


client.run(TOKEN)
