import os
import discord
from discord.ext import commands
from discord import app_commands
import aiohttp # Import aiohttp for making HTTP requests
import urllib.parse # For URL encoding parameters

# Define intents
intents = discord.Intents.default()
intents.members = True
intents.presences = True

# Initialize the bot
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    print('------')
    try:
        # Sync your commands globally. For faster testing,
        # you can sync to a specific guild using bot.tree.sync(guild=discord.Object(id=YOUR_GUILD_ID))
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

# --- Slash Command ---
@bot.tree.command(name="serene", description="Interact with the Serene bot backend.")
@app_commands.describe(text_input="Your message or question for Serene.")
async def serene_command(interaction: discord.Interaction, text_input: str):
    """
    Sends text and username to serene_bot.php and returns the response.
    """
    await interaction.response.defer() # Acknowledge the command immediately as the PHP call might take a moment

    php_backend_url = "https://serenekeks.com/serene_bot.php"
    player_name = interaction.user.display_name # Get the Discord user's display name

    # Determine which parameter to use based on the input text
    # This logic needs to align with your PHP script's 'start', 'question', 'hail' parameters
    # For now, let's assume 'question' is a good default for general text input.
    # You might want to implement more sophisticated logic here based on your PHP's function calls.

    # Example: If the input starts with "hello" or "hi", use 'hail'.
    # Otherwise, use 'question'. You can expand this logic.
    lower_text_input = text_input.lower()
    if lower_text_input.startswith(("hello", "hi", "hail")):
        param_name = "hail"
    elif lower_text_input.startswith(("start", "begin")): # Assuming 'start' is for initial greetings
        param_name = "start"
    else:
        param_name = "question" # Default to 'question' for general inquiries

    # Construct the URL with parameters
    # Using urllib.parse.quote_plus for robust URL encoding of spaces and special characters
    params = {
        param_name: text_input,
        "player": player_name
    }
    encoded_params = urllib.parse.urlencode(params, quote_via=urllib.parse.quote_plus)
    full_url = f"{php_backend_url}?{encoded_params}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(full_url) as response:
                if response.status == 200:
                    php_response_text = await response.text()
                    await interaction.followup.send(php_response_text)
                else:
                    await interaction.followup.send(
                        f"Serene backend returned an error: HTTP Status {response.status}"
                    )
    except aiohttp.ClientError as e:
        await interaction.followup.send(
            f"Could not connect to the Serene backend. Error: {e}"
        )
    except Exception as e:
        await interaction.followup.send(
            f"An unexpected error occurred: {e}"
        )

# Load environment variables for the token
BOT_TOKEN = os.getenv('BOT_TOKEN')

if BOT_TOKEN is None:
    print("Error: BOT_TOKEN environment variable not set.")
else:
    bot.run(BOT_TOKEN)
