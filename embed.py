import discord
from datetime import datetime


def stats_embed(s: dict) -> discord.Embed:
    """Отчёт по сделкам за период (данные из db.stats)."""
    embed = discord.Embed(title=f"📈 Статистика за {s['period_days']} дн.", color=discord.Color.blurple())
    b, o, p = s["buys"], s["sales"], s["profit"]
    embed.add_field(
        name="🛒 Покупки (таргеты)",
        value=f"**{b['count']}** шт. на **${b['sum']:.2f}**",
        inline=False,
    )
    embed.add_field(
        name="💸 Продажи (офферы)",
        value=(
            f"**{o['count']}** шт. на **${o['gross']:.2f}**\n"
            f"комиссии **${o['fees']:.2f}** → чистыми **${o['net']:.2f}**\n"
            f"🔒 из них trade-protected: **{o['trade_protected']}** шт. (**${o['pending_net']:.2f}**)"
        ),
        inline=False,
    )
    sign = "🟢" if p["realized"] >= 0 else "🔴"
    embed.add_field(
        name="📊 Прибыль (продажи с известной ценой покупки)",
        value=f"**{p['count']}** шт. → {sign} **${p['realized']:.2f}**",
        inline=False,
    )
    if s["top_sales"]:
        lines = [
            f"{'🟢' if t['profit'] >= 0 else '🔴'} **{t['title']}** — чистыми ${t['net']:.2f}, прибыль **${t['profit']:.2f}**"
            for t in s["top_sales"]
        ]
        embed.add_field(name="🏆 Топ продаж по прибыли", value="\n".join(lines), inline=False)
    embed.set_footer(text=f"период с {datetime.fromtimestamp(s['since']).strftime('%d.%m.%Y %H:%M')}")
    return embed


def rebid_embed(title: str, old_price: float, new_price: float, net: float) -> discord.Embed:
    embed = discord.Embed(title="🔄 Авто-перебивка", color=discord.Color.orange())
    embed.add_field(name="Скин", value=f"**{title}**", inline=False)
    embed.add_field(name="Было", value=f"**${old_price:.2f}**", inline=True)
    embed.add_field(name="Стало", value=f"**${new_price:.2f}**", inline=True)
    embed.add_field(name="Net", value=f"**${net:.2f}**", inline=True)
    return embed


def offer_sold_embed(title: str, price: float, fee_amount: float, fee_percent: float, net: float, status: str,
                     buy_price: float | None = None) -> discord.Embed:
    pending = status == "trade_protected"
    embed = discord.Embed(
        title="💸 Скин продан",
        color=discord.Color.orange() if pending else discord.Color.green(),
    )
    embed.add_field(name="Скин", value=f"**{title}**", inline=False)
    embed.add_field(name="Цена продажи", value=f"**${price:.2f}**", inline=True)
    embed.add_field(name="Комиссия", value=f"**${fee_amount:.2f}** ({fee_percent * 100:.0f}%)", inline=True)
    embed.add_field(name="Чистыми", value=f"**${net:.2f}**", inline=True)
    if buy_price is not None:
        profit = net - buy_price
        sign = "🟢" if profit >= 0 else "🔴"
        embed.add_field(name="Покупка", value=f"**${buy_price:.2f}**", inline=True)
        embed.add_field(name="Прибыль (с учётом покупки)", value=f"{sign} **${profit:.2f}**", inline=True)
    else:
        embed.add_field(name="Прибыль (с учётом покупки)", value="— (цена покупки неизвестна)", inline=True)
    status_label = "🔒 Trade-protected (деньги в ожидании)" if pending else "✅ Завершена"
    embed.add_field(name="Статус", value=status_label, inline=False)
    return embed


def offer_update_embed(title: str, old_price: float, new_price: float, buy_price: float, net: float, my_lock: float, fee: float = 0.10,
                       competitor: float | None = None, market: list | None = None, my_offer_id: str | None = None) -> discord.Embed:
    if new_price > old_price:
        head, color = "📈 Цена оффера поднята", discord.Color.green()
    else:
        head, color = "📉 Цена оффера снижена", discord.Color.gold()

    embed = discord.Embed(title=head, color=color)
    embed.add_field(name="Скин", value=f"**{title}**", inline=False)
    embed.add_field(name="Было", value=f"**${old_price:.2f}**", inline=True)
    embed.add_field(name="Стало", value=f"**${new_price:.2f}**", inline=True)
    if competitor is not None:
        embed.add_field(name="Мин. конкурент", value=f"**${competitor:.2f}**", inline=True)
    embed.add_field(name="Покупка", value=f"**${buy_price:.2f}**", inline=True)
    embed.add_field(name="Комиссия", value=f"**{fee * 100:.0f}%**", inline=True)
    embed.add_field(name="Чистыми", value=f"**${net:.2f}**", inline=True)
    if market:
        lines = []
        for price, oid, *_ in market[:6]:
            mark = "  ← мой" if oid == my_offer_id else ""
            lines.append(f"${price:.2f}{mark}")
        lines.append(f"🔒 Trade Lock: {f'{int(my_lock)} часов' if my_lock < 24 else f'{int(my_lock // 24)}д {int(my_lock % 24)}ч'}")
        embed.add_field(name="Рынок (топ-6 ↑)", value="\n".join(lines), inline=False)
    return embed


def offer_create_embed(title: str, price: float, buy_price: float, net: float, fee: float = 0.10) -> discord.Embed:
    embed = discord.Embed(title="🆕 Оффер выставлен", color=discord.Color.green())
    embed.add_field(name="Скин", value=f"**{title}**", inline=False)
    embed.add_field(name="Цена", value=f"**${price:.2f}**", inline=True)
    embed.add_field(name="Покупка", value=f"**${buy_price:.2f}**", inline=True)
    embed.add_field(name="Комиссия", value=f"**{fee * 100:.0f}%**", inline=True)
    embed.add_field(name="Чистыми", value=f"**${net:.2f}**", inline=True)
    return embed


def offer_create_no_buy_embed(title: str, price: float, net: float, fee: float = 0.10) -> discord.Embed:
    embed = discord.Embed(title="🆕 Оффер выставлен (цена покупки неизвестна)", color=discord.Color.yellow())
    embed.add_field(name="Скин", value=f"**{title}**", inline=False)
    embed.add_field(name="Цена", value=f"**${price:.2f}**", inline=True)
    embed.add_field(name="Комиссия", value=f"**{fee * 100:.0f}%**", inline=True)
    embed.add_field(name="Чистыми", value=f"**${net:.2f}**", inline=True)
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
