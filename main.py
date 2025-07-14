import os
import discord
from discord.ext import commands

# Define intents. Adjust based on what your bot needs to do.
# For a MEE6-like bot, you'll likely need all privileged intents.
intents = discord.Intents.default()
intents.members = True # Required for member-related events (e.g., welcome messages)
intents.message_content = True # Required to read message content for commands
intents.presences = True # Required for presence updates

# Initialize the bot with a command prefix and intents
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    print('------')

@bot.command(name='hello')
async def hello(ctx):
    """A simple hello command."""
    await ctx.send(f'Hello, {ctx.author.display_name}!')

@bot.command(name='ping')
async def ping(ctx):
    """Checks the bot's latency."""
    await ctx.send(f'Pong! {round(bot.latency * 1000)}ms')

# Load environment variables for the token
BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')

if BOT_TOKEN is None:
    print("Error: DISCORD_BOT_TOKEN environment variable not set.")
else:
    bot.run(BOT_TOKEN)
