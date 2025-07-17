# bot.py
import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import aiomysql
import json
import re
import time
import traceback

# Define intents
intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.message_content = True

# Initialize the bot
bot = commands.Bot(command_prefix='!', intents=intents)

# --- Global Game State Storage ---
active_tictactoe_games = {}
active_jeopardy_games = {}
active_blackjack_games = {}
active_texasholdem_games = {}

# --- Database Operations (Shared Utility Functions) ---
async def add_user_to_db_if_not_exists(guild_id: int, user_name: str, discord_id: int):
    """
    Checks if a user exists in the 'discord_users' table for a given guild.
    If not, inserts a new row for the user with default values.
    """
    db_user = os.getenv('DB_USER')
    db_password = os.getenv('DB_PASSWORD')
    db_host = os.getenv('DB_HOST')
    db_name = "serene_users"
    table_name = "discord_users"

    if not all([db_user, db_password, db_host]):
        print("Database operation failed: Missing one or more environment variables (DB_USER, DB_PASSWORD, DB_HOST).")
        return

    conn = None
    try:
        conn = await aiomysql.connect(
            host=db_host,
            user=db_user,
            password=db_password,
            db=db_name,
            charset='utf8mb4',
            autocommit=True
        )
        async with conn.cursor() as cursor:
            await cursor.execute(
                f"SELECT COUNT(*) FROM {table_name} WHERE channel_id = %s AND discord_id = %s",
                (str(guild_id), str(discord_id))
            )
            (count,) = await cursor.fetchone()

            if count == 0:
                initial_json_data = json.dumps({"warnings": {}})
                await cursor.execute(
                    f"INSERT INTO {table_name} (channel_id, user_name, discord_id, kekchipz, json_data) VALUES (%s, %s, %s, %s, %s)",
                    (str(guild_id), user_name, str(discord_id), 0, initial_json_data)
                )
                print(f"Added new user '{user_name}' (ID: {discord_id}) to '{table_name}' in guild {guild_id}.")

    except aiomysql.Error as e:
        print(f"Database operation failed for user {user_name} (ID: {discord_id}): MySQL Error: {e}")
    except Exception as e:
        print(f"Database operation failed for user {discord_id}): An unexpected error occurred: {e}")
    finally:
        if conn:
            conn.close()

async def update_user_kekchipz(guild_id: int, discord_id: int, amount: int):
    """
    Updates the kekchipz balance for a user in the database.
    """
    db_user = os.getenv('DB_USER')
    db_password = os.getenv('DB_PASSWORD')
    db_host = os.getenv('DB_HOST')
    db_name = "serene_users"
    table_name = "discord_users"

    if not all([db_user, db_password, db_host]):
        print("Database operation failed: Missing one or more environment variables (DB_USER, DB_PASSWORD, DB_HOST).")
        return

    conn = None
    try:
        conn = await aiomysql.connect(
            host=db_host,
            user=db_user,
            password=db_password,
            db=db_name,
            charset='utf8mb4',
            autocommit=True
        )
        async with conn.cursor() as cursor:
            await cursor.execute(
                f"SELECT kekchipz FROM {table_name} WHERE channel_id = %s AND discord_id = %s",
                (str(guild_id), str(discord_id))
            )
            result = await cursor.fetchone()
            
            current_kekchipz = result[0] if result else 0
            new_kekchipz = current_kekchipz + amount

            await cursor.execute(
                f"UPDATE {table_name} SET kekchipz = %s WHERE channel_id = %s AND discord_id = %s",
                (new_kekchipz, str(guild_id), str(discord_id))
            )
            print(f"Updated kekchipz for user {discord_id} in guild {guild_id}: {current_kekchipz} -> {new_kekchipz}")

    except aiomysql.Error as e:
        print(f"Database update failed for user {discord_id}: MySQL Error: {e}")
    except Exception as e:
        print(f"Database update failed for user {discord_id}): An unexpected error occurred: {e}")
    finally:
        if conn:
            conn.close()

# --- Helper for fuzzy matching (MODIFIED to use Levenshtein distance) ---
def levenshtein_distance(s1: str, s2: str) -> int:
    """
    Calculates the Levenshtein distance between two strings.
    This is the minimum number of single-character edits (insertions, deletions, or substitutions)
    required to change one word into the other.
    """
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]

def calculate_word_similarity(word1: str, word2: str) -> float:
    """
    Calculates a percentage of similarity between two words using Levenshtein distance.
    A higher percentage means more similarity.
    """
    word1_lower = word1.lower()
    word2_lower = word2.lower()

    max_len = max(len(word1_lower), len(word2_lower))
    if max_len == 0:
        return 100.0

    dist = levenshtein_distance(word1_lower, word2_lower)
    similarity_percentage = ((max_len - dist) / max_len) * 100.0
    return similarity_percentage

