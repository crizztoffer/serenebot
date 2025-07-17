# cogs/general.py
import os
import urllib.parse
import json
import aiohttp
import discord
from discord.ext import commands
from discord import app_commands

# Import helper functions and the global serene_group from the main bot file
from bot import to_past_tense, serene_group # Import serene_group here

class General(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # Commands that are NOT part of the /serene group
    @app_commands.command(name="ping", description="Responds with Pong!")
    async def ping(self, interaction: discord.Interaction):
        await interaction.response.send_message("Pong!", ephemeral=True)

    @app_commands.command(name="sync", description="Syncs slash commands (Admin only).")
    @commands.is_owner()
    async def sync(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            synced = await self.bot.tree.sync()
            await interaction.followup.send(f"Synced {len(synced)} commands globally.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Failed to sync commands: {e}", ephemeral=True)

    # Define the commands that *will be added* to the serene_group.
    # These are now regular methods or app_commands.Command objects.
    @app_commands.describe(text_input="Your message or question for Serene.")
    async def talk_command_impl(self, interaction: discord.Interaction, text_input: str):
        """Implementation for the /serene talk command."""
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

    async def hail_command_impl(self, interaction: discord.Interaction):
        """Implementation for the /serene hail command."""
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

    async def roast_command_impl(self, interaction: discord.Interaction):
        """Implementation for the /serene roast command."""
        await interaction.response.defer()

        php_backend_url = "https://serenekeks.com/serene_bot.php"
        player_name = interaction.user.display_name
        text_to_send = "roast me"
        param_name = "roast"

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

    async def story_command_impl(self, interaction: discord.Interaction):
        """Implementation for the /serene story command."""
        await interaction.response.defer()

        php_backend_url = "https://serenekeks.com/serene_bot_2.php"
        player_name = interaction.user.display_name

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
            async with aiohttp.ClientSession() as session:
                async with session.get(php_backend_url) as response:
                    if response.status == 200:
                        php_story_structure = await response.json()
                        v1_form_required = php_story_structure.get("verb_forms", {}).get("v1_form", "infinitive")
                        v2_form_required = php_story_structure.get("verb_forms", {}).get("v2_form", "past_tense")
                    else:
                        print(f"Warning: PHP backend call failed with status {response.status}. Using default verb forms and structure.")
        except aiohttp.ClientError as e:
            print(f"Error connecting to PHP backend: {e}. Using default story structure and verb forms.")
        except Exception as e:
            print(f"An unexpected error occurred while fetching PHP structure: {e}. Using default story structure and verb forms.")

        try:
            gemini_prompt = """
            Generate 3 distinct, imaginative, and often absurd or whimsical nouns. These nouns should be simple, common, and in **lowercase**.
            Also, generate 2 distinct, action-oriented verbs in their BASE/INFINITIVE form. These verbs must be simple, common, and in **lowercase**. They must be suitable for both an infinitive context (e.g., "loved to [verb]") and a simple past tense context (e.g., "they [verb_past_tense]").
            Crucially, consider the following specific PHP sentence fragments where these verbs will be inserted. Ensure the BASE verb makes sense in these contexts, even when later conjugated to past tense:

            **For Verb 1 (infinitive - will be used after phrases like 'loved to'):**
            - "who loved to [verb]"
            - "that hated to [verb]"
            - "who used to [verb]"
            - "that preferred to [verb]"
            - "spent their life trying to [verb]"

            **For Verb 2 (will be converted to simple past tense - will be used after phrases like 'before they'):**
            - "came barreling towards them before they [verb_past_tense]"
            - "fell from the heavens just as they [verb_past_tense]"
            - "slipped off the roof above—and with a thump—they [verb_past_tense]"
            - "shit out a turd that flew out of their ass so fast, they [verb_past_tense]"
            - "busted a nut so hard, they [verb_past_tense]"
            - "burped so loud, they [verb_past_tense]"
            - "rocketd right into their face—so hard that they [verb_past_tense]"
            - "crossed over the great divide, gave Jesus a high five, and flew back down with such velocity, that they [verb_past_tense]"
            - "told such a bad joke that they [verb_past_tense]"
            - "whispered so quietly that they [verb_past_tense]"
            - "pissed so loudly that they [verb_past_tense]"
            - "took a cock so big that they [verb_past_tense]"
            - "put their thing down, flipped it, and reversed it so perfectly, that they [verb_past_tense]"
            "waffle-spanked a vagrant so hard that they [verb_past_tense]"
            "kiss": "kissed", # "kissed Crizz P."
            "spin": "spun", # "spun around"
            "vomit": "vomitted", # "vomitted so loudly"
            "sand-blast": "sand-blasted", # "sand-blasted out a power-shart"
            "slip": "slipped", # "slipped off the roof"

            Avoid verbs that are passive, imply a state of being, or require complex grammatical structures (e.g., phrasal verbs that depend heavily on prepositions) to make sense in these direct contexts. Focus on verbs that are direct and complete actions.

            Provide the output as a JSON object with keys "nouns" (an array of 3 strings) and "verbs" (an array of 2 strings).
            Example: {"nouns": ["dragon", "knight", "castle"], "verbs": ["escape", "explode"]}
            """

            chat_history = []
            chat_history.append({"role": "user", "parts": [{"text": gemini_prompt}]})
            
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
            
            api_key = os.getenv('GEMINI_API_KEY')
            if api_key is None:
                print("Error: GEMINI_API_KEY environment variable not set. Gemini API calls will fail.")
                nouns = ["creature", "forest", "adventure"]
                verbs_infinitive = ["walk", "discover"]
            
            if api_key:
                api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"

                async with aiohttp.ClientSession() as session:
                    async with session.post(api_url, headers={'Content-Type': 'application/json'}, json=payload) as response:
                        if response.status == 200:
                            gemini_result = await response.json()
                            
                            if gemini_result.get("candidates") and len(gemini_result["candidates"]) > 0 and \
                               gemini_result["candidates"][0].get("content") and \
                               gemini_result["candidates"][0]["content"].get("parts") and \
                               len(gemini_result["candidates"][0]["content"]["parts"]) > 0:
                                
                                generated_json_str = gemini_result["candidates"][0]["content"]["parts"][0]["text"]
                                generated_words = json.loads(generated_json_str)
                                
                                nouns = [n.lower() for n in generated_words.get("nouns", ["thing", "place", "event"])]
                                verbs_infinitive = [v.lower() for v in generated_words.get("verbs", ["do", "happen"])]
                                
                                nouns = (nouns + ["thing", "place", "event"])[:3]
                                verbs_infinitive = (verbs_infinitive + ["do", "happen"])[:2] 

                            else:
                                print("Warning: Gemini response structure unexpected. Using fallback words.")

                        else:
                            print(f"Warning: Gemini API call failed with status {response.status}. Using fallback words.")

        except Exception as e:
            print(f"Error calling Gemini API: {e}. Using fallback words.")

        verb1_final = verbs_infinitive[0]
        if v1_form_required == "past_tense":
            verb1_final = to_past_tense(verbs_infinitive[0])

        verb2_final = verbs_infinitive[1]
        if v2_form_required == "past_tense":
            verb2_final = to_past_tense(verbs_infinitive[1])


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


async def setup(bot):
    cog = General(bot)
    await bot.add_cog(cog)
    # Explicitly add commands to the serene_group after the cog is loaded
    serene_group.add_command(app_commands.Command(callback=cog.talk_command_impl, name="talk", description="Interact with the Serene bot backend."))
    serene_group.add_command(app_commands.Command(callback=cog.hail_command_impl, name="hail", description="Hail Serene!"))
    serene_group.add_command(app_commands.Command(callback=cog.roast_command_impl, name="roast", description="Get roasted by Serene!"))
    serene_group.add_command(app_commands.Command(callback=cog.story_command_impl, name="story", description="Generate a story with contextually appropriate nouns and verbs."))
