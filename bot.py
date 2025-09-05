import discord
from discord.ext import commands, tasks
import sqlite3
import asyncio
import re
import time
import os
from dotenv import load_dotenv

# ---------------- CONFIG ----------------
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")  # <-- Replace with your bot token
PREFIX = "!"
INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.members = True
# ----------------------------------------

# Automod / settings (customize)
bad_words = {"badword1", "badword2"}  # add words to filter (lowercase)
link_whitelist = set()  # e.g. {"youtube.com", "discord.gg"}

# Role rewards
level_roles = {
    1: "Level 1",
    10: "Level 10",
    20: "Level 20",
    40: "Level 40",
    60: "Level 60",
    80: "Level 80",
    100: "Level 100"
}

# Database setup
conn = sqlite3.connect("bot_data.db", check_same_thread=False)
c = conn.cursor()

# Create tables
c.execute("""CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER,
    guild_id INTEGER,
    xp INTEGER DEFAULT 0,
    level INTEGER DEFAULT 0,
    last_message INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, guild_id)
)""")

c.execute("""CREATE TABLE IF NOT EXISTS warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    user_id INTEGER,
    moderator_id INTEGER,
    reason TEXT,
    time INTEGER
)""")

c.execute("""CREATE TABLE IF NOT EXISTS mutes (
    guild_id INTEGER,
    user_id INTEGER,
    unmute_time INTEGER,
    PRIMARY KEY (guild_id, user_id)
)""")

conn.commit()

bot = commands.Bot(command_prefix=PREFIX, intents=INTENTS, help_command=None)

# ---------- Utilities ----------
def add_xp(guild_id: int, user_id: int, amount: int = 1):
    now = int(time.time())
    c.execute("SELECT xp, level, last_message FROM users WHERE user_id=? AND guild_id=?", (user_id, guild_id))
    row = c.fetchone()
    if row is None:
        c.execute(
            "INSERT INTO users (user_id,guild_id,xp,level,last_message) VALUES (?,?,?,?,?)",
            (user_id, guild_id, amount, 0, now),
        )
        conn.commit()
        return 0, amount, 0

    xp, level, last_msg = row
    if now - last_msg < 10:  # anti-spam
        return level, xp, level

    xp += amount
    new_level = int(xp ** 0.5)
    c.execute(
        "UPDATE users SET xp=?, level=?, last_message=? WHERE user_id=? AND guild_id=?",
        (xp, new_level, now, user_id, guild_id),
    )
    conn.commit()
    return level, xp, new_level

def get_profile(guild_id: int, user_id: int):
    c.execute("SELECT xp, level FROM users WHERE user_id=? AND guild_id=?", (user_id, guild_id))
    row = c.fetchone()
    if not row:
        return {"xp": 0, "level": 0}
    return {"xp": row[0], "level": row[1]}

def add_warning(guild_id: int, user_id: int, moderator_id: int, reason: str):
    now = int(time.time())
    c.execute(
        "INSERT INTO warnings (guild_id,user_id,moderator_id,reason,time) VALUES (?,?,?,?,?)",
        (guild_id, user_id, moderator_id, reason, now),
    )
    conn.commit()
    return c.lastrowid

def get_warnings(guild_id: int, user_id: int):
    c.execute(
        "SELECT id, moderator_id, reason, time FROM warnings WHERE guild_id=? AND user_id=? ORDER BY time DESC",
        (guild_id, user_id),
    )
    return c.fetchall()

async def warn_and_notify(member: discord.Member, moderator: discord.Member, reason: str):
    add_warning(member.guild.id, member.id, moderator.id, reason)
    try:
        await member.send(f"You were warned in **{member.guild.name}** by **{moderator}** for: {reason}")
    except Exception:
        pass

