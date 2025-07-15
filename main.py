import os
import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import urllib.parse
import nltk
from nltk.corpus import wordnet as wn
from pattern.en import conjugate, PAST
import random

# Download WordNet data
nltk.download('wordnet')
nltk.download('omw-1.4')

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
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

# --- Slash Command: /serene ---
@bot.tree.command(name="serene", description="Interact with the Serene bot backend.")
@app_commands.describe(text_input="Your message or question for Serene.")
async def serene_command(interaction: discord.Interaction, text_input: str):
    await interaction.response.defer()

    php_backend_url = "https://serenekeks.com/serene_bot.php"
    player_name = interaction.user.display_name

    lower_text_input = text_input.lower()
    if lower_text_input.startswith(("hello", "hi", "hail")):
        param_name = "hail"
    elif lower_text_input.startswith(("start", "begin")):
        param_name = "start"
    else:
        param_name = "question"

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
                    display_message = (
                        f"**{player_name} says:** {text_input}\n"
                        f"**Serene says:** {php_response_text}"
                    )
                    await interaction.followup.send(display_message)
                else:
                    await interaction.followup.send(
                        f"**{player_name} says:** {text_input}\n"
                        f"Serene backend returned an error: HTTP Status {response.status}"
                    )
    except aiohttp.ClientError as e:
        await interaction.followup.send(
            f"**{player_name} says:** {text_input}\n"
            f"Could not connect to the Serene backend. Error: {e}"
        )
    except Exception as e:
        await interaction.followup.send(
            f"**{player_name} says:** {text_input}\n"
            f"An unexpected error occurred: {e}"
        )

# --- Slash Command: /hail_serene ---
@bot.tree.command(name="hail_serene", description="Hail Serene!")
async def hail_serene_command(interaction: discord.Interaction):
    await interaction.response.defer()

    php_backend_url = "https://serenekeks.com/serene_bot.php"
    player_name = interaction.user.display_name

    text_to_send = "hail serene"
    param_name = "hail"

    params = {
        param_name: text_to_send,
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

# --- New Slash Command: /serene_story ---
@bot.tree.command(name="serene_story", description="Generates a random Serene story using nouns and past-tense verbs.")
async def serene_story_command(interaction: discord.Interaction):
    await interaction.response.defer()

    php_backend_url = "https://serenekeks.com/serene_bot_2.php"

    try:
        # Get 3 random nouns
        nouns = list({lemma.name().replace('_', ' ') for syn in wn.all_synsets('n') for lemma in syn.lemmas()})
        random_nouns = random.sample(nouns, 3)

        # Get 2 random past-tense verbs
        verbs = list({lemma.name() for syn in wn.all_synsets('v') for lemma in syn.lemmas()})
        past_tense_verbs = []
        while len(past_tense_verbs) < 2:
            base = random.choice(verbs)
            past = conjugate(base, tense=PAST)
            if past:
                past_tense_verbs.append(past)

        # Build parameters
        params = {
            "n1": random_nouns[0],
            "v1": past_tense_verbs[0],
            "n2": random_nouns[1],
            "v2": past_tense_verbs[1],
            "n3": random_nouns[2]
        }

        encoded_params = urllib.parse.urlencode(params, quote_via=urllib.parse.quote_plus)
        full_url = f"{php_backend_url}?{encoded_params}"

        # Request story from backend
        async with aiohttp.ClientSession() as session:
            async with session.get(full_url) as response:
                if response.status == 200:
                    story = await response.text()
                    await interaction.followup.send(f"**Serene tells a story:**\n{story}")
                else:
                    await interaction.followup.send(
                        f"Serene backend returned an error: HTTP Status {response.status}"
                    )

    except Exception as e:
        await interaction.followup.send(f"An error occurred while generating the story: {e}")

# Load environment variable for bot token
BOT_TOKEN = os.getenv('BOT_TOKEN')

if BOT_TOKEN is None:
    print("Error: BOT_TOKEN environment variable not set.")
else:
    bot.run(BOT_TOKEN)
