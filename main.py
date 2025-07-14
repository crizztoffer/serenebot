import os
import discord
from discord.ext import commands
from discord import app_commands # Import app_commands

# Define intents
intents = discord.Intents.default()
intents.members = True
intents.message_content = True # Still needed for message-based commands if you keep them
intents.presences = True

# Initialize the bot
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    print('------')
    try:
        # This will sync your commands globally. For faster testing,
        # you can sync to a specific guild using bot.tree.sync(guild=discord.Object(id=YOUR_GUILD_ID))
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

# --- Legacy Message-Based Commands (Optional, can be removed if only using slash commands) ---
@bot.command(name='hello')
async def hello(ctx):
    """A simple hello command."""
    await ctx.send(f'Hello, {ctx.author.display_name}!')

@bot.command(name='ping')
async def ping(ctx):
    """Checks the bot's latency."""
    await ctx.send(f'Pong! {round(bot.latency * 1000)}ms')
# -----------------------------------------------------------------------------------------

# --- Slash Command ---
@bot.tree.command(name="serene", description="Sends a serene message back with your text.")
@app_commands.describe(text_input="The text you want to send with serene.") # Describe the parameter
async def serene_command(interaction: discord.Interaction, text_input: str):
    """
    Shows your text after the /serene command.
    """
    await interaction.response.send_message(f"You typed: {text_input}")

# Load environment variables for the token
BOT_TOKEN = os.getenv('BOT_TOKEN')

if BOT_TOKEN is None:
    print("Error: BOT_TOKEN environment variable not set.")
else:
    bot.run(BOT_TOKEN)
