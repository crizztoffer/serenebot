# bot.py
import os
import discord
from discord.ext import commands
from discord import app_commands
import random
import aiohttp
import json
import traceback

# --- Global Variables and Helper Functions (as implied by your cogs) ---

# Active game states (placeholders, ensure these are accessible by your cogs)
active_tictactoe_games = {}
active_jeopardy_games = {}
active_blackjack_games = {}
active_texasholdem_games = {}

# Define the global command group for /serene
serene_group = app_commands.Group(name="serene", description="Commands for interacting with Serene!")

# Placeholder for update_user_kekchipz (implement your actual database logic here)
async def update_user_kekchipz(guild_id: int, user_id: int, amount: int):
    """
    Placeholder function to update user's kekchipz.
    Replace with actual database interaction (e.g., Firestore).
    """
    print(f"DEBUG: Updating kekchipz for user {user_id} in guild {guild_id} by {amount}. (Placeholder)")
    # Example: You would interact with your database here
    # user_data = await fetch_user_data(guild_id, user_id)
    # user_data['kekchipz'] += amount
    # await save_user_data(guild_id, user_id, user_data)

# Placeholder for to_past_tense (implement your actual logic here)
def to_past_tense(verb: str) -> str:
    """
    Placeholder function to convert a verb to its past tense.
    Replace with actual NLP/string manipulation logic.
    """
    # Simple, incomplete example for demonstration
    if verb.endswith('e'):
        return verb + 'd'
    elif verb.endswith('y') and len(verb) > 1 and verb[-2] not in 'aeiou':
        return verb[:-1] + 'ied'
    else:
        return verb + 'ed'

# Placeholder for calculate_word_similarity (implement your actual logic here)
def calculate_word_similarity(word1: str, word2: str) -> float:
    """
    Placeholder function to calculate word similarity.
    Replace with actual fuzzy matching or NLP library.
    Returns a percentage (0-100).
    """
    # Very basic example: Levenshtein distance based similarity
    # For real use, consider libraries like `fuzzywuzzy` or `difflib`
    if not word1 or not word2:
        return 0.0
    
    # Simple Levenshtein distance approximation for similarity
    s1 = word1.lower()
    s2 = word2.lower()
    if s1 == s2:
        return 100.0
    
    rows = len(s1) + 1
    cols = len(s2) + 1
    
    dp = [[0 for _ in range(cols)] for _ in range(rows)]
    
    for i in range(rows):
        dp[i][0] = i
    for j in range(cols):
        dp[0][j] = j
        
    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if s1[i-1] == s2[j-1] else 1
            dp[i][j] = min(dp[i-1][j] + 1, dp[i][j-1] + 1, dp[i-1][j-1] + cost)
            
    max_len = max(len(s1), len(s2))
    if max_len == 0: return 100.0 # Both empty strings
    
    similarity_score = ((max_len - dp[rows-1][cols-1]) / max_len) * 100
    return similarity_score

# --- Bot Setup ---

# Intents are required for certain Discord features
intents = discord.Intents.default()
intents.message_content = True # Required for listening to message content (e.g., for Jeopardy answers)
intents.members = True # Required for accessing member display names

# Create the bot instance
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')

    # Add the command group to the bot's tree
    bot.tree.add_command(serene_group)

    # Load cogs
    try:
        await bot.load_extension("cogs.general")
        print("Loaded cogs.general")
        await bot.load_extension("cogs.games_main")
        print("Loaded cogs.games_main")
        await bot.load_extension("cogs.tictactoe")
        print("Loaded cogs.tictactoe")
        await bot.load_extension("cogs.jeopardy")
        print("Loaded cogs.jeopardy")
        await bot.load_extension("cogs.blackjack")
        print("Loaded cogs.blackjack")
        await bot.load_extension("cogs.texasholdem")
        print("Loaded cogs.texasholdem")
    except Exception as e:
        print(f"Error loading cogs: {e}")

    # Sync slash commands globally
    try:
        synced = await bot.tree.sync() # Sync globally
        print(f"Synced {len(synced)} commands globally.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

# Run the bot
# Replace 'YOUR_BOT_TOKEN' with your actual bot token from Discord Developer Portal
# You should ideally store this in an environment variable (e.g., BOT_TOKEN)
bot_token = os.getenv('BOT_TOKEN')
if bot_token is None:
    print("ERROR: BOT_TOKEN environment variable not set. Please set it before running the bot.")
else:
    bot.run(bot_token)
