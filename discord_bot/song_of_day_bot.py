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

# ======================
# CONFIG
# ======================

PLAYLIST_ID = "3jCw4Oamo30wY0HBMZGXPl"
POST_HOUR = 10
POST_MINUTE = 0

# ======================
# DISCORD CLIENT
# ======================

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# ======================
# SPOTIFY AUTH
# ======================

SPOTIFY_CACHE_FILE = os.path.join(BASE_DIR, "spotify_token.cache")

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

def load_used_songs():
    if not os.path.exists(USED_SONGS_FILE):
        return set()

    try:
        with open(USED_SONGS_FILE, "r") as f:
            data = json.load(f)
            if isinstance(data, list):
                return set(data)
    except json.JSONDecodeError:
        pass

    # If file is empty or corrupted, reset it safely
    with open(USED_SONGS_FILE, "w") as f:
        json.dump([], f)

    return set()


def save_used_songs(song_ids):
    with open(USED_SONGS_FILE, "w") as f:
        json.dump(list(song_ids), f)

def load_channel_config():
    if os.path.exists(CHANNEL_CONFIG_FILE):
        with open(CHANNEL_CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}

def save_channel_config(config):
    with open(CHANNEL_CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

def load_last_run():
    if os.path.exists(LAST_RUN_FILE):
        with open(LAST_RUN_FILE, "r") as f:
            return date.fromisoformat(f.read().strip())
    return None

def save_last_run(d):
    with open(LAST_RUN_FILE, "w") as f:
        f.write(d.isoformat())

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

    return [
        item["track"]
        for item in items
        if item["track"] is not None
    ]

# ======================
# MAIN ACTION
# ======================

async def song_of_the_day():
    channel_config = load_channel_config()
    if not channel_config:
        print("No channels configured.")
        return

    used_songs = load_used_songs()
    tracks = get_all_tracks()

    if not tracks:
        print("No tracks found.")
        return

    unused = [t for t in tracks if t["id"] not in used_songs]
    if not unused:
        used_songs.clear()
        unused = tracks

    song = random.choice(unused)
    used_songs.add(song["id"])
    save_used_songs(used_songs)

    song_name = song["name"]
    artist = ", ".join(a["name"] for a in song["artists"])
    album_art = song["album"]["images"][0]["url"]
    spotify_url = song["external_urls"]["spotify"]

    today = datetime.now().strftime("%A, %B %d, %Y")

    embed = discord.Embed(
        title=song_name,
        url=spotify_url,
        description=f"**Artist:** {artist}",
        color=discord.Color.blue()
    )
    embed.set_thumbnail(url=album_art)
    embed.set_footer(text="Automatically selected • No repeats")

    for guild_id, channel_id in channel_config.items():
        channel = client.get_channel(channel_id)
        if channel:
            await channel.send(
                content=f"🎶 **Abyss's song of the Day — {today}** 🎶",
                embed=embed
            )
            print(f"Posted to guild {guild_id} in channel {channel_id}")
        else:
            print(f"Channel {channel_id} not found in guild {guild_id}")

# ======================
# SCHEDULER LOOP
# ======================

@tasks.loop(seconds=10)
async def scheduler():
    now = datetime.now()
    today = now.date()
    last_run = load_last_run()

    if now.hour == POST_HOUR and now.minute == POST_MINUTE and last_run != today:
        await song_of_the_day()
        save_last_run(today)
        await asyncio.sleep(60)

# ======================
# SLASH COMMANDS
# ======================

@tree.command(
    name="setchannel",
    description="Set the channel where the Song of the Day will post"
)
@app_commands.checks.has_permissions(administrator=True)
async def setchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    config = load_channel_config()
    config[str(interaction.guild.id)] = channel.id
    save_channel_config(config)
    await interaction.response.send_message(
        f"Song of the Day channel set to {channel.mention}", ephemeral=True
    )

@tree.command(
    name="testsong",
    description="Post a test Song of the Day immediately"
)
@app_commands.checks.has_permissions(administrator=True)
async def testsong(interaction: discord.Interaction):
    await interaction.response.send_message("Posting test Song of the Day...", ephemeral=True)
    await song_of_the_day()

# ======================
# EVENTS
# ======================

@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    await tree.sync()
    print("Slash commands synced.")
    scheduler.start()

# ======================
# RUN BOT
# ======================

client.run(os.getenv("DISCORD_BOT_TOKEN"))


