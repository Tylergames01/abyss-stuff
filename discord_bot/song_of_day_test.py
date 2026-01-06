import os
import random
import json
import asyncio
from datetime import datetime, date

import discord
from discord.ext import tasks
from discord import app_commands

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

load_dotenv()

# ======================
# PATHS / STATE FILES
# ======================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

USED_SONGS_FILE = os.path.join(BASE_DIR, "used_songs.json")
CHANNEL_CONFIG_FILE = os.path.join(BASE_DIR, "channel_config.json")
LAST_RUN_FILE = os.path.join(BASE_DIR, "last_run.txt")
SPOTIFY_CACHE_FILE = os.path.join(BASE_DIR, "spotify_token.cache")
SCHEDULED_SONGS_FILE = os.path.join(BASE_DIR, "scheduled_songs.json")
ALLOWED_ROLES_FILE = os.path.join(BASE_DIR, "allowed_roles.json")

# ======================
# CONFIG
# ======================

PLAYLIST_ID = "3jCw4Oamo30wY0HBMZGXPl"
POST_HOUR = 11
POST_MINUTE = 30

# ======================
# DISCORD CLIENT
# ======================

intents = discord.Intents.default()
intents.guilds = True
intents.guild_messages = True
intents.message_content = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# ======================
# SPOTIFY AUTH
# ======================

sp = spotipy.Spotify(
    auth_manager=SpotifyOAuth(
        scope="playlist-read-private playlist-read-collaborative",
        redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
        cache_path=SPOTIFY_CACHE_FILE,
        open_browser=True
    )
)

# ======================
# STATE HELPERS
# ======================

def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def load_used_songs():
    return set(load_json(USED_SONGS_FILE, []))

def save_used_songs(data):
    save_json(USED_SONGS_FILE, list(data))

def load_channel_config():
    return load_json(CHANNEL_CONFIG_FILE, {})

def save_channel_config(data):
    save_json(CHANNEL_CONFIG_FILE, data)

def load_last_run():
    if os.path.exists(LAST_RUN_FILE):
        with open(LAST_RUN_FILE, "r") as f:
            return date.fromisoformat(f.read().strip())
    return None

def save_last_run(d):
    with open(LAST_RUN_FILE, "w") as f:
        f.write(d.isoformat())

def load_scheduled_songs():
    return load_json(SCHEDULED_SONGS_FILE, {})

def save_scheduled_songs(data):
    save_json(SCHEDULED_SONGS_FILE, data)

def load_allowed_roles():
    return load_json(ALLOWED_ROLES_FILE, {})

def save_allowed_roles(data):
    save_json(ALLOWED_ROLES_FILE, data)

# ======================
# PERMISSION CHECK
# ======================

