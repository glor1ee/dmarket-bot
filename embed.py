import discord


def rebid_embed(title: str, old_price: float, new_price: float, net: float) -> discord.Embed:
    embed = discord.Embed(title="🔄 Авто-перебивка", color=discord.Color.orange())
    embed.add_field(name="Скин", value=f"**{title}**", inline=False)
    embed.add_field(name="Было", value=f"**${old_price:.2f}**", inline=True)
    embed.add_field(name="Стало", value=f"**${new_price:.2f}**", inline=True)
    embed.add_field(name="Net", value=f"**${net:.2f}**", inline=True)
    return embed


def offer_update_embed(title: str, old_price: float, new_price: float, buy_price: float, net: float) -> discord.Embed:
    embed = discord.Embed(title="📉 Цена оффера снижена", color=discord.Color.gold())
    embed.add_field(name="Скин", value=f"**{title}**", inline=False)
    embed.add_field(name="Было", value=f"**${old_price:.2f}**", inline=True)
    embed.add_field(name="Стало", value=f"**${new_price:.2f}**", inline=True)
    embed.add_field(name="Покупка", value=f"**${buy_price:.2f}**", inline=True)
    embed.add_field(name="Чистыми", value=f"**${net:.2f}**", inline=True)
    return embed


def offer_create_embed(title: str, price: float, buy_price: float, net: float) -> discord.Embed:
    embed = discord.Embed(title="🆕 Оффер выставлен", color=discord.Color.green())
    embed.add_field(name="Скин", value=f"**{title}**", inline=False)
    embed.add_field(name="Цена", value=f"**${price:.2f}**", inline=True)
    embed.add_field(name="Покупка", value=f"**${buy_price:.2f}**", inline=True)
    embed.add_field(name="Чистыми", value=f"**${net:.2f}**", inline=True)
    return embed


def offer_create_no_buy_embed(title: str, price: float) -> discord.Embed:
    embed = discord.Embed(title="🆕 Оффер выставлен (цена покупки неизвестна)", color=discord.Color.yellow())
    embed.add_field(name="Скин", value=f"**{title}**", inline=False)
    embed.add_field(name="Цена", value=f"**${price:.2f}**", inline=True)
    embed.add_field(name="Чистыми", value=f"**${price * 0.90:.2f}**", inline=True)
    return embed


def inventory_embed(items: list, balance: float | None = None) -> discord.Embed:
    on_market = [i for i in items if i.get("inMarket")]
    embed = discord.Embed(
        title=f"На маркете: {len(on_market)}",
        color=discord.Color.green(),
    )
    if balance is not None:
        embed.add_field(name="💰 Баланс", value=f"**${balance:.2f}**", inline=False)
    if not on_market:
        embed.description = "Ничего не выставлено."
        return embed
    lines = []
    for item in on_market:
        attr = item.get("attributes", {})
        price = float(item.get("offerRecommendedPrice", {}).get("Amount", 0))
        lines.append(f"🟢 **{attr.get('title')}** — **${price:.2f}**")
    embed.add_field(name="🎒 Предметы", value="\n".join(lines), inline=False)
    return embed


def my_targets_embed(items: list, kind: str = "all") -> discord.Embed:
    configs = {
        "all":      ("📋 Все таргеты",        discord.Color.blurple()),
        "active":   ("Активные таргеты",   discord.Color.green()),
        "inactive": ("Неактивные таргеты", discord.Color.red()),
    }
    title_prefix, color = configs.get(kind, configs["all"])
    embed = discord.Embed(title=f"{title_prefix}: {len(items)}", color=color)
    if not items:
        embed.description = "Нет таргетов."
    else:
        lines = []
        for t in items:
            price = int(t.get("priceCents", 0)) / 100
            status = t.get("status", "")
            emoji = "🟢" if status == "TARGET_STATUS_ACTIVE" else "🔴"
            lines.append(f"{emoji} **{t.get('title')}** — **${price:.2f}**")
        embed.description = "\n".join(lines)
    return embed


def target_embed(skin_title: str, price: float, user: discord.User | discord.Member) -> discord.Embed:
    embed = discord.Embed(
        title="🎯 Таргет поставлен",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Скин", value=f"**{skin_title}**", inline=False)
    embed.add_field(name="Цена", value=f"**${price:.2f}**", inline=True)
    embed.add_field(name="Кол-во", value="**1**", inline=True)
    embed.set_footer(text=f"Поставил: {user.display_name}", icon_url=user.display_avatar.url)
    return embed
