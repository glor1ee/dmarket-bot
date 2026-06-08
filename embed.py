import discord


def inventory_embed(items: list) -> discord.Embed:
    on_market = [i for i in items if i.get("inMarket")]
    embed = discord.Embed(
        title=f"На маркете: {len(on_market)}",
        color=discord.Color.green(),
    )
    if not on_market:
        embed.description = "Ничего не выставлено."
        return embed
    lines = []
    for item in on_market:
        attr = item.get("attributes", {})
        price = float(item.get("offerRecommendedPrice", {}).get("Amount", 0))
        lines.append(f"🟢 **{attr.get('title')}** — **${price:.2f}**")
    embed.description = "\n".join(lines)
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