# Helper function to convert a verb to its simple past tense
def to_past_tense(verb):
    """
    Converts a given verb to its simple past tense form.
    Handles common irregular verbs and regular verbs.
    """
    irregular_verbs = {
        "go": "went", "come": "came", "see": "saw", "say": "said", "make": "made",
        "take": "took", "know": "knew", "get": "got", "give": "gave", "find": "found",
        "think": "thought", "told": "told", "become": "became", "show": "showed",
        "leave": "left", "feel": "felt", "put": "put", "bring": "brought", "begin": "began",
        "run": "ran", "eat": "ate", "sing": "sang", "drink": "drank", "swim": "swam",
        "break": "broke", "choose": "chose", "drive": "drove", "fall": "fell", "fly": "flew",
        "forget": "forgot", "hold": "held", "read": "read", "ride": "rode", "speak": "spoke",
        "stand": "stood", "steal": "stole", "strike": "struck", "write": "wrote",
        "burst": "burst", "hit": "hit", "cut": "cut", "cost": "cost", "let": "let",
        "shut": "shut", "spread": "spread",
        "shit": "shit",
        "bust": "busted",
        "burp": "burped",
        "rocket": "rocketed",
        "cross": "crossed",
        "give": "gave",
        "tell": "told",
        "whisper": "whispered",
        "piss": "pissed",
        "take": "took",
        "put": "put",
        "flip": "flipped",
        "reverse": "reversed",
        "waffle-spank": "waffle-spanked",
        "kiss": "kissed",
        "spin": "spun",
        "vomit": "vomitted",
        "sand-blast": "sand-blasted",
        "slip": "slipped",
    }
    if verb in irregular_verbs:
        return irregular_verbs[verb]
    elif verb.endswith('e'):
        return verb + 'd'
    elif verb.endswith('y') and len(verb) > 1 and verb[-2] not in 'aeiou':
        return verb[:-1] + 'ied'
    else:
        return verb + 'ed'

# --- Consolidate commands under a single /serene command group ---
# This group is defined ONCE here and then commands are added to it by cogs.
serene_group = app_commands.Group(name="serene", description="Commands for Serene Bot.")
bot.tree.add_command(serene_group)


@bot.event
async def on_ready():
    """
    Event handler that runs when the bot is ready.
    It prints the bot's login information, loads cogs, syncs slash commands,
    starts the hourly database connection check, and
    adds all existing guild members to the database if they don't exist.
    """
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    print('------')
    try:
        # Load cogs first
        await bot.load_extension("cogs.general")
        await bot.load_extension("cogs.games_main")
        await bot.load_extension("cogs.tictactoe")
        await bot.load_extension("cogs.jeopardy")
        await bot.load_extension("cogs.blackjack")
        await bot.load_extension("cogs.texasholdem")

        # After all cogs are loaded, attempt to clear and then sync commands.
        # This is a robust way to handle CommandSignatureMismatch on startup.
        print("Attempting to clear existing global commands before syncing...")
        # The TypeError indicates that bot.tree.clear_commands(guild=None) might be returning None
        # and then await is called on None.
        # Let's try clear_commands() without any arguments, which is the default for global.
        # If that still fails, it suggests a deeper issue with the CommandTree itself.
        await bot.tree.clear_commands() # Clear global commands (no arguments needed for global)
        synced = await bot.tree.sync() # Sync global commands (no arguments needed for global)
        print(f"Cleared and Synced {len(synced)} slash commands globally.")
        
    except Exception as e:
        print(f"Failed to sync commands or load cogs: {e}")
        traceback.print_exc() # Print the full traceback of the exception
    
    hourly_db_check.start()
    await bot.wait_until_ready()
    print("Checking existing guild members for database entry...")
    for guild in bot.guilds:
        print(f"Processing guild: {guild.name} (ID: {guild.id})")
        for member in guild.members:
            if not member.bot:
                await add_user_to_db_if_not_exists(member.guild.id, member.display_name, member.id)
    print("Finished checking existing guild members.")


@bot.event
async def on_member_join(member: discord.Member):
    """
    Event handler that runs when a new member joins a guild.
    Adds the new member to the database if they don't already exist.
    """
    if member.bot:
        return
    print(f"New member joined: {member.display_name} (ID: {member.id}) in guild {member.guild.name} (ID: {member.guild.id}).")
    await add_user_to_db_if_not_exists(member.guild.id, member.display_name, member.id)


@bot.event
async def on_message(message: discord.Message):
    """Listens for messages to handle Jeopardy answers."""
    # Ignore messages from the bot itself
    if message.author.id == bot.user.id:
        return

    # Process other commands normally (important for text commands if any)
    await bot.process_commands(message)


# --- Hourly Database Connection Check ---
@tasks.loop(hours=1)
async def hourly_db_check():
    """
    Attempts to connect to the MySQL database every hour using environment variables.
    Logs success or failure to the console.
    This is primarily for monitoring database connectivity.
    """
    print("Attempting hourly database connection check...")
    db_user = os.getenv('DB_USER')
    db_password = os.getenv('DB_PASSWORD')
    db_host = os.getenv('DB_HOST')
    db_name = "serene_users"

    if not all([db_user, db_password, db_host]):
        print("Database connection failed: Missing one or more environment variables (DB_USER, DB_PASSWORD, DB_HOST).")
        return

    conn = None
    try:
        conn = await aiomysql.connect(
            host=db_host,
            user=db_user,
            password=db_password,
            db=db_name,
            charset='utf8mb4',
            autocommit=True
        )
        print(f"Successfully connected to MySQL database '{db_name}' on host '{db_host}' as user '{db_user}'.")
    except aiomysql.Error as e:
        print(f"Database connection failed: MySQL Error: {e}")
    except Exception as e:
        print(f"Database connection failed: An unexpected error occurred: {e}")
    finally:
        if conn:
            conn.close()
            print("Database connection closed.")

@hourly_db_check.error
async def hourly_db_check_error(exception):
    """Error handler for the hourly_db_check task."""
    print(f"An error occurred in hourly_db_check task: {exception}")


# Load environment variables for the token
BOT_TOKEN = os.getenv('BOT_TOKEN')

if BOT_TOKEN is None:
    print("Error: BOT_TOKEN environment variable not set.")
else:
    # This ensures bot.run() is only called when bot.py is executed directly.
    if __name__ == "__main__":
        bot.run(BOT_TOKEN)
