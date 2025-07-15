import os
import random
import urllib.parse
import json # Still needed for handling JSON response from PHP

import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
# Re-added nltk imports for WordNet
import nltk
from nltk.corpus import wordnet as wn

# Define a persistent directory for NLTK data
# This is crucial for deployment environments like Railway
NLTK_DATA_DIR = os.path.join(os.getcwd(), '.nltk_data')
if not os.path.exists(NLTK_DATA_DIR):
    os.makedirs(NLTK_DATA_DIR)
nltk.data.path.append(NLTK_DATA_DIR)


# Download WordNet if not already downloaded
# This is crucial for nltk.corpus.wordnet to work.
# Added checks to prevent repeated downloads on subsequent runs.
try:
    print(f"Checking for 'wordnet' in {NLTK_DATA_DIR}...")
    # Removed 'path' argument as it's not supported in older NLTK versions and redundant with nltk.data.path.append
    nltk.data.find('corpora/wordnet')
except LookupError:
    print("Downloading WordNet...")
    nltk.download('wordnet', download_dir=NLTK_DATA_DIR)
try:
    print(f"Checking for 'omw-1.4' in {NLTK_DATA_DIR}...")
    # Removed 'path' argument as it's not supported in older NLTK versions and redundant with nltk.data.path.append
    nltk.data.find('corpora/omw-1.4')
except LookupError:
    print("Downloading OMW-1.4...")
    nltk.download('omw-1.4', download_dir=NLTK_DATA_DIR)


# Define intents
intents = discord.Intents.default()
intents.members = True
intents.presences = True

# Initialize the bot
bot = commands.Bot(command_prefix='!', intents=intents)

# Functions to get simple nouns and verbs using WordNet
def get_simple_nouns(n=3):
    """
    Fetches a list of simple, common nouns using NLTK's WordNet.
    Filters for single, lowercase, alphabetic words, and attempts to avoid overly generic terms.
    """
    nouns = set()
    # Increased pool size to get more variety
    for synset in wn.all_synsets('n'):
        name = synset.lemmas()[0].name().lower() # Ensure lowercase
        # Filter: only alphabetic lowercase simple words, and avoid very common/generic ones if possible
        if name.isalpha() and len(name) > 2 and '_' not in name and '-' not in name: # Avoid multi-word lemmas
            nouns.add(name)
        if len(nouns) >= 3000: # Increased limit for more variety
            break
    
    # Attempt to get more imaginative nouns by filtering or re-sampling if necessary
    # Blacklist of very common/less descriptive nouns
    common_boring_nouns = {
        "thing", "person", "place", "time", "way", "man", "woman", "boy", "girl", "day", "night",
        "world", "life", "hand", "part", "child", "eye", "head", "house", "car", "door", "room",
        "water", "air", "food", "money", "work", "game", "story", "fact", "idea", "group", "system",
        "number", "point", "problem", "question", "side", "state", "area", "city", "country", "government",
        "sound", "light", "color", "shape", "size", "kind", "form", "value", "word", "name", "line",
        "art", "music", "book", "film", "show", "play", "power", "force", "energy", "matter", "space",
        "mind", "spirit", "soul", "body", "heart", "blood", "bone", "skin", "hair", "face", "mouth",
        "nose", "ear", "foot", "arm", "leg", "finger", "toe", "back", "front", "top", "bottom", "side",
        "end", "beginning", "middle", "moment", "hour", "week", "month", "year", "century", "age",
        "morning", "afternoon", "evening", "night", "past", "present", "future", "time", "space", "form"
    }
    
    filtered_nouns = [noun for noun in list(nouns) if noun not in common_boring_nouns]
    
    # Ensure we can pick 'n' distinct nouns. If not enough after filtering, fall back to less strict selection.
    if len(filtered_nouns) < n:
        # If filtering was too aggressive, take from the original set, excluding already chosen
        remaining_nouns = list(nouns - set(filtered_nouns))
        final_nouns = random.sample(filtered_nouns, min(n, len(filtered_nouns)))
        while len(final_nouns) < n and remaining_nouns:
            # Pop from remaining to ensure distinctness and avoid infinite loop if pool is small
            final_nouns.append(remaining_nouns.pop(random.randrange(len(remaining_nouns))))
    else:
        final_nouns = random.sample(filtered_nouns, n)
        
    return final_nouns


