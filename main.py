# -*- coding: utf-8 -*-
"""Single-file Discord economy/game bot. Python 3.11+, discord.py 2.x, Flask."""
import os
import json
import random
import asyncio
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands
from flask import Flask, jsonify

# ===================== CONFIG =====================
PREFIX = "t"
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
STARTING_COINS = 0
WORK_COOLDOWN = 120
DAILY_COOLDOWN = 24 * 60 * 60
DATA_FILE = Path(os.getenv("DATA_FILE", "data.json"))
PORT = int(os.getenv("PORT", "10000"))

# Flask endpoint for UptimeRobot. It is a health check, not a guarantee against Render sleep.
app = Flask(__name__)
_started_at = time.time()

@app.get("/")
def home():
    return jsonify(status="ok", service="Discord game bot", discord_ready=bot.is_ready(), uptime_seconds=int(time.time() - _started_at))

@app.get("/health")
def health():
    return jsonify(status="ok", discord_ready=bot.is_ready())

# ===================== STORAGE =====================
_lock = threading.RLock()
def _blank_data():
    return {"users": {}, "guilds": {}, "gift_codes": {}}

def load_data():
    try:
        if DATA_FILE.exists():
            obj = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                obj.setdefault("users", {})
                obj.setdefault("guilds", {})
                obj.setdefault("gift_codes", {})
                return obj
    except Exception as exc:
        print(f"[DATA] Cannot read {DATA_FILE}: {exc}")
    return _blank_data()

data = load_data()

def save_data():
    with _lock:
        temp = DATA_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(DATA_FILE)

def user_record(user_id):
    uid = str(user_id)
    with _lock:
        users = data.setdefault("users", {})
        if uid not in users:
            users[uid] = {
                "coins": STARTING_COINS,
                "last_work": 0,
                "last_daily": 0,
                "last_lixi": {},
                "stats": {"wins": 0, "losses": 0, "earned": 0, "spent": 0},
            }
        u = users[uid]
        u.setdefault("coins", STARTING_COINS)
        u.setdefault("last_work", 0)
        u.setdefault("last_daily", 0)
        u.setdefault("last_lixi", {})
        u.setdefault("stats", {"wins": 0, "losses": 0, "earned": 0, "spent": 0})
        return u

def change_coins(user_id, amount):
    u = user_record(user_id)
    with _lock:
        u["coins"] = max(0, int(u["coins"]) + int(amount))
        if amount > 0:
            u["stats"]["earned"] = int(u["stats"].get("earned", 0)) + int(amount)
        elif amount < 0:
            u["stats"]["spent"] = int(u["stats"].get("spent", 0)) + abs(int(amount))
        save_data()
        return u["coins"]

def parse_amount(raw: str) -> Optional[int]:
    """Accept 100, 1k, 1.5m, 2b, 3t."""
    try:
        s = raw.strip().lower().replace(",", "").replace("đ", "")
        mult = 1
        if s.endswith("k"):
            mult, s = 10**3, s[:-1]
        elif s.endswith("m"):
            mult, s = 10**6, s[:-1]
        elif s.endswith("b"):
            mult, s = 10**9, s[:-1]
        elif s.endswith("t"):
            mult, s = 10**12, s[:-1]
        value = int(float(s) * mult)
        return value if value > 0 else None
    except (ValueError, OverflowError):
        return None

def fmt(n):
    return f"{int(n):,}".replace(",", ".")

def is_admin_id(user_id):
    return int(user_id) in ADMIN_IDS

def admin_only():
    async def predicate(ctx):
        return is_admin_id(ctx.author.id)
    return commands.check(predicate)

# ===================== DISCORD SETUP =====================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix=commands.when_mentioned_or(PREFIX), intents=intents, help_command=None)