# ---------- Level Role Handling ----------
async def handle_level_up(member: discord.Member, channel: discord.TextChannel, new_level: int):
    await channel.send(f"🎉 {member.mention} You are now **level {new_level}**!")

    if new_level in level_roles:
        role_name = level_roles[new_level]
        role = discord.utils.get(member.guild.roles, name=role_name)
        if role is None:
            role = await member.guild.create_role(name=role_name)
        await member.add_roles(role, reason=f"Reached level {new_level}")

        # remove lower milestone roles
        for lvl, rname in level_roles.items():
            if lvl < new_level:
                old_role = discord.utils.get(member.guild.roles, name=rname)
                if old_role and old_role in member.roles:
                    await member.remove_roles(old_role, reason="Upgraded to higher role")

# ---------- Automod ----------
link_regex = re.compile(r"(https?://[^\s]+)")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    lowered = message.content.lower()
    for bad in bad_words:
        if bad in lowered:
            try:
                await message.delete()
            except Exception:
                pass
            await message.channel.send(f"{message.author.mention}, your message was removed (bad language).")
            add_warning(message.guild.id, message.author.id, bot.user.id, f"Bad language: {bad}")
            return

    for match in re.findall(link_regex, message.content):
        allowed = False
        for domain in link_whitelist:
            if domain in match:
                allowed = True
                break
        if not allowed:
            try:
                await message.delete()
            except Exception:
                pass
            await message.channel.send(f"{message.author.mention}, links are not allowed here.")
            add_warning(message.guild.id, message.author.id, bot.user.id, "Posted disallowed link")
            return

    # Leveling
    old_level, xp, new_level = add_xp(message.guild.id, message.author.id, amount=5)
    if new_level > old_level:
        await handle_level_up(message.author, message.channel, new_level)

    await bot.process_commands(message)