def get_simple_verbs(n=2):
    """
    Fetches a list of simple, common verbs (infinitive form) using NLTK's WordNet.
    Filters for single, lowercase, alphabetic words, and attempts to avoid overly generic terms.
    """
    verbs = set()
    # Increased pool size to get more variety
    for synset in wn.all_synsets('v'):
        name = synset.lemmas()[0].name().lower() # Ensure lowercase
        # Filter: only alphabetic lowercase simple words, and avoid very common/generic ones
        if name.isalpha() and len(name) > 1 and '_' not in name and '-' not in name: # Avoid multi-word lemmas
            verbs.add(name)
        if len(verbs) >= 3000: # Increased limit for more variety
            break
            
    # Blacklist of very common/less descriptive verbs
    common_boring_verbs = {
        "be", "have", "do", "say", "get", "make", "go", "know", "take", "see", "come",
        "think", "look", "want", "give", "use", "find", "tell", "ask", "seem", "feel",
        "show", "try", "call", "mean", "become", "leave", "put", "hold", "write", "stand",
        "sit", "run", "walk", "talk", "start", "end", "begin", "help", "play", "move", "live",
        "turn", "work", "change", "follow", "stop", "create", "read", "add", "grow", "open",
        "build", "send", "expect", "allow", "force", "offer", "learn", "change", "lead", "understand"
    }

    filtered_verbs = [verb for verb in list(verbs) if verb not in common_boring_verbs]

    if len(filtered_verbs) < n:
        remaining_verbs = list(verbs - set(filtered_verbs))
        final_verbs = random.sample(filtered_verbs, min(n, len(filtered_verbs)))
        while len(final_verbs) < n and remaining_verbs:
            # Pop from remaining to ensure distinctness and avoid infinite loop if pool is small
            final_verbs.append(remaining_verbs.pop(random.randrange(len(remaining_verbs))))
    else:
        final_verbs = random.sample(filtered_verbs, n)

    return final_verbs


@bot.event
async def on_ready():
    """
    Event handler that runs when the bot is ready.
    It prints the bot's login information and syncs slash commands.
    """
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    print('------')
    try:
        # Sync slash commands with Discord. This makes the commands available in guilds.
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")


# --- Existing /serene command (unchanged) ---
@bot.tree.command(name="serene", description="Interact with the Serene bot backend.")
@app_commands.describe(text_input="Your message or question for Serene.")
async def serene_command(interaction: discord.Interaction, text_input: str):
    """
    Handles the /serene slash command.
    Sends user input to the serene_bot.php backend and displays the response.
    """
    await interaction.response.defer() # Acknowledge the interaction to prevent timeout

    php_backend_url = "https://serenekeks.com/serene_bot.php"
    player_name = interaction.user.display_name

    # Determine the parameter name based on the input text
    lower_text_input = text_input.lower()
    if lower_text_input.startswith(("hello", "hi", "hail")):
        param_name = "hail"
    elif lower_text_input.startswith(("start", "begin")):
        param_name = "start"
    else:
        param_name = "question"

    # Prepare parameters for the PHP backend
    params = {
        param_name: text_input,
        "player": player_name
    }
    # URL-encode parameters to safely pass them in the URL
    encoded_params = urllib.parse.urlencode(params, quote_via=urllib.parse.quote_plus)
    full_url = f"{php_backend_url}?{encoded_params}"

    try:
        # Make an asynchronous HTTP GET request to the PHP backend
        async with aiohttp.ClientSession() as session:
            async with session.get(full_url) as response:
                if response.status == 200:
                    # If the request was successful, get the response text
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
        # Handle network-related errors (e.g., cannot connect to host)
        await interaction.followup.send(
            f"**{player_name} says:** {text_input}\n"
            f"Could not connect to the Serene backend. Error: {e}"
        )
    except Exception as e:
        # Handle any other unexpected errors
        await interaction.followup.send(
            f"**{player_name} says:** {text_input}\n"
            f"An unexpected error occurred: {e}"
        )