@bot.event
async def on_ready():
    print(f"[READY] Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"[DATA] Storage: {DATA_FILE.resolve()}")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CheckFailure):
        return await ctx.reply("❌ Bạn không có quyền dùng lệnh này.", mention_author=False)
    if isinstance(error, commands.MissingRequiredArgument):
        return await ctx.reply(f"⚠️ Thiếu tham số `{error.param.name}`. Gõ `{PREFIX}help` để xem cách dùng.", mention_author=False)
    if isinstance(error, commands.BadArgument):
        return await ctx.reply("⚠️ Tham số không hợp lệ.", mention_author=False)
    if isinstance(error, commands.CommandOnCooldown):
        seconds = max(1, int(error.retry_after))
        return await ctx.reply(f"⏳ Chờ thêm **{seconds} giây** nhé.", mention_author=False)
    print(f"[COMMAND ERROR] {repr(error)}")
    await ctx.reply("❌ Có lỗi khi chạy lệnh. Hãy thử lại sau.", mention_author=False)

# ===================== ECONOMY =====================
@bot.command(name="coin", aliases=["cash", "tien"])
async def coin(ctx, member: Optional[discord.Member] = None):
    member = member or ctx.author
    u = user_record(member.id)
    e = discord.Embed(title="💰 Ví tiền", description=f"**{member.display_name}** đang có **{fmt(u['coins'])} xu**.", color=discord.Color.gold())
    e.set_thumbnail(url=member.display_avatar.url)
    await ctx.reply(embed=e, mention_author=False)

@bot.command(name="work", aliases=["w", "lam", "lamviec"])
@commands.cooldown(1, WORK_COOLDOWN, commands.BucketType.user)
async def work(ctx):
    u = user_record(ctx.author.id)
    now = time.time()
    left = WORK_COOLDOWN - (now - float(u.get("last_work", 0)))
    if left > 0:
        ctx.command.reset_cooldown(ctx)
        return await ctx.reply(f"⏳ Còn **{int(left)} giây** nữa mới làm việc được.", mention_author=False)
    reward = random.randint(300, 500)
    u["last_work"] = now
    change_coins(ctx.author.id, reward)
    save_data()
    await ctx.reply(f"🧰 Bạn làm việc chăm chỉ và nhận **{fmt(reward)} xu**!", mention_author=False)

@bot.command(name="daily", aliases=["d"])
async def daily(ctx):
    u = user_record(ctx.author.id)
    now = time.time()
    left = DAILY_COOLDOWN - (now - float(u.get("last_daily", 0)))
    if left > 0:
        hours, rem = divmod(int(left), 3600)
        minutes = rem // 60
        return await ctx.reply(f"⏳ Daily đã nhận rồi. Quay lại sau **{hours} giờ {minutes} phút**.", mention_author=False)
    reward = 15000
    u["last_daily"] = now
    change_coins(ctx.author.id, reward)
    save_data()
    await ctx.reply(f"🎁 Nhận daily thành công: **{fmt(reward)} xu**!", mention_author=False)

@bot.command(name="give")
async def give(ctx, member: discord.Member, amount_text: str):
    amount = parse_amount(amount_text)
    if not amount:
        return await ctx.reply("Cách dùng: `tgive @người_nhận 10k`", mention_author=False)
    if member.bot or member.id == ctx.author.id:
        return await ctx.reply("❌ Không thể chuyển xu cho bot hoặc chính bạn.", mention_author=False)
    sender = user_record(ctx.author.id)
    if sender["coins"] < amount:
        return await ctx.reply("❌ Bạn không đủ xu.", mention_author=False)
    view = ConfirmTransferView(ctx.author.id, member.id, amount)
    await ctx.reply(f"Chuyển **{fmt(amount)} xu** từ {ctx.author.mention} cho {member.mention}?", view=view, mention_author=False)

class ConfirmTransferView(discord.ui.View):
    def __init__(self, sender_id, receiver_id, amount):
        super().__init__(timeout=60)
        self.sender_id, self.receiver_id, self.amount = sender_id, receiver_id, amount
        self.done = False
    async def interaction_check(self, interaction):
        if interaction.user.id != self.sender_id:
            await interaction.response.send_message("Chỉ người tạo yêu cầu mới xác nhận được.", ephemeral=True)
            return False
        return True
    @discord.ui.button(label="Xác nhận", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm(self, interaction, button):
        if self.done:
            return await interaction.response.send_message("Yêu cầu đã xử lý.", ephemeral=True)
        sender = user_record(self.sender_id)
        if sender["coins"] < self.amount:
            self.done = True
            return await interaction.response.edit_message(content="❌ Số dư không còn đủ, giao dịch bị hủy.", view=None)
        change_coins(self.sender_id, -self.amount)
        change_coins(self.receiver_id, self.amount)
        self.done = True
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(content=f"✅ Đã chuyển **{fmt(self.amount)} xu** thành công.", view=self)
    @discord.ui.button(label="Từ chối", style=discord.ButtonStyle.danger, emoji="✖️")
    async def decline(self, interaction, button):
        self.done = True
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(content="Đã hủy giao dịch; xu vẫn ở trong ví của bạn.", view=self)

@bot.command(name="lixi")
async def lixi(ctx, amount_text: str, *, message: str = "Chúc may mắn!"):
    amount = parse_amount(amount_text)
    if not amount:
        return await ctx.reply("Cách dùng: `tlixi 100k lời chúc`", mention_author=False)
    u = user_record(ctx.author.id)
    if u["coins"] < amount:
        return await ctx.reply("❌ Bạn không đủ xu để tạo lì xì.", mention_author=False)
    code = f"LX{random.randrange(10**7, 10**8)}"
    data["gift_codes"][code] = {"owner": ctx.author.id, "amount": amount, "remaining": amount, "claimed": [], "message": message[:180]}
    change_coins(ctx.author.id, -amount)
    save_data()
    view = LixiView(code)
    e = discord.Embed(title="🧧 LÌ XÌ MAY MẮN", description=f"{message[:180]}\n\nTổng quỹ: **{fmt(amount)} xu**\nBấm nút bên dưới để nhận lì xì. Mỗi người chỉ nhận một lần.", color=discord.Color.red())
    e.set_footer(text=f"Mã lì xì: {code} • Người tạo: {ctx.author.display_name}")
    await ctx.reply(embed=e, view=view, mention_author=False)

class LixiView(discord.ui.View):
    def __init__(self, code):
        super().__init__(timeout=None)
        self.code = code
    @discord.ui.button(label="Nhận lì xì", style=discord.ButtonStyle.success, emoji="🧧")
    async def claim(self, interaction, button):
        gift = data.get("gift_codes", {}).get(self.code)
        if not gift:
            return await interaction.response.send_message("Lì xì không còn tồn tại.", ephemeral=True)
        uid = str(interaction.user.id)
        if uid in gift["claimed"]:
            return await interaction.response.send_message("Bạn đã nhận lì xì này rồi.", ephemeral=True)
        if gift["remaining"] <= 0:
            return await interaction.response.send_message("Lì xì đã hết tiền.", ephemeral=True)
        # Random amount while reserving enough for at least 1 coin per remaining claimant isn't possible
        # without a fixed claimant count; this draw safely gives up to the remaining pool.
        amount = random.randint(1, max(1, int(gift["remaining"])))
        gift["remaining"] -= amount
        gift["claimed"].append(uid)
        change_coins(interaction.user.id, amount)
        save_data()
        await interaction.response.send_message(f"🎉 Bạn nhận được **{fmt(amount)} xu**! Còn **{fmt(gift['remaining'])} xu** trong quỹ.", ephemeral=True)
        if gift["remaining"] == 0:
            for child in self.children: child.disabled = True
            try: await interaction.message.edit(view=self)
            except Exception: pass

@bot.command(name="leaderboard", aliases=["lb", "top", "bxh"])
async def leaderboard(ctx):
    rows = sorted(data.get("users", {}).items(), key=lambda item: int(item[1].get("coins", 0)), reverse=True)[:10]
    if not rows:
        return await ctx.reply("Chưa có dữ liệu người chơi.", mention_author=False)
    lines = []
    for i, (uid, u) in enumerate(rows, 1):
        member = ctx.guild.get_member(int(uid)) if ctx.guild else None
        name = member.display_name if member else f"User {uid}"
        lines.append(f"**{i}.** {name} — **{fmt(u.get('coins', 0))} xu**")
    await ctx.reply(embed=discord.Embed(title="🏆 BẢNG XẾP HẠNG", description="\n".join(lines), color=discord.Color.blurple()), mention_author=False)

# ===================== GAME HELPERS =====================
async def get_bet(ctx, raw):
    amount = parse_amount(raw)
    if not amount:
        await ctx.reply("⚠️ Tiền cược không hợp lệ. Ví dụ: `10k`, `1m`, `2b`, `1t`.", mention_author=False)
        return None
    u = user_record(ctx.author.id)
    if u["coins"] < amount:
        await ctx.reply("❌ Bạn không đủ xu để cược.", mention_author=False)
        return None
    change_coins(ctx.author.id, -amount)
    return amount

async def settle(ctx, bet, payout, title, description, color=None):
    payout = max(0, int(payout))
    if payout:
        change_coins(ctx.author.id, payout)
    u = user_record(ctx.author.id)
    if payout > bet:
        u["stats"]["wins"] = int(u["stats"].get("wins", 0)) + 1
    elif payout < bet:
        u["stats"]["losses"] = int(u["stats"].get("losses", 0)) + 1
    save_data()
    e = discord.Embed(title=title, description=f"{description}\n\n💵 Cược: **{fmt(bet)} xu**\n💰 Trả về: **{fmt(payout)} xu**\n👛 Số dư: **{fmt(u['coins'])} xu**", color=color or (discord.Color.green() if payout > bet else discord.Color.red() if payout < bet else discord.Color.gold()))
    await ctx.reply(embed=e, mention_author=False)

# ===================== NO HU =====================
@bot.command(name="nohu", aliases=["nh"])
async def nohu(ctx, amount: str):
    bet = await get_bet(ctx, amount)
    if bet is None: return
    symbols = ["🍒", "🍋", "🍉", "🔔", "💎", "7️⃣"]
    reels = [random.choice(symbols) for _ in range(3)]
    if reels[0] == reels[1] == reels[2]:
        mult = {"7️⃣": 10, "💎": 7, "🔔": 5}.get(reels[0], 4)
        payout = bet * mult
        result = f"🎉 JACKPOT! {' '.join(reels)} — x{mult}!"
    elif len(set(reels)) == 2:
        payout = bet * 2
        result = f"✨ Hai biểu tượng trùng! {' '.join(reels)} — trả x2."
    else:
        payout = 0
        result = f"{' '.join(reels)}\n💨 Không trúng lần này!"
    await settle(ctx, bet, payout, "🎰 NỔ HŨ", result)

# ===================== MAY BAY =====================
@bot.command(name="maybay", aliases=["fly", "mb"])
async def maybay(ctx, amount: str):
    bet = await get_bet(ctx, amount)
    if bet is None: return
    crash = round(random.uniform(1.0, 8.0), 2) if random.random() < 0.06 else round(1 + random.expovariate(0.75), 2)
    cashout = round(random.uniform(1.1, 4.0), 2)
    if cashout < crash:
        payout = int(bet * cashout)
        result = f"✈️ Máy bay đạt **x{crash}**. Bạn rút ở **x{cashout}** thành công!"
    else:
        payout = 0
        result = f"💥 Máy bay nổ ở **x{crash}** trước khi bạn rút!"
    await settle(ctx, bet, payout, "✈️ MÁY BAY", result)

# ===================== DONAL TRUMP =====================
@bot.command(name="donaltrump", aliases=["dt", "tha"])
async def donaltrump(ctx, amount: str):
    bet = await get_bet(ctx, amount)
    if bet is None: return
    roll = random.random()
    if roll < 0.02:
        mult, line = 10, "🇺🇸 Trump thắng lớn — JACKPOT x10!"
    elif roll < 0.22:
        mult, line = 2, "🗳️ Bạn thắng cuộc bầu cử! x2"
    elif roll < 0.32:
        mult, line = 1, "🤝 Hòa! Hoàn tiền cược."
    else:
        mult, line = 0, "📉 Đối thủ thắng, bạn mất cược."
    await settle(ctx, bet, bet * mult, "🇺🇸 DONAL TRUMP", line)

# ===================== SIC BO =====================
@bot.command(name="sicbo", aliases=["sb"])
async def sicbo(ctx, amount: str, choice: str):
    bet = await get_bet(ctx, amount)
    if bet is None: return
    choice = choice.lower()
    dice = [random.randint(1, 6) for _ in range(3)]
    total = sum(dice)
    triple = len(set(dice)) == 1
    valid = {"tai", "xiu", "chan", "le", "triple"}
    if choice not in valid:
        change_coins(ctx.author.id, bet)
        return await ctx.reply("Chọn `tai`, `xiu`, `chan`, `le` hoặc `triple`. Đã hoàn lại tiền cược.", mention_author=False)
    wins = (choice == "tai" and 11 <= total <= 17 and not triple) or (choice == "xiu" and 4 <= total <= 10 and not triple) or (choice == "chan" and total % 2 == 0) or (choice == "le" and total % 2 == 1) or (choice == "triple" and triple)
    mult = 30 if choice == "triple" else 2 if wins else 0
    payout = bet * mult
    await settle(ctx, bet, payout, "🎲 SIC BO", f"Xúc xắc: **{' – '.join(map(str, dice))}** (tổng **{total}**).\nBạn chọn **{choice}** — {'THẮNG!' if wins else 'THUA!'}")

# ===================== BACCARAT =====================
@bot.command(name="bacarat", aliases=["bcrt", "baccarat"])
async def bacarat(ctx, amount: str, choice: str):
    bet = await get_bet(ctx, amount)
    if bet is None: return
    choice = choice.lower()
    if choice not in {"player", "banker", "tie", "nguoi", "nha", "hoa"}:
        change_coins(ctx.author.id, bet)
        return await ctx.reply("Chọn `player`, `banker` hoặc `tie` (cũng nhận `nguoi`, `nha`, `hoa`). Đã hoàn tiền.", mention_author=False)
    aliases = {"nguoi": "player", "nha": "banker", "hoa": "tie"}
    choice = aliases.get(choice, choice)
    p = random.randint(0, 9); b = random.randint(0, 9)
    winner = "player" if p > b else "banker" if b > p else "tie"
    mult = 8 if choice == "tie" and winner == "tie" else 2 if choice == winner and winner != "tie" else 0
    payout = bet * mult
    await settle(ctx, bet, payout, "🃏 BACCARAT", f"Player: **{p}** | Banker: **{b}**\nKết quả: **{winner.upper()}**. Bạn chọn **{choice.upper()}**.")

# ===================== KEO BO =====================
@bot.command(name="keobo", aliases=["kb"])
async def keobo(ctx, amount: str, choice: str):
    bet = await get_bet(ctx, amount)
    if bet is None: return
    choice = choice.lower()
    options = ["keo", "bua", "bao"]
    if choice not in options:
        change_coins(ctx.author.id, bet)
        return await ctx.reply("Cách dùng: `tkeobo 10k keo` (keo/bua/bao). Đã hoàn tiền.", mention_author=False)
    bot_pick = random.choice(options)
    if choice == bot_pick: payout, result = bet, "🤝 Hòa, hoàn tiền cược."
    elif (choice, bot_pick) in [("keo", "bao"), ("bua", "keo"), ("bao", "bua")]: payout, result = bet * 2, "🎉 Bạn thắng! Trả về x2."
    else: payout, result = 0, "😵 Bot thắng ván này."
    await settle(ctx, bet, payout, "✂️ KÉO BÚA BAO", f"Bạn: **{choice}** | Bot: **{bot_pick}**\n{result}")

# ===================== DOMINO =====================
@bot.command(name="domin", aliases=["domino"])
async def domin(ctx, amount: str):
    bet = await get_bet(ctx, amount)
    if bet is None: return
    you = random.randint(0, 12)
    rival = random.randint(0, 12)
    if you > rival: payout, result = bet * 2, "🎉 Bạn thắng domino!"
    elif you == rival: payout, result = bet, "🤝 Hòa, hoàn tiền cược."
    else: payout, result = 0, "😵 Đối thủ thắng."
    await settle(ctx, bet, payout, "🁣 DOMINO", f"Bạn rút **{you}** | Đối thủ **{rival}**\n{result}")

# ===================== BAU CUA =====================
@bot.command(name="baucua", aliases=["bc"])
async def baucua(ctx, amount: str, choice: str):
    bet = await get_bet(ctx, amount)
    if bet is None: return
    animals = ["bau", "cua", "tom", "ca", "ga", "nai"]
    choice = choice.lower()
    if choice not in animals:
        change_coins(ctx.author.id, bet)
        return await ctx.reply("Chọn một trong: `bau cua tom ca ga nai`. Đã hoàn tiền.", mention_author=False)
    dice = [random.choice(animals) for _ in range(3)]
    hits = dice.count(choice)
    payout = bet * (hits + 1) if hits else 0
    await settle(ctx, bet, payout, "🦀 BẦU CUA", f"Kết quả: **{' – '.join(dice)}**\nBạn chọn **{choice}**, trúng **{hits}** lần.")

# ===================== HELP / STATS =====================
@bot.command(name="help", aliases=["commands"])
async def help_cmd(ctx):
    e = discord.Embed(title="🎮 HƯỚNG DẪN BOT GAME", description="Prefix: `t` (cũng có thể nhắc tên bot).", color=discord.Color.blurple())
    e.add_field(name="💰 Kinh tế", value="`tcoin` / `tcash` / `ttien` — xem xu\n`tw` — làm việc (cooldown 120 giây, +300–500 xu)\n`tdaily` — nhận 15.000 xu mỗi 24 giờ\n`tgive @user 10k` — chuyển xu, cần xác nhận\n`tlixi 100k lời chúc` — tạo lì xì\n`tlb` — bảng xếp hạng", inline=False)
    e.add_field(name="🎲 Game cược", value="`tnohu 10k`\n`tmaybay 10k`\n`tdonaltrump 10k`\n`tsicbo 10k tai` (tai/xiu/chan/le/triple)\n`tbacarat 10k player` (player/banker/tie)\n`tkeobo 10k keo` (keo/bua/bao)\n`tdomin 10k`\n`tbaucua 10k cua` (bau/cua/tom/ca/ga/nai)", inline=False)
    e.add_field(name="🪙 Cách nhập tiền", value="Dùng `k` = nghìn, `m` = triệu, `b` = tỷ, `t` = nghìn tỷ. Ví dụ: `50k`, `1.5m`, `2b`.", inline=False)
    e.set_footer(text="Chỉ dùng xu ảo trong bot; kết quả game dựa trên may mắn.")
    await ctx.reply(embed=e, mention_author=False)

# ===================== HIDDEN ADMIN COMMANDS =====================
@bot.command(name="addcoin", hidden=True)
@admin_only()
async def addcoin(ctx, member: discord.Member, amount_text: str):
    amount = parse_amount(amount_text)
    if not amount:
        return await ctx.reply("Cách dùng: `taddcoin @user 1m`", mention_author=False)
    change_coins(member.id, amount)
    await ctx.reply(f"✅ Đã cộng **{fmt(amount)} xu** cho {member.mention}.", mention_author=False)

@bot.command(name="botlixi", hidden=True)
@admin_only()
async def botlixi(ctx, amount_text: str, *, message: str = "Lì xì từ quản trị viên!"):
    amount = parse_amount(amount_text)
    if not amount:
        return await ctx.reply("Cách dùng: `tbotlixi 100k lời chúc`", mention_author=False)
    code = f"ADM{random.randrange(10**7, 10**8)}"
    data["gift_codes"][code] = {"owner": ctx.author.id, "amount": amount, "remaining": amount, "claimed": [], "message": message[:180]}
    save_data()
    e = discord.Embed(title="🧧 LÌ XÌ CỦA BOT", description=f"{message[:180]}\nQuỹ lì xì: **{fmt(amount)} xu**\nBấm nút để nhận, mỗi người một lần.", color=discord.Color.red())
    e.set_footer(text=f"Mã: {code}")
    await ctx.reply(embed=e, view=LixiView(code), mention_author=False)

@bot.command(name="setcoin", hidden=True)
@admin_only()
async def setcoin(ctx, member: discord.Member, amount_text: str):
    amount = parse_amount(amount_text)
    if amount is None:
        return await ctx.reply("Cách dùng: `tsetcoin @user 100k`", mention_author=False)
    u = user_record(member.id)
    u["coins"] = amount
    save_data()
    await ctx.reply(f"✅ Đã đặt số dư {member.mention} thành **{fmt(amount)} xu**.", mention_author=False)

# ===================== WEB + START =====================
def run_web():
    app.run(host="0.0.0.0", port=PORT, use_reloader=False)

if __name__ == "__main__":
    if not TOKEN:
        print("[CONFIG ERROR] Set environment variable DISCORD_TOKEN in Render.")
    threading.Thread(target=run_web, daemon=True).start()
    if not TOKEN:
        # Keep the web health endpoint alive for diagnosis, but bot won't connect until token is set.
        while True:
            time.sleep(3600)
    bot.run(TOKEN, log_handler=None)