# ---------- Moderation ----------
@bot.command(name="warn")
@commands.has_permissions(kick_members=True)
async def _warn(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    add_warning(ctx.guild.id, member.id, ctx.author.id, reason)
    await ctx.send(f"{member.mention} has been warned for: {reason}")
    await warn_and_notify(member, ctx.author, reason)

@bot.command(name="warnings")
@commands.has_permissions(kick_members=True)
async def _warnings(ctx, member: discord.Member):
    rows = get_warnings(ctx.guild.id, member.id)
    if not rows:
        await ctx.send("No warnings for that user.")
        return
    embed = discord.Embed(title=f"Warnings for {member}", color=discord.Color.orange())
    for r in rows[:10]:
        wid, modid, reason, t = r
        mod = ctx.guild.get_member(modid) or f"ID {modid}"
        embed.add_field(name=f"ID {wid}", value=f"By: {mod}\nReason: {reason}\nTime: <t:{t}:F>", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="clearwarnings")
@commands.has_permissions(kick_members=True)
async def _clearwarnings(ctx, member: discord.Member):
    c.execute("DELETE FROM warnings WHERE guild_id=? AND user_id=?", (ctx.guild.id, member.id))
    conn.commit()
    await ctx.send(f"Cleared warnings for {member.mention}.")

@bot.command(name="mute")
@commands.has_permissions(manage_roles=True)
async def _mute(ctx, member: discord.Member, minutes: int = 10):
    guild = ctx.guild
    muted_role = discord.utils.get(guild.roles, name="Muted")
    if muted_role is None:
        muted_role = await guild.create_role(name="Muted", reason="Mute role")
        for ch in guild.channels:
            try:
                await ch.set_permissions(muted_role, send_messages=False, speak=False, add_reactions=False)
            except Exception:
                pass
    await member.add_roles(muted_role, reason=f"Muted by {ctx.author}")
    unmute_time = int(time.time()) + minutes * 60
    c.execute("INSERT OR REPLACE INTO mutes (guild_id,user_id,unmute_time) VALUES (?,?,?)", (guild.id, member.id, unmute_time))
    conn.commit()
    await ctx.send(f"{member.mention} has been muted for {minutes} minutes.")

@bot.command(name="unmute")
@commands.has_permissions(manage_roles=True)
async def _unmute(ctx, member: discord.Member):
    muted_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if muted_role:
        try:
            await member.remove_roles(muted_role, reason=f"Unmuted by {ctx.author}")
        except Exception:
            pass
    c.execute("DELETE FROM mutes WHERE guild_id=? AND user_id=?", (ctx.guild.id, member.id))
    conn.commit()
    await ctx.send(f"{member.mention} has been unmuted.")

@bot.command(name="kick")
@commands.has_permissions(kick_members=True)
async def _kick(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    await member.kick(reason=reason)
    await ctx.send(f"{member} was kicked. Reason: {reason}")

@bot.command(name="ban")
@commands.has_permissions(ban_members=True)
async def _ban(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    await member.ban(reason=reason)
    await ctx.send(f"{member} was banned. Reason: {reason}")

@bot.command(name="purge")
@commands.has_permissions(manage_messages=True)
async def _purge(ctx, amount: int = 10):
    deleted = await ctx.channel.purge(limit=amount + 1)
    await ctx.send(f"Deleted {len(deleted) - 1} messages.", delete_after=5)

# ---------- Level Commands ----------
@bot.command(name="profile")
async def _profile(ctx, member: discord.Member = None):
    member = member or ctx.author
    p = get_profile(ctx.guild.id, member.id)
    await ctx.send(f"{member.mention} — Level **{p['level']}** — XP **{p['xp']}**")

@bot.command(name="leaderboard")
async def _leaderboard(ctx):
    c.execute("SELECT user_id, xp, level FROM users WHERE guild_id=? ORDER BY xp DESC LIMIT 10", (ctx.guild.id,))
    rows = c.fetchall()
    if not rows:
        await ctx.send("No data yet.")
        return
    embed = discord.Embed(title="Top XP", color=discord.Color.blue())
    for i, r in enumerate(rows, start=1):
        uid, xp, level = r
        member = ctx.guild.get_member(uid)
        name = member.display_name if member else f"ID {uid}"
        embed.add_field(name=f"#{i} — {name}", value=f"Level {level} • XP {xp}", inline=False)
    await ctx.send(embed=embed)

# ---------- Background unmute ----------
@tasks.loop(seconds=30)
async def _check_unmutes():
    now = int(time.time())
    rows = c.execute("SELECT guild_id, user_id FROM mutes WHERE unmute_time<=?", (now,)).fetchall()
    for guild_id, user_id in rows:
        guild = bot.get_guild(guild_id)
        if not guild:
            c.execute("DELETE FROM mutes WHERE guild_id=? AND user_id=?", (guild_id, user_id))
            conn.commit()
            continue
        member = guild.get_member(user_id)
        muted_role = discord.utils.get(guild.roles, name="Muted")
        if member and muted_role:
            try:
                await member.remove_roles(muted_role, reason="Auto unmute")
            except Exception:
                pass
        c.execute("DELETE FROM mutes WHERE guild_id=? AND user_id=?", (guild_id, user_id))
        conn.commit()

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    if not _check_unmutes.is_running():
        _check_unmutes.start()

# ---------- Help ----------
@bot.command(name="help")
async def _help(ctx):
    embed = discord.Embed(title="Help — Commands", color=discord.Color.green())
    embed.add_field(name=f"{PREFIX}profile [user]", value="Show XP/level", inline=False)
    embed.add_field(name=f"{PREFIX}leaderboard", value="Top players", inline=False)
    embed.add_field(name=f"{PREFIX}warn <user> [reason]", value="Warn a user", inline=False)
    embed.add_field(name=f"{PREFIX}warnings <user>", value="List warnings", inline=False)
    embed.add_field(name=f"{PREFIX}mute <user> [minutes]", value="Mute a user", inline=False)
    embed.add_field(name=f"{PREFIX}unmute <user>", value="Unmute a user", inline=False)
    embed.add_field(name=f"{PREFIX}kick <user> [reason]", value="Kick a user", inline=False)
    embed.add_field(name=f"{PREFIX}ban <user> [reason]", value="Ban a user", inline=False)
    embed.add_field(name=f"{PREFIX}purge <amount>", value="Delete messages", inline=False)
    await ctx.send(embed=embed)

if __name__ == "__main__":
    bot.run(TOKEN)