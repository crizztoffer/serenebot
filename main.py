import os
import random
import urllib.parse
import json # Import json for handling LLM output

import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
# Removed nltk imports as WordNet will no longer be used for word generation
# import nltk
# from nltk.corpus import wordnet as wn

# Define intents
intents = discord.Intents.default()
intents.members = True
intents.presences = True

# Initialize the bot
bot = commands.Bot(command_prefix='!', intents=intents)

# Removed get_simple_nouns and get_simple_verbs as they will be replaced by LLM calls

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
                    # Handle non-200 HTTP responses
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
        "shut": "shut", "spread": "spread", "busted": "busted", "burped": "burped", # Added some from PHP forth array
        "fisted": "fisted", "fucked": "fucked", "spanked": "spanked", "crossed": "crossed",
        "gave": "gave", "flew": "flew", "told": "told", "whispered": "whispered",
        "pissed": "pissed", "took": "took", "put": "put", "flipped": "flipped",
        "reversed": "reversed", "waffle-spanked": "waffle-spanked", "kissed": "kissed",
        "ate": "ate", "spun": "spun", "vomitted": "vomitted", "sand-blasted": "sand-blasted",
        "sharted": "sharted", # Assuming 'sand-blasted out a power-shart' might use 'sharted'
        "slipped": "slipped", "fell": "fell", "came": "came", "rocket": "rocketed"
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