def is_allowed(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True

    allowed_roles = load_allowed_roles().get(str(interaction.guild.id), [])
    user_roles = [role.name for role in interaction.user.roles]
    return any(role in allowed_roles for role in user_roles)

# ======================
# SPOTIFY TRACK FETCH
# ======================

def get_all_tracks():
    items = []
    offset = 0

    while True:
        page = sp.playlist_items(
            PLAYLIST_ID,
            offset=offset,
            additional_types=["track"]
        )
        items.extend(page["items"])
        if page["next"] is None:
            break
        offset += len(page["items"])

    return [i["track"] for i in items if i["track"]]

# ======================
# SONG SELECTION (AUTHORITATIVE)
# ======================

def pick_song():
    tracks = get_all_tracks()
    if not tracks:
        return None

    used = load_used_songs()
    scheduled = load_scheduled_songs()
    today = date.today()

    lookup = {t["id"]: t for t in tracks}

    # Pick earliest scheduled song <= today
    valid_dates = sorted(
        d for d in scheduled.keys()
        if date.fromisoformat(d) <= today
    )

    if valid_dates:
        chosen_date = valid_dates[0]
        track_id = scheduled.pop(chosen_date)
        save_scheduled_songs(scheduled)
        song = lookup.get(track_id)
    else:
        unused = [t for t in tracks if t["id"] not in used]
        if not unused:
            used.clear()
            unused = tracks
        song = random.choice(unused)

    if song:
        used.add(song["id"])
        save_used_songs(used)

    return song

# ======================
# MAIN ACTION
# ======================

async def song_of_the_day():
    channel_config = load_channel_config()
    if not channel_config:
        return False

    song = pick_song()
    if not song:
        return False

    embed = discord.Embed(
        title=song["name"],
        url=song["external_urls"]["spotify"],
        description=f"**Artist:** {', '.join(a['name'] for a in song['artists'])}",
        color=discord.Color.blue()
    )
    embed.set_thumbnail(url=song["album"]["images"][0]["url"])
    embed.set_footer(text="Automatically selected • No repeats")

    today_str = datetime.now().strftime("%A, %B %d, %Y")

    for channel_id in channel_config.values():
        channel = client.get_channel(channel_id)
        if channel:
            await channel.send(
                content=f"🎶 **Abyss's Song of the Day — {today_str}** 🎶",
                embed=embed
            )

    return True

# ======================
# SCHEDULER
# ======================

@tasks.loop(seconds=10)
async def scheduler():
    now = datetime.now()
    today = now.date()
    last_run = load_last_run()

    if (
        now.hour == POST_HOUR
        and now.minute == POST_MINUTE
        and now.second < 10
        and last_run != today
    ):
        await song_of_the_day()
        save_last_run(today)

# ======================
# STATUS CYCLING
# ======================

async def cycle_status():
    await client.wait_until_ready()
    statuses = [
        discord.Game(name="testing till i explode or work"),
        discord.Activity(type=discord.ActivityType.listening, name="vibing to today's song"),
        discord.Activity(type=discord.ActivityType.watching, name="watching you")
    ]
    i = 0
    while not client.is_closed():
        await client.change_presence(activity=statuses[i])
        i = (i + 1) % len(statuses)
        await asyncio.sleep(15)

# ======================
# SLASH COMMANDS
# ======================

@tree.command(name="setchannel")
async def setchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not is_allowed(interaction):
        await interaction.response.send_message("Permission denied.", ephemeral=True)
        return

    config = load_channel_config()
    config[str(interaction.guild.id)] = channel.id
    save_channel_config(config)

    await interaction.response.send_message(
        f"Song of the Day channel set to {channel.mention}",
        ephemeral=True
    )

@tree.command(name="schedule_song")
async def schedule_song(interaction: discord.Interaction, date_str: str, spotify_url: str):
    if not is_allowed(interaction):
        await interaction.response.send_message("Permission denied.", ephemeral=True)
        return

    try:
        target = date.fromisoformat(date_str)
    except ValueError:
        await interaction.response.send_message("Invalid date format (YYYY-MM-DD).", ephemeral=True)
        return

    if "/track/" not in spotify_url:
        await interaction.response.send_message("Invalid Spotify track URL.", ephemeral=True)
        return

    track_id = spotify_url.split("/track/")[1].split("?")[0]
    scheduled = load_scheduled_songs()
    scheduled[target.isoformat()] = track_id
    save_scheduled_songs(scheduled)

    await interaction.response.send_message("Song scheduled.", ephemeral=True)

@tree.command(name="unschedule_song")
async def unschedule_song(interaction: discord.Interaction, date_str: str):
    if not is_allowed(interaction):
        await interaction.response.send_message("Permission denied.", ephemeral=True)
        return

    scheduled = load_scheduled_songs()
    if date_str not in scheduled:
        await interaction.response.send_message("Nothing scheduled for that date.", ephemeral=True)
        return

    del scheduled[date_str]
    save_scheduled_songs(scheduled)
    await interaction.response.send_message("Schedule removed.", ephemeral=True)

@tree.command(name="view_schedule")
async def view_schedule(interaction: discord.Interaction):
    scheduled = sorted(load_scheduled_songs().items())
    if not scheduled:
        await interaction.response.send_message("No scheduled songs.", ephemeral=True)
        return

    lines = [f"{d} → https://open.spotify.com/track/{t}" for d, t in scheduled]
    await interaction.response.send_message("\n".join(lines), ephemeral=True)

@tree.command(name="clear_schedule")
async def clear_schedule(interaction: discord.Interaction):
    if not is_allowed(interaction):
        await interaction.response.send_message("Permission denied.", ephemeral=True)
        return

    save_scheduled_songs({})
    await interaction.response.send_message("All scheduled songs cleared.", ephemeral=True)

@tree.command(name="test_sotd")
async def test_sotd(interaction: discord.Interaction):
    if not is_allowed(interaction):
        await interaction.response.send_message("Permission denied.", ephemeral=True)
        return

    await interaction.response.send_message("Forcing Song of the Day…", ephemeral=True)
    await song_of_the_day()

# ======================
# EVENTS
# ======================

@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    await tree.sync()
    scheduler.start()
    asyncio.create_task(cycle_status())

# ======================
# RUN
# ======================

client.run(os.getenv("DISCORD_BOT_TOKEN"))