# --- Existing /hail_serene command (unchanged) ---
@bot.tree.command(name="hail_serene", description="Hail Serene!")
async def hail_serene_command(interaction: discord.Interaction):
    """
    Handles the /hail_serene slash command.
    Sends a predefined "hail serene" message to the backend and displays the response.
    """
    await interaction.response.defer() # Acknowledge the interaction

    php_backend_url = "https://serenekeks.com/serene_bot.php"
    player_name = interaction.user.display_name

    text_to_send = "hail serene"  # Predefined text for this command
    param_name = "hail"

    # Prepare parameters for the PHP backend
    params = {
        param_name: text_to_send,
        "player": player_name
    }
    encoded_params = urllib.parse.urlencode(params, quote_via=urllib.parse.quote_plus)
    full_url = f"{php_backend_url}?{encoded_params}"

    try:
        # Make an asynchronous HTTP GET request
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


# Helper function to convert a verb to its simple past tense
def to_past_tense(verb):
    """
    Converts a given verb to its simple past tense form.
    Handles common irregular verbs and regular verbs.
    This function is crucial for ensuring grammatical correctness
    with the PHP sentence structures.
    """
    irregular_verbs = {
        "go": "went", "come": "came", "see": "saw", "say": "said", "make": "made",
        "take": "took", "know": "knew", "get": "got", "give": "gave", "find": "found",
        "think": "thought", "tell": "told", "become": "became", "show": "showed",
        "leave": "left", "feel": "felt", "put": "put", "bring": "brought", "begin": "began",
        "run": "ran", "eat": "ate", "sing": "sang", "drink": "drank", "swim": "swam",
        "break": "broke", "choose": "chose", "drive": "drove", "fall": "fell", "fly": "flew",
        "forget": "forgot", "hold": "held", "read": "read", "ride": "rode", "speak": "spoke",
        "stand": "stood", "steal": "stole", "strike": "struck", "write": "wrote",
        "burst": "burst", "hit": "hit", "cut": "cut", "cost": "cost", "let": "let",
        "shut": "shut", "spread": "spread",
        # Explicitly added from PHP's $forth array to ensure correct past tense handling
        "shit": "shit", # "shit out a turd"
        "bust": "busted", # "busted a nut"
        "burp": "burped", # "burped so loud"
        "rocket": "rocketed", # "rocketed right into"
        "cross": "crossed", # "crossed over the great divide"
        "give": "gave", # "gave Jesus a high five"
        "tell": "told", # "told such a bad joke"
        "whisper": "whispered", # "whispered so quietly"
        "piss": "pissed", # "pissed so loudly"
        "take": "took", # "took a cock"
        "put": "put", # "put their thing down"
        "flip": "flipped", # "flipped it"
        "reverse": "reversed", # "reversed it"
        "waffle-spank": "waffle-spanked", # "waffle-spanked a vagrant"
        "kiss": "kissed", # "kissed Crizz P."
        "spin": "spun", # "spun around"
        "vomit": "vomitted", # "vomitted so loudly"
        "sand-blast": "sand-blasted", # "sand-blasted out a power-shart"
        "slip": "slipped", # "slipped off the roof"
    }
    if verb in irregular_verbs:
        return irregular_verbs[verb]
    elif verb.endswith('e'):
        return verb + 'd'
    elif verb.endswith('y') and len(verb) > 1 and verb[-2] not in 'aeiou':
        return verb[:-1] + 'ied'
    # Handle doubling consonant for single-syllable verbs ending in CVC (e.g., "stop" -> "stopped")
    elif len(verb) > 1 and verb[-1] not in 'aeiouy' and verb[-2] in 'aeiou' and verb[-3] not in 'aeiouy':
        # Simple check, might not cover all cases but catches many common ones
        return verb + verb[-1] + 'ed'
    else:
        return verb + 'ed'