# --- NEW /serene_story command (MODIFIED to use Gemini API and PHP JSON output) ---
@bot.tree.command(name="serene_story", description="Generate a story with contextually appropriate nouns and verbs.")
async def serene_story_command(interaction: discord.Interaction):
    """
    Handles the /serene_story slash command.
    Fetches sentence structure from PHP, generates nouns and verbs using Gemini API,
    then constructs and displays the story.
    """
    await interaction.response.defer() # Acknowledge the interaction

    php_backend_url = "https://serenekeks.com/serene_bot_2.php"
    player_name = interaction.user.display_name

    # Initialize nouns and verbs with fallbacks in case of API failure
    nouns = ["dragon", "wizard", "monster"]
    verbs_infinitive = ["fly", "vanish"]

    try:
        # First, call the PHP backend to get the sentence structure
        async with aiohttp.ClientSession() as session:
            async with session.get(php_backend_url) as response:
                if response.status == 200:
                    php_story_structure = await response.json()
                    
                    # Extract verb form requirements from PHP response (though currently static, good practice)
                    v1_form_required = php_story_structure.get("verb_forms", {}).get("v1_form", "infinitive")
                    v2_form_required = php_story_structure.get("verb_forms", {}).get("v2_form", "past_tense")

                else:
                    print(f"Warning: PHP backend call failed with status {response.status}. Using default verb forms and structure.")
                    php_story_structure = {
                        "first": "There once was a ",
                        "second": " who loved to ",
                        "third": ". But then one night, there came a shock… for a ",
                        "forth": " came barreling towards them before they ",
                        "fifth": " and lived happily ever after."
                    }
                    v1_form_required = "infinitive"
                    v2_form_required = "past_tense"

    except aiohttp.ClientError as e:
        print(f"Error connecting to PHP backend: {e}. Using default story structure and verb forms.")
        php_story_structure = {
            "first": "There once was a ",
            "second": " who loved to ",
            "third": ". But then one night, there came a shock… for a ",
            "forth": " came barreling towards them before they ",
            "fifth": " and lived happily ever after."
        }
        v1_form_required = "infinitive"
        v2_form_required = "past_tense"
    except Exception as e:
        print(f"An unexpected error occurred while fetching PHP structure: {e}. Using default story structure and verb forms.")
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
        # Prompt for the Gemini API to get contextually appropriate words
        # The prompt is significantly refined to ensure variety and contextual cohesion
        gemini_prompt = """
        Generate 3 distinct, simple, and imaginative nouns (e.g., "goblin", "wizard", "spaceship", "potato", "unicorn", "sandwich", "elder", "cat", "robot", "shadow", "whisper", "dream", "clown", "squirrel", "banana", "ghost", "vampire", "mermaid", "gnome", "dragon", "ogre", "fairy", "robot", "alien", "zombie", "ninja", "pirate", "cowboy", "detective", "astronaut") and 2 distinct, action-oriented verbs (in their BASE/INFINITIVE form) suitable for a whimsical, adventurous, or absurd story.

        For the nouns, ensure a wide variety in concept, avoiding overly common or generic terms. Prioritize nouns that evoke a sense of character or object in a fantastical or humorous setting.

        For the verbs, they should describe a direct, completed action or reaction. They should be suitable for both an infinitive context (e.g., "loved to [verb]") and a simple past tense context (e.g., "they [verb]").
        Consider the following types of phrases where the verbs will be inserted, ensuring the BASE verb would make sense:
        - "who loved to [verb]"
        - "that hated to [verb]"
        - "who used to [verb]"
        - "that preferred to [verb]"
        - "spent their life trying to [verb]"
        - "before they [verb_past_tense]"
        - "just as they [verb_past_tense]"
        - "with a thump—they [verb_past_tense]"
        - "so fast, they [verb_past_tense]"
        - "so hard, they [verb_past_tense]"
        - "so loud, they [verb_past_tense]"
        - "so hard that they [verb_past_tense]"
        - "gave Jesus a high five, and flew back down with such velocity, that they [verb_past_tense]"
        - "told such a bad joke that they [verb_past_tense]"
        - "whispered so quietly that they [verb_past_tense]"
        - "pissed so loudly that they [verb_past_tense]"
        - "took a cock so big that they [verb_past_tense]"
        - "put their thing down, flipped it, and reversed it so perfectly, that they [verb_past_tense]"
        - "waffle-spanked a vagrant so hard that they [verb_past_tense]"
        - "kissed Crizz P. so fast that he [verb_past_tense]"
        - "ate a dong so long that they [verb_past_tense]"
        - "spun around so fast that they [verb_past_tense]"
        - "vomitted so loudly that they [verb_past_tense]"
        - "sand-blasted out a power-shart so strong, that they [verb_past_tense]"

        Avoid verbs that imply a state of being (e.g., "be", "seem"), a continuous action (e.g., "running"), or require complex objects/prepositions to make sense in these contexts. Focus on verbs that are direct and complete actions.

        Provide the output as a JSON object with keys "nouns" (an array of 3 strings) and "verbs" (an array of 2 strings).
        Example: {"nouns": ["dragon", "knight", "castle"], "verbs": ["escape", "explode"]}
        """

        chat_history = []
        chat_history.append({"role": "user", "parts": [{"text": gemini_prompt}]})
        
        # Define the response schema for structured JSON output from Gemini
        payload = {
            "contents": chat_history,
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {
                        "nouns": {
                            "type": "ARRAY",
                            "items": {"type": "STRING"}
                        },
                        "verbs": {
                            "type": "ARRAY",
                            "items": {"type": "STRING"}
                        }
                    },
                    "propertyOrdering": ["nouns", "verbs"]
                }
            }
        }
        
        # API key will be automatically provided by the Canvas environment
        api_key = "" 
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"

        # Make an asynchronous HTTP POST request to the Gemini API
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, headers={'Content-Type': 'application/json'}, json=payload) as response:
                if response.status == 200:
                    gemini_result = await response.json()
                    
                    # Parse the JSON string from Gemini's response
                    if gemini_result.get("candidates") and len(gemini_result["candidates"]) > 0 and \
                       gemini_result["candidates"][0].get("content") and \
                       gemini_result["candidates"][0]["content"].get("parts") and \
                       len(gemini_result["candidates"][0]["content"]["parts"]) > 0:
                        
                        generated_json_str = gemini_result["candidates"][0]["content"]["parts"][0]["text"]
                        generated_words = json.loads(generated_json_str)
                        
                        # Extract nouns and verbs, using fallbacks if keys are missing
                        nouns = generated_words.get("nouns", ["thing", "place", "event"])
                        verbs_infinitive = generated_words.get("verbs", ["do", "happen"]) 
                        
                        # Ensure we have exactly 3 nouns and 2 verbs, using fallbacks if needed
                        nouns = (nouns + ["thing", "place", "event"])[:3]
                        verbs_infinitive = (verbs_infinitive + ["do", "happen"])[:2] 

                    else:
                        print("Warning: Gemini response structure unexpected. Using fallback words.")

                else:
                    print(f"Warning: Gemini API call failed with status {response.status}. Using fallback words.")

    except Exception as e:
        print(f"Error calling Gemini API: {e}. Using fallback words.")

    # Conjugate verbs based on PHP's requirements
    # v1_form_required and v2_form_required are obtained from PHP's JSON response
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
        php_story_structure["fifth"]
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
