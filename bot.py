import discord
import os
import asyncio
import requests.utils
from dotenv import load_dotenv
from dmarket import get_recommended_skins, format_lot_output

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

CHANNEL_ID = 1510652051749732544

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


class LinkButton(discord.ui.View):
    def __init__(self, title: str):
        super().__init__(timeout=180)
        self.title = title

    @discord.ui.button(label="🔗 Получить ссылку", style=discord.ButtonStyle.secondary)
    async def get_link(self, interaction: discord.Interaction, button: discord.ui.Button):
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
    channel = client.get_channel(CHANNEL_ID)
    seen: set[str] = set()
    sent_count = 0

    while not client.is_closed():
        if sent_count >= 100:
            print("✅ Отправлено 100 скинов, останавливаюсь")
            break

        try:
            skins = await asyncio.to_thread(get_recommended_skins)
        except Exception as e:
            print(f"Ошибка сканирования: {e}")
            await asyncio.sleep(5)
            continue

        for item in skins:
            if sent_count >= 100:
                break

            item_id = item.get("itemId", "")
            if item_id in seen:
                continue

            msg = await asyncio.to_thread(format_lot_output, item)
            if msg:
                seen.add(item_id)
                title = item.get("title", "N/A")
                view = LinkButton(title=title)
                await channel.send(content=msg, view=view)
                sent_count += 1
                await asyncio.sleep(0.5)


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
