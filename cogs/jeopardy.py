# cogs/jeopardy.py
import os
import random
import urllib.parse
import json
import asyncio
import re
import time

import discord
from discord.ext import commands
from discord import app_commands, ui
import aiohttp

# Import active game states and database functions from bot.py
from bot import active_jeopardy_games, update_user_kekchipz, calculate_word_similarity

class Jeopardy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # --- New Jeopardy Game UI Components ---

    class CategoryValueSelect(discord.ui.Select):
        """A dropdown (select) for choosing a question's value within a specific category."""
        def __init__(self, category_name: str, options: list[discord.SelectOption], placeholder: str, row: int):
            super().__init__(
                placeholder=placeholder,
                min_values=1,
                max_values=1,
                options=options,
                custom_id=f"jeopardy_select_{category_name.replace(' ', '_').lower()}_{row}",
                row=row
            )
            self.category_name = category_name

        async def callback(self, interaction: discord.Interaction):
            view: 'Jeopardy.JeopardyGameView' = self.view # Corrected type hint
            game: 'Jeopardy.NewJeopardyGame' = view.game # Corrected type hint

            if interaction.user.id != game.player.id:
                await interaction.response.send_message("You are not the active player for this Jeopardy game.", ephemeral=True)
                return
            
            if game.current_question:
                await interaction.response.send_message("A question is currently active. Please wait for it to conclude.", ephemeral=True)
                return

            selected_value_str = self.values[0]
            selected_value = int(selected_value_str)

            question_data = None
            categories_to_search = []
            if game.game_phase == "NORMAL_JEOPARDY":
                categories_to_search = game.normal_jeopardy_data.get("normal_jeopardy", [])
            elif game.game_phase == "DOUBLE_JEOPARDY":
                categories_to_search = game.double_jeopardy_data.get("double_data", [])

            for cat_data in categories_to_search:
                if cat_data["category"] == self.category_name:
                    for q_data in cat_data["questions"]:
                        if q_data["value"] == selected_value and not q_data["guessed"]: # Corrected: q_data["guessed"]
                            question_data = q_data
                            break
                    if question_data:
                        break
            
            if question_data:
                await interaction.response.send_message(
                    f"**{game.player.display_name}** selected **{question_data['category']}** for **${question_data['value']}**.\n\n"
                    "*Processing your selection...*",
                    ephemeral=True
                )

                question_data["guessed"] = True
                game.current_question = question_data

                view._selected_category = None
                view._selected_value = None

                if game.board_message:
                    try:
                        await game.board_message.delete()
                        game.board_message = None
                    except discord.errors.NotFound:
                        print("WARNING: Original board message not found (already deleted or inaccessible).")
                        game.board_message = None
                    except discord.errors.Forbidden:
                        print("WARNING: Missing permissions to delete the original board message. Please ensure the bot has 'Manage Messages' permission.")
                    except Exception as delete_e:
                        print(f"WARNING: An unexpected error occurred during original board message deletion: {delete_e}")
                        game.board_message = None
                
                determined_prefix = "What is"
                api_key = os.getenv('GEMINI_API_KEY')
                if api_key:
                    try:
                        gemini_prompt = f"Given the answer '{question_data['answer']}', what is the single most grammatically appropriate prefix (e.g., 'What is', 'Who is', 'What are', 'Who are', 'What was', 'Who was', 'What were', 'Who were') that would precede it in a Jeopardy-style question? Provide only the prefix string, exactly as it should be used (e.g., 'Who is', 'What were')."
                        chat_history = [{"role": "user", "parts": [{"text": gemini_prompt}]}]
                        payload = {"contents": chat_history}
                        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"

                        async with aiohttp.ClientSession() as session:
                            async with session.post(api_url, headers={'Content-Type': 'application/json'}, json=payload) as response:
                                if response.status == 200:
                                    gemini_result = await response.json()
                                    if gemini_result.get("candidates") and len(gemini_result["candidates"]) > 0 and \
                                       gemini_result["candidates"][0].get("content") and \
                                       gemini_result["candidates"][0]["content"].get("parts") and \
                                       len(gemini_result["candidates"][0]["content"]["parts"]) > 0:
                                        
                                        generated_text = gemini_result["candidates"][0]["content"]["parts"][0]["text"].strip()
                                        valid_prefixes = ("what is", "who is", "what are", "who are", "what was", "who was", "what were", "who were")
                                        if generated_text.lower() in valid_prefixes:
                                            determined_prefix = generated_text
                                        else:
                                            print(f"Gemini returned unexpected prefix: '{generated_text}'. Using default.")
                                    else:
                                        print("Gemini response structure unexpected for prefix determination. Using default.")
                                else:
                                    print(f"Gemini API call failed for prefix determination with status {response.status}. Using default.")
                    except Exception as e:
                        print(f"Error calling Gemini API for prefix determination: {e}. Using default.")
                else:
                    print("GEMINI_API_KEY not set. Cannot determine dynamic prefixes. Using default.")

                is_daily_double = question_data.get("daily_double", False)
                game.current_wager = question_data['value']

                if is_daily_double:
                    await interaction.followup.send(
                        f"**DAILY DOUBLE!** {game.player.display_name}, you found the Daily Double!\n"
                        f"Your current score is **{'-' if game.score < 0 else ''}${abs(game.score)}**."
                    )

                    max_wager = max(2000, game.score) if game.score >= 0 else 2000
                    print(f"DEBUG: Player score: {game.score}, Calculated max_wager: {max_wager}")
                    
                    wager_prompt_message = await interaction.channel.send(
                        f"{game.player.display_name}, please enter your wager. "
                        f"You can wager any amount up to **${max_wager}** (must be positive)."
                    )

                    def check_wager(m: discord.Message):
                        return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id and m.content.isdigit()

                    try:
                        wager_msg = await self.view.bot.wait_for('message', check=check_wager, timeout=30.0)
                        wager_input = int(wager_msg.content)
                        print(f"DEBUG: User entered wager: {wager_input}")

                        if wager_input <= 0:
                            await interaction.channel.send("Your wager must be a positive amount. Defaulting to $500.", delete_after=5)
                            game.current_wager = 500
                            print("DEBUG: Wager defaulted to 500 (<=0)")
                        elif wager_input > max_wager:
                            await interaction.channel.send(f"Your wager exceeds the maximum allowed (${max_wager}). Defaulting to max wager.", delete_after=5)
                            game.current_wager = max_wager
                            print(f"DEBUG: Wager defaulted to max_wager ({max_wager})")
                        else:
                            game.current_wager = wager_input
                            print(f"DEBUG: Wager set to user input: {game.current_wager}")
                        
                        try:
                            await wager_prompt_message.delete()
                            await wager_msg.delete()
                        except discord.errors.Forbidden:
                            print("WARNING: Missing permissions to delete wager messages. Please ensure the bot has 'Manage Messages' permission.")
                        except Exception as delete_e:
                            print(f"WARNING: An unexpected error occurred during message deletion: {delete_e}")

                    except asyncio.TimeoutError:
                        print("DEBUG: Wager input timed out.")
                        await interaction.channel.send("Time's up! You didn't enter a wager. Defaulting to $500.", delete_after=5)
                        game.current_wager = 500
                    except Exception as e:
                        print(f"DEBUG: Error getting wager (before deletion attempt): {e}")
                        await interaction.channel.send("An error occurred while getting your wager. Defaulting to $500.", delete_after=5)
                        game.current_wager = 500
                    
                    print(f"DEBUG: Final game.current_wager before sending question: {game.current_wager}")
                    await interaction.followup.send(
                        f"You wagered **${game.current_wager}**.\n*For the Daily Double:*\n**{question_data['question']}**"
                    )
                else:
                    await interaction.followup.send(
                        f"*For ${question_data['value']}:*\n**{question_data['question']}**"
                    )

                valid_user_prefixes = (
                    "what is", "who is", "what are", "who are",
                    "what was", "who was", "what were", "who were"
                )

                def check_answer(m: discord.Message):
                    if not (m.channel.id == interaction.channel.id and m.author.id == interaction.user.id):
                        return False
                    
                    msg_content_lower = m.content.lower()
                    for prefix in valid_user_prefixes:
                        if msg_content_lower.startswith(prefix):
                            return True
                    return False

                try:
                    user_answer_msg = await self.view.bot.wait_for('message', check=check_answer, timeout=30.0)
                    user_raw_answer = user_answer_msg.content.lower()

                    matched_prefix_len = 0
                    for prefix in valid_user_prefixes:
                        if user_raw_answer.startswith(prefix):
                            matched_prefix_len = len(prefix)
                            break
                    
                    processed_user_answer = user_raw_answer[matched_prefix_len:].strip()
                    
                    correct_answer_raw_lower = question_data['answer'].lower()
                    correct_answer_for_comparison = re.sub(r'\s*\(.*\)', '', correct_answer_raw_lower).strip()

                    is_correct = False
                    if processed_user_answer == correct_answer_for_comparison:
                        is_correct = True
                    else:
                        user_words = set(re.findall(r'\b\w+\b', processed_user_answer))
                        correct_words_full = set(re.findall(r'\b\w+\b', correct_answer_for_comparison))
                        question_words = set(re.findall(r'\b\w+\b', question_data['question'].lower()))

                        significant_correct_words = [word for word in correct_words_full if word not in question_words]

                        if len(user_words) == 1 and list(user_words)[0] in significant_correct_words:
                            is_correct = True
                        else:
                            for user_word in user_words:
                                for sig_correct_word in significant_correct_words:
                                    similarity = calculate_word_similarity(user_word, sig_correct_word)
                                    if similarity >= 70.0:
                                        is_correct = True
                                        break
                                if is_correct:
                                    break
                    
                    if is_correct:
                        game.score += game.current_wager
                        await interaction.followup.send(
                            f"✅ Correct, {game.player.display_name}! Your score is now **{'-' if game.score < 0 else ''}${abs(game.score)}**."
                        )
                    else:
                        game.score -= game.current_wager
                        full_correct_answer = f'"{determined_prefix} {question_data["answer"]}"'.strip()
                        await interaction.followup.send(
                            f"❌ Incorrect, {game.player.display_name}! The correct answer was: "
                            f"**__{full_correct_answer}__**. Your score is now **{'-' if game.score < 0 else ''}${abs(game.score)}**."
                        )

                except asyncio.TimeoutError:
                    full_correct_answer = f'"{determined_prefix} {question_data["answer"]}"'.strip()
                    await interaction.followup.send(
                        f"⏰ Time's up, {game.player.display_name}! You didn't answer in time for '${question_data['value']}' question. The correct answer was: "
                        f"**__{full_correct_answer}__**."
                    )
                except Exception as e:
                    print(f"Error waiting for answer: {e}")
                    await interaction.followup.send("An unexpected error occurred while waiting for your answer.")
                finally:
                    game.current_question = None
                    game.current_wager = 0

                    current_phase_completed = False
                    if game.game_phase == "NORMAL_JEOPARDY" and game.is_all_questions_guessed("normal_jeopardy"):
                        current_phase_completed = True
                        game.game_phase = "DOUBLE_JEOPARDY"
                        await interaction.channel.send(f"**Double Jeopardy!** All normal jeopardy questions have been answered. Get ready for new challenges, {game.player.display_name}!")
                    elif game.game_phase == "DOUBLE_JEOPARDY" and game.is_all_questions_guessed("double_jeopardy"):
                        current_phase_completed = True
                        
                        if game.score <= 0:
                            await interaction.channel.send(
                                f"Thank you for playing Jeopardy, {game.player.display_name}! "
                                f"Your balance is **${game.score}**, and so here's where your game ends. "
                                "We hope to see you in Final Jeopardy very soon!"
                            )
                            if game.channel_id in active_jeopardy_games:
                                del active_jeopardy_games[game.channel_id]
                            view.stop()
                            return

                        game.game_phase = "FINAL_JEOPARDY"
                        await interaction.channel.send(f"**Final Jeopardy!** All double jeopardy questions have been answered. Get ready for the final round, {game.player.display_name}!")

                        final_max_wager = max(2000, game.score)
                        wager_prompt_message = await interaction.channel.send(
                            f"{game.player.display_name}, your current score is **{'-' if game.score < 0 else ''}${abs(game.score)}**. "
                            f"Please enter your Final Jeopardy wager. You can wager any amount up to **${final_max_wager}** (must be positive)."
                        )

                        def check_final_wager(m: discord.Message):
                            return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id and m.content.isdigit()

                        try:
                            final_wager_msg = await self.view.bot.wait_for('message', check=check_final_wager, timeout=60.0)
                            final_wager_input = int(final_wager_msg.content)

                            if final_wager_input <= 0:
                                await interaction.channel.send("Your wager must be a positive amount. Defaulting to $1.", delete_after=5)
                                game.current_wager = 1
                            elif final_wager_input > final_max_wager:
                                await interaction.channel.send(f"Your wager exceeds the maximum allowed (${final_max_wager}). Defaulting to max wager.", delete_after=5)
                                game.current_wager = final_max_wager
                            else:
                                game.current_wager = final_wager_input
                            
                            try:
                                await wager_prompt_message.delete()
                                await final_wager_msg.delete()
                            except discord.errors.Forbidden:
                                print("WARNING: Missing permissions to delete wager messages.")
                            except Exception as delete_e:
                                print(f"WARNING: An unexpected error occurred during message deletion: {delete_e}")

                        except asyncio.TimeoutError:
                            await interaction.channel.send("Time's up! You didn't enter a wager. Defaulting to $0.", delete_after=5)
                            game.current_wager = 0
                        except Exception as e:
                            print(f"Error getting Final Jeopardy wager: {e}")
                            await interaction.channel.send("An error occurred while getting your wager. Defaulting to $0.", delete_after=5)
                            game.current_wager = 0

                        final_question_data = game.final_jeopardy_data.get("final_jeopardy")
                        if final_question_data:
                            await interaction.channel.send(
                                f"Your wager: **${game.current_wager}**.\n\n"
                                f"**Final Jeopardy Category:** {final_question_data['category']}\n\n"
                                f"**The Clue:** {final_question_data['question']}"
                            )

                            def check_final_answer(m: discord.Message):
                                return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id

                            try:
                                final_user_answer_msg = await self.view.bot.wait_for('message', check=check_final_answer, timeout=60.0)
                                final_user_raw_answer = final_user_answer_msg.content.lower().strip()

                                final_correct_answer_raw_lower = final_question_data['answer'].lower()
                                final_correct_answer_for_comparison = re.sub(r'\s*\(.*\)', '', final_correct_answer_raw_lower).strip()

                                final_is_correct = False
                                if final_user_raw_answer == final_correct_answer_for_comparison:
                                    final_is_correct = True
                                else:
                                    final_user_words = set(re.findall(r'\b\w+\b', final_user_raw_answer))
                                    final_correct_words_full = set(re.findall(r'\b\w+\b', final_correct_answer_for_comparison))
                                    
                                    final_significant_correct_words = list(final_correct_words_full)

                                    for user_word in final_user_words:
                                        for sig_correct_word in final_significant_correct_words:
                                            similarity = calculate_word_similarity(user_word, sig_correct_word)
                                            if similarity >= 70.0:
                                                final_is_correct = True
                                                break
                                        if final_is_correct:
                                            break
                                
                                if final_is_correct:
                                    game.score += game.current_wager
                                    await interaction.channel.send(
                                        f"✅ Correct, {game.player.display_name}! You answered correctly and gained **${game.current_wager}**."
                                    )
                                else:
                                    game.score -= game.current_wager
                                    await interaction.channel.send(
                                        f"❌ Incorrect, {game.player.display_name}! The correct answer was: "
                                        f"**__{final_question_data['answer']}__**. You lost **${game.current_wager}**."
                                    )
                            except asyncio.TimeoutError:
                                await interaction.channel.send(
                                    f"⏰ Time's up, {game.player.display_name}! You didn't answer in time for Final Jeopardy. "
                                    f"The correct answer was: **__{final_question_data['answer']}__**."
                                )
                            except Exception as e:
                                print(f"Error waiting for Final Jeopardy answer: {e}")
                                await interaction.channel.send("An unexpected error occurred while waiting for your Final Jeopardy answer.")
                        
                        await interaction.channel.send(
                            f"Final Score for {game.player.display_name}: **{'-' if game.score < 0 else ''}${abs(game.score)}**.\n"
                            "Thank you for playing Jeopardy!"
                        )
                        if game.score > 0:
                            await update_user_kekchipz(interaction.guild.id, interaction.user.id, game.score)

                        if game.channel.id in active_jeopardy_games:
                            del active_jeopardy_games[game.channel_id]
                        view.stop()
                        return

                    view.stop()

                    new_jeopardy_view = Jeopardy.JeopardyGameView(game) # Refer to JeopardyGameView
                    new_jeopardy_view.add_board_components()
                    
                    board_message_content = ""
                    if game.game_phase == "NORMAL_JEOPARDY":
                        board_message_content = f"**{game.player.display_name}**'s Score: **{'-' if game.score < 0 else ''}${abs(game.score)}**\n\n" \
                                                "Select a category and value from the dropdowns below!"
                    elif game.game_phase == "DOUBLE_JEOPARDY":
                        board_message_content = f"**{game.player.display_name}**'s Score: **{'-' if game.score < 0 else ''}${abs(game.score)}**\n\n" \
                                                "**Double Jeopardy!** Select a category and value from the dropdowns below!"
                    
                    if board_message_content:
                        game.board_message = await interaction.channel.send(
                            content=board_message_content,
                            view=new_jeopardy_view
                        )
                    else:
                        if jeopardy_view.children:
                            for item in jeopardy_view.children:
                                item.disabled = True
                            await interaction.channel.send("Game concluded. No more questions.", view=jeopardy_view)
                        else:
                            await interaction.channel.send("Game concluded. No more questions.")

            else:
                await interaction.response.send_message(
                    f"Question '{self.category_name}' for ${selected_value} not found or already picked. Please select another.",
                    ephemeral=True
                )


    class JeopardyGameView(discord.ui.View):
        """The Discord UI View that holds the interactive Jeopardy board dropdowns."""
        def __init__(self, game: 'Jeopardy.NewJeopardyGame'): # Corrected type hint
            super().__init__(timeout=900)
            self.game = game
            self._selected_category = None
            self._selected_value = None
            self.message = None

        def add_board_components(self):
            self.clear_items()

            categories_to_process = []
            if self.game.game_phase == "NORMAL_JEOPARDY":
                categories_to_process = self.game.normal_jeopardy_data.get("normal_jeopardy", [])
            elif self.game.game_phase == "DOUBLE_JEOPARDY":
                categories_to_process = self.game.double_jeopardy_data.get("double_data", [])
            else:
                return

            for i, category_data in enumerate(categories_to_process):
                if i >= 5:
                    break

                category_name = category_data["category"]
                options = [
                    discord.SelectOption(label=f"${q['value']}", value=str(q['value']))
                    for q in category_data["questions"] if not q["guessed"]
                ]

                if options:
                    # Refer to CategoryValueSelect via Jeopardy.CategoryValueSelect
                    self.add_item(Jeopardy.CategoryValueSelect(
                        category_name,
                        options,
                        f"Pick for {category_name}",
                        row=i
                    ))

        async def on_timeout(self):
            if self.game.board_message:
                try:
                    await self.game.board_message.edit(content="Jeopardy game timed out due to inactivity.", view=None)
                except discord.errors.NotFound:
                    print("WARNING: Board message not found during timeout, likely already deleted.")
                except Exception as e:
                    print(f"WARNING: An error occurred editing board message on timeout: {e}")
            
            if self.game.channel_id in active_jeopardy_games:
                del active_jeopardy_games[self.game.channel_id]
            print(f"Jeopardy game in channel {self.game.channel_id} timed out.")


    class NewJeopardyGame:
        """
        A placeholder class for the new Jeopardy game.
        Currently, its primary function is to fetch and parse the Jeopardy data
        from the external API and store it in separate attributes.
        """
        def __init__(self, channel_id: int, player: discord.User):
            self.channel_id = channel_id
            self.player = player
            self.score = 0
            self.normal_jeopardy_data = None
            self.double_jeopardy_data = None
            self.final_jeopardy_data = None
            self.jeopardy_data_url = "https://serenekeks.com/serene_bot_games.php"
            self.board_message = None
            self.current_question = None
            self.current_wager = 0
            self.game_phase = "NORMAL_JEOPARDY"

        async def fetch_and_parse_jeopardy_data(self) -> bool:
            try:
                params = {"jeopardy": "true"}
                encoded_params = urllib.parse.urlencode(params)
                full_url = f"{self.jeopardy_data_url}?{encoded_params}"

                async with aiohttp.ClientSession() as session:
                    async with session.get(full_url) as response:
                        if response.status == 200:
                            full_data = await response.json()
                            
                            for category_type in ["normal_jeopardy", "double_jeopardy"]:
                                if category_type in full_data:
                                    for category in full_data[category_type]:
                                        for question_data in category["questions"]:
                                            question_data["guessed"] = False
                                            question_data["category"] = category["category"]
                            if "final_jeopardy" in full_data:
                                full_data["final_jeopardy"]["guessed"] = False
                                full_data["final_jeopardy"]["category"] = full_data["final_jeopardy"].get("category", "Final Jeopardy")

                            self.normal_jeopardy_data = {"normal_jeopardy": full_data.get("normal_jeopardy", [])}
                            self.double_jeopardy_data = {"double_data": full_data.get("double_jeopardy", [])}
                            self.final_jeopardy_data = {"final_jeopardy": full_data.get("final_jeopardy", {})}
                            
                            print(f"Jeopardy data fetched and parsed for channel {self.channel_id}")
                            return True
                        else:
                            print(f"Error fetching Jeopardy data: HTTP Status {response.status}")
                            return False
            except Exception as e:
                print(f"Error loading Jeopardy data: {e}")
                return False

        def is_all_questions_guessed(self, phase_type: str) -> bool:
            data_to_check = []
            if phase_type == "normal_jeopardy":
                data_to_check = self.normal_jeopardy_data.get("normal_jeopardy", [])
            elif phase_type == "double_jeopardy":
                data_to_check = self.double_jeopardy_data.get("double_data", [])
            else:
                return False

            if not data_to_check:
                return True

            for category in data_to_check:
                for question_data in category["questions"]:
                    if not question_data["guessed"]:
                        return False
            return True

async def setup(bot):
    await bot.add_cog(Jeopardy(bot))

