import discord
import os
import asyncio
import requests.utils
from dotenv import load_dotenv
from dmarket import get_recommended_skins, get_market_depth, get_last_sales_raw

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1510652051749732544
PROFIT_CHANNEL_ID = 1511801054918869196
REVIEW_CHANNEL_ID = 1511801444825698404

intents = discord.Intents.default()
client = discord.Client(intents=intents)


class LinkButton(discord.ui.View):
    def __init__(self, title: str):
        super().__init__(timeout=180)
        self.title = title

    @discord.ui.button(label="🔗 Открыть на DMarket", style=discord.ButtonStyle.secondary)
    async def get_link(self, interaction: discord.Interaction, _button: discord.ui.Button):
        link = (
            "https://dmarket.com/ingame-items/item-list/csgo-skins"
            f"?title={requests.utils.quote(self.title)}"
        )
        try:
            await interaction.user.send(f"<{link}>")
            await interaction.response.send_message("✅ Ссылка отправлена в ЛС!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Не могу отправить ЛС — открой личные сообщения от ботов в настройках.",
                ephemeral=True,
            )


async def scan_loop():
    await client.wait_until_ready()
    profit_channel = client.get_channel(PROFIT_CHANNEL_ID)
    review_channel = client.get_channel(REVIEW_CHANNEL_ID)
    seen: set[str] = set()

    while not client.is_closed():
        try:
            skins = await asyncio.to_thread(get_recommended_skins)
        except Exception as e:
            print(f"Ошибка: {e}")
            await asyncio.sleep(5)
            continue

        for item in skins:
            title = item.get("title", "N/A")
            price = int(item["price"].get("USD", 0)) / 100

            if price > 100:
                continue

            if title in seen:
                continue

            orders = await asyncio.to_thread(get_market_depth, title)
            if not orders:
                continue

            max_target = int(orders[0]["price"]) / 100
            if max_target == 0:
                continue

            net = price * 0.93 - max_target
            if net < 1 or net > 10:
                continue

            seen.add(title)
            diff = price - max_target

            sales = await asyncio.to_thread(get_last_sales_raw, title, 10)
            offer_prices = [float(s["price"]) for s in sales if s.get("txOperationType") == "Offer" and s.get("price")] if sales else []
            target_prices = [float(s["price"]) for s in sales if s.get("txOperationType") == "Target" and s.get("price")] if sales else []
            avg_offer = sum(offer_prices) / len(offer_prices) if offer_prices else None
            max_target_hist = max(target_prices) if target_prices else None
            if avg_offer is None or max_target_hist is None:
                continue
            profit = avg_offer * 0.93 - max_target_hist

            emoji = "🟢" if profit > 0 else "🔴"

            lines = [
                f"{emoji} **{title}**",
                f"   ОФФЕР: **${price:.2f}** | ТАРГЕТ: **${max_target:.2f}** | Прибыль: **${net:.2f}**",
            ]
            if sales:
                for s in sales:
                    lines.append(f"   `{s.get('txOperationType', '?'):<7}` ${s.get('price', '?')}")
            if offer_prices:
                lines.append(f"   Макс оффер:  **${max(offer_prices):.2f}**")
                lines.append(f"   Сред оффер:  **${avg_offer:.2f}**")
            if target_prices:
                lines.append(f"   Макс таргет: **${max_target_hist:.2f}**")
            if profit <= -5:
                continue
            lines.append(f"   Прибыль (сред−7%−таргет): **${profit:.2f}**")

            channel = profit_channel if emoji == "🟢" else review_channel
            await channel.send(content="\n".join(lines), view=LinkButton(title=title))
            await asyncio.sleep(0.5)

        await asyncio.sleep(5)


@client.event
async def on_ready():
    print(f"✅ Бот запущен как {client.user}")
    asyncio.ensure_future(scan_loop())


@client.event
async def on_message(message):
    if message.author == client.user:
        return
    if message.content == "!стоп":
        await message.channel.send("⏹ Остановлен")
        await client.close()


client.run(TOKEN)