# --- NEW /serene_story command (MODIFIED to use NLTK and PHP JSON output) ---
@bot.tree.command(name="serene_story", description="Generate a story with contextually appropriate nouns and verbs (free).")
async def serene_story_command(interaction: discord.Interaction):
    """
    Handles the /serene_story slash command.
    Fetches sentence structure from PHP, generates nouns and verbs using NLTK,
    then constructs and displays the story.
    """
    await interaction.response.defer() # Acknowledge the interaction

    php_backend_url = "https://serenekeks.com/serene_bot_2.php"
    player_name = interaction.user.display_name

    # Initialize nouns and verbs with fallbacks in case NLTK fails or provides insufficient variety
    nouns = ["creature", "forest", "adventure"]
    verbs_infinitive = ["walk", "discover"]

    # Initialize php_story_structure with defaults in case PHP call fails
    php_story_structure = {
        "first": "There once was a ",
        "second": " who loved to ",
        "third": ". But then one night, there came a shock… for a ",
        "forth": " came barreling towards them before they ",
        "fifth": " and lived happily ever after."
    }
    v1_form_required = "infinitive"
    v2_form_required = "past_tense"

    try:
        # First, call the PHP backend to get the sentence structure
        async with aiohttp.ClientSession() as session:
            async with session.get(php_backend_url) as response:
                if response.status == 200:
                    php_story_structure = await response.json()
                    
                    # Extract verb form requirements from PHP response
                    v1_form_required = php_story_structure.get("verb_forms", {}).get("v1_form", "infinitive")
                    v2_form_required = php_story_structure.get("verb_forms", {}).get("v2_form", "past_tense")

                else:
                    print(f"Warning: PHP backend call failed with status {response.status}. Using default verb forms and structure.")

    except aiohttp.ClientError as e:
        print(f"Error connecting to PHP backend: {e}. Using default story structure and verb forms.")
    except Exception as e:
        print(f"An unexpected error occurred while fetching PHP structure: {e}. Using default story structure and verb forms.")


    # Get nouns and verbs using NLTK
    try:
        nouns = get_simple_nouns(3)
        verbs_infinitive = get_simple_verbs(2)
    except Exception as e:
        print(f"Error generating words with NLTK: {e}. Using fallback words.")
        nouns = ["creature", "forest", "adventure"]
        verbs_infinitive = ["walk", "discover"]


    # Conjugate verbs based on PHP's requirements
    verb1_final = verbs_infinitive[0]
    if v1_form_required == "past_tense":
        verb1_final = to_past_tense(verbs_infinitive[0])

    verb2_final = verbs_infinitive[1]
    if v2_form_required == "past_tense":
        verb2_final = to_past_tense(verbs_infinitive[1])


    # Assemble the full story using PHP's structure and generated words
    full_story = (
        php_story_structure["first"] + nouns[0] +
        php_story_structure["second"] + verb1_final +
        php_story_structure["third"] + nouns[1] +
        php_story_structure["forth"] + verb2_final +
        php_story_structure["fifth"] # Noun3 is now part of the fifth phrase in PHP's structure
    )

    display_message = (
        f"**{player_name} asked for a story**\n"
        f"**Serene says:** {full_story}"
    )
    await interaction.followup.send(display_message)


# Load environment variables for the token
BOT_TOKEN = os.getenv('BOT_TOKEN')

if BOT_TOKEN is None:
    print("Error: BOT_TOKEN environment variable not set.")
else:
    bot.run(BOT_TOKEN)
