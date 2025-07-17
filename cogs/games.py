# cogs/games.py
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

# Import global game state storage and database functions from the main bot file
# Adjust these imports based on where you decide to store them
from bot import (
    active_tictactoe_games, active_jeopardy_games,
    active_blackjack_games, active_texasholdem_games,
    add_user_to_db_if_not_exists, update_user_kekchipz,
    calculate_word_similarity # Assuming this helper is also moved to bot.py or utils.py
)

class Games(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Create a subcommand group for /serene within this cog
        # This needs to be done once per cog that uses a group.
        # The parent group 'serene' is defined in bot.py
        self.serene_group = app_commands.Group(name="serene", description="Commands for Serene Bot.")
        self.bot.tree.add_command(self.serene_group)


    # --- New Jeopardy Game UI Components ---
    # (All Jeopardy-related classes and their methods would go here)
    class CategoryValueSelect(discord.ui.Select):
        # ... (Your existing CategoryValueSelect class code) ...
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
            view: 'JeopardyGameView' = self.view
            game: 'NewJeopardyGame' = view.game

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
                        if q_data["value"] == selected_value and not q_data["guessed"]:
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
                        wager_msg = await self.view.bot.wait_for('message', check=check_wager, timeout=30.0) # Use self.view.bot
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
                    user_answer_msg = await self.view.bot.wait_for('message', check=check_answer, timeout=30.0) # Use self.view.bot
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
                                del active_jeopardy_games[game.channel.id]
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
                            final_wager_msg = await self.view.bot.wait_for('message', check=check_final_wager, timeout=60.0) # Use self.view.bot
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
                                final_user_answer_msg = await self.view.bot.wait_for('message', check=check_final_answer, timeout=60.0) # Use self.view.bot
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
                        else:
                            await interaction.channel.send("Could not load Final Jeopardy question data.")
                        
                        await interaction.channel.send(
                            f"Final Score for {game.player.display_name}: **{'-' if game.score < 0 else ''}${abs(game.score)}**.\n"
                            "Thank you for playing Jeopardy!"
                        )
                        if game.score > 0:
                            await update_user_kekchipz(interaction.guild.id, interaction.user.id, game.score)

                        if game.channel.id in active_jeopardy_games:
                            del active_jeopardy_games[game.channel.id]
                        view.stop()
                        return

                    view.stop()

                    new_jeopardy_view = JeopardyGameView(game)
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
                        if view.children:
                            for item in view.children:
                                item.disabled = True
                            await interaction.channel.send("Game concluded. No more questions.", view=view)
                        else:
                            await interaction.channel.send("Game concluded. No more questions.")

            else:
                await interaction.response.send_message(
                    f"Question '{self.category_name}' for ${selected_value} not found or already picked. Please select another.",
                    ephemeral=True
                )


    class JeopardyGameView(discord.ui.View):
        # ... (Your existing JeopardyGameView class code) ...
        def __init__(self, game: 'NewJeopardyGame'):
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
                    self.add_item(Games.CategoryValueSelect( # Refer to CategoryValueSelect via Games.CategoryValueSelect
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
        # ... (Your existing NewJeopardyGame class code) ...
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


    # --- Tic-Tac-Toe Game Classes ---
    # (All TicTacToe-related classes and their methods would go here)
    class TicTacToeButton(discord.ui.Button):
        # ... (Your existing TicTacToeButton class code) ...
        def __init__(self, row: int, col: int, player_mark: str = "⬜"):
            super().__init__(style=discord.ButtonStyle.secondary, label=player_mark, row=row)
            self.row = row
            self.col = col
            self.player_mark = player_mark

        async def callback(self, interaction: discord.Interaction):
            view: 'TicTacToeView' = self.view
            
            if interaction.user.id != view.players[view.current_player].id:
                await interaction.response.send_message("It's not your turn!", ephemeral=True)
                return

            if view.board[self.row][self.col] != " ":
                await interaction.response.send_message("That spot is already taken!", ephemeral=True)
                return

            self.player_mark = view.current_player
            self.label = self.player_mark
            
            if self.player_mark == "X":
                self.style = discord.ButtonStyle.primary
            else:
                self.style = discord.ButtonStyle.danger
                
            self.disabled = True
            view.board[self.row][self.col] = self.player_mark

            await interaction.response.defer()

            if view._check_winner():
                winner_player = view.players[view.current_player]
                loser_player = view.players["O" if view.current_player == "X" else "X"]

                if winner_player.id == interaction.user.id:
                    await update_user_kekchipz(interaction.guild.id, interaction.user.id, 100)
                elif loser_player.id == interaction.user.id:
                    await update_user_kekchipz(interaction.guild.id, interaction.user.id, 10)

                await interaction.edit_original_response(
                    content=f"🎉 **{winner_player.display_name} wins!** 🎉",
                    embed=view._start_game_message(),
                    view=view._end_game()
                )
                del active_tictactoe_games[interaction.channel.id]
            elif view._check_draw():
                await update_user_kekchipz(interaction.guild.id, interaction.user.id, 25)
                await interaction.edit_original_response(
                    content="It's a **draw!** 🤝",
                    embed=view._start_game_message(),
                    view=view._end_game()
                )
                del active_tictactoe_games[interaction.channel.id]
            else:
                view.current_player = "O" if view.current_player == "X" else "X"
                next_player_obj = view.players[view.current_player]

                await interaction.edit_original_response(
                    content=f"It's **{next_player_obj.display_name}**'s turn ({view.current_player})",
                    embed=view._start_game_message(),
                    view=view
                )

                if view.players[view.current_player].id == self.view.bot.user.id: # Use self.view.bot.user.id
                    await asyncio.sleep(1)
                    await view._bot_make_move(interaction)


    class TicTacToeView(discord.ui.View):
        # ... (Your existing TicTacToeView class code) ...
        def __init__(self, player_x: discord.User, player_o: discord.User):
            super().__init__(timeout=300)
            self.players = {"X": player_x, "O": player_o}
            self.current_player = "X"
            self.board = [[" ", " ", " "], [" ", " ", " "], [" ", " ", " "]]
            self.message = None

            self._create_board()

        def _create_board(self):
            for row in range(3):
                for col in range(3):
                    self.add_item(Games.TicTacToeButton(row, col, player_mark="⬜")) # Refer to TicTacToeButton via Games.TicTacToeButton

        def _update_board_display(self):
            for item in self.children:
                if isinstance(item, Games.TicTacToeButton): # Refer to TicTacToeButton via Games.TicTacToeButton
                    mark = self.board[item.row][item.col]
                    item.label = mark
                    if mark == "X":
                        item.style = discord.ButtonStyle.primary
                    elif mark == "O":
                        item.style = discord.ButtonStyle.danger
                    else:
                        item.style = discord.ButtonStyle.secondary
                    item.disabled = mark != " "

        def _start_game_message(self) -> discord.Embed:
            embed = discord.Embed(
                title="Tic-Tac-Toe",
                description=f"**{self.players['X'].display_name}** (X) vs. **{self.players['O'].display_name}** (O)\n"
                            f"Current Turn: **{self.players[self.current_player].display_name}** ({self.current_player})",
                color=discord.Color.blue()
            )
            board_str = ""
            for r in range(3):
                for c in range(3):
                    mark = self.board[r][c]
                    if mark == "X":
                        board_str += "🇽 "
                    elif mark == "O":
                        board_str += "🅾️ "
                    else:
                        board_str += "⬜ "
                board_str += "\n"
            embed.add_field(name="Board", value=board_str, inline=False)
            return embed

        def _check_win_state(self, board, player) -> bool:
            for i in range(3):
                if all(board[i][j] == player for j in range(3)): return True
                if all(board[j][i] == player for j in range(3)): return True
            if all(board[k][k] == player for k in range(3)): return True
            if all(board[k][2-k] == player for k in range(3)): return True
            return False

        def _check_winner(self) -> bool:
            return self._check_win_state(self.board, self.current_player)

        def _check_draw(self) -> bool:
            for row in self.board:
                if " " in row:
                    return False
            return not self._check_winner()

        def _get_empty_cells(self, board):
            empty_cells = []
            for r in range(3):
                for c in range(3):
                    if board[r][c] == " ":
                        empty_cells.append((r, c))
            return empty_cells

        def _minimax(self, board, is_maximizing_player):
            if self._check_win_state(board, "O"):
                return 1
            if self._check_win_state(board, "X"):
                return -1
            if not self._get_empty_cells(board):
                return 0

            if is_maximizing_player:
                best_eval = -float('inf')
                for r, c in self._get_empty_cells(board):
                    board[r][c] = "O"
                    evaluation = self._minimax(board, False)
                    board[r][c] = " "
                    best_eval = max(best_eval, evaluation)
                return best_eval
            else:
                best_eval = float('inf')
                for r, c in self._get_empty_cells(board):
                    board[r][c] = "X"
                    evaluation = self._minimax(board, True)
                    board[r][c] = " "
                    best_eval = min(best_eval, evaluation)
                return best_eval

        async def _bot_make_move(self, interaction: discord.Interaction):
            best_score = -float('inf')
            best_move = None

            for r, c in self._get_empty_cells(self.board):
                self.board[r][c] = "O"
                score = self._minimax(self.board, False)
                self.board[r][c] = " "

                if score > best_score:
                    best_score = score
                    best_move = (r, c)
            
            if best_move:
                row, col = best_move
                self.board[row][col] = "O"

                for item in self.children:
                    if isinstance(item, Games.TicTacToeButton) and item.row == row and item.col == col: # Refer to TicTacToeButton via Games.TicTacToeButton
                        item.label = "O"
                        item.style = discord.ButtonStyle.danger
                        item.disabled = True
                        break
                
                if self._check_winner():
                    winner_player = self.players[self.current_player]
                    loser_player = self.players["X" if self.current_player == "O" else "O"]

                    if winner_player.id == interaction.user.id:
                        await update_user_kekchipz(interaction.guild.id, interaction.user.id, 100)
                    elif loser_player.id == interaction.user.id:
                        await update_user_kekchipz(interaction.guild.id, interaction.user.id, 10)

                    await interaction.edit_original_response(
                        content=f"🎉 **{winner_player.display_name} wins!** 🎉",
                        embed=self._start_game_message(),
                        view=self._end_game()
                    )
                    del active_tictactoe_games[interaction.channel.id]
                elif self._check_draw():
                    await update_user_kekchipz(interaction.guild.id, interaction.user.id, 25)
                    await interaction.edit_original_response(
                        content="It's a **draw!** 🤝",
                        embed=self._start_game_message(),
                        view=self._end_game()
                    )
                    del active_tictactoe_games[interaction.channel.id]
                else:
                    self.current_player = "X"
                    next_player_obj = self.players[self.current_player]
                    await interaction.edit_original_response(
                        content=f"It's **{next_player_obj.display_name}**'s turn ({self.current_player})",
                        embed=self._start_game_message(),
                        view=self
                    )

        def _end_game(self):
            for item in self.children:
                item.disabled = True
            return self

        async def on_timeout(self):
            if self.message:
                try:
                    await self.message.edit(content="Game timed out due to inactivity.", view=None, embed=None)
                except discord.errors.NotFound:
                    print("WARNING: Board message not found during timeout, likely already deleted.")
                except Exception as e:
                    print(f"WARNING: An error occurred editing board message on timeout: {e}")
            
            # Need to get the channel ID from the interaction or stored game state
            # Assuming the game object or channel_id is accessible from the view's context
            # This might require passing channel_id to the view's constructor if not already done.
            # For now, let's assume it's available via self.game.channel_id if TicTacToeView also gets a game object.
            # If not, you'd need to adjust how active_tictactoe_games is managed.
            # For simplicity, if it's a direct view, you might need to pass channel_id explicitly.
            # Let's assume the main bot.py will handle cleanup based on the message.
            # Or, for now, we'll use a placeholder.
            # Fix: The original code used interaction.channel.id, which is not available in on_timeout.
            # The TicTacToeView needs to store the channel_id it's operating in.
            # Let's add channel_id to TicTacToeView's __init__
            if hasattr(self, 'channel_id') and self.channel_id in active_tictactoe_games:
                del active_tictactoe_games[self.channel_id]
            print(f"Tic-Tac-Toe game in channel {self.message.channel.id if self.message else 'unknown'} timed out.")


    # --- New Blackjack Game UI Components ---
    # (All Blackjack-related classes and their methods would go here)
    class BlackjackGameView(discord.ui.View):
        # ... (Your existing BlackjackGameView class code) ...
        def __init__(self, game: 'BlackjackGame'):
            super().__init__(timeout=300)
            self.game = game
            self.message = None

        async def _update_game_message(self, interaction: discord.Interaction, embed: discord.Embed, view_to_use: discord.ui.View = None):
            try:
                await interaction.edit_original_response(embed=embed, view=view_to_use)
            except discord.errors.NotFound:
                print("WARNING: Original interaction message not found during edit, likely already deleted or inaccessible.")
            except Exception as e:
                print(f"WARNING: An error occurred editing original interaction response: {e}")

        def _end_game_buttons(self):
            for item in self.children:
                if item.custom_id in ["blackjack_hit", "blackjack_stay"]:
                    item.disabled = True
                elif item.custom_id == "blackjack_play_again":
                    item.disabled = False
            return self

        async def on_timeout(self):
            if self.message:
                try:
                    for item in self.children:
                        item.disabled = True
                    if not any(item.custom_id == "blackjack_play_again" for item in self.children):
                        self.add_item(discord.ui.Button(label="Play Again", style=discord.ButtonStyle.blurple, custom_id="blackjack_play_again"))
                    
                    for item in self.children:
                        if item.custom_id == "blackjack_play_again":
                            item.disabled = False
                            break

                    await self.message.edit(content="Blackjack game timed out due to inactivity. Click 'Play Again' to start a new game.", view=self, embed=self.message.embed)

                except discord.errors.NotFound:
                    print("WARNING: Game message not found during timeout, likely already deleted.")
                except Exception as e:
                    print(f"WARNING: An error occurred editing game message on timeout: {e}")
            
            if self.game.channel_id in active_blackjack_games:
                pass
            print(f"Blackjack game in channel {self.game.channel_id} timed out.")


        @discord.ui.button(label="Hit", style=discord.ButtonStyle.green, custom_id="blackjack_hit")
        async def hit_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.game.player.id:
                await interaction.response.send_message("This is not your Blackjack game!", ephemeral=True)
                return
            
            await interaction.response.defer()

            self.game.player_hand.append(self.game.deal_card())
            player_value = self.game.calculate_hand_value(self.game.player_hand)

            if player_value > 21:
                final_embed = self.game._create_game_embed(reveal_dealer=True, result_message="BUST! Serene wins.")
                self._end_game_buttons()
                await self._update_game_message(interaction, final_embed, view_to_use=self)
                await update_user_kekchipz(interaction.guild.id, interaction.user.id, -50)
                del active_blackjack_games[self.game.channel_id]
            else:
                new_embed = self.game._create_game_embed()
                await self._update_game_message(interaction, new_embed, view_to_use=self)

        @discord.ui.button(label="Stay", style=discord.ButtonStyle.red, custom_id="blackjack_stay")
        async def stay_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.game.player.id:
                await interaction.response.send_message("This is not your Blackjack game!", ephemeral=True)
                return
            
            await interaction.response.defer()

            player_value = self.game.calculate_hand_value(self.game.player_hand)
            serene_value = self.game.calculate_hand_value(self.game.dealer_hand)

            while serene_value < 17:
                self.game.dealer_hand.append(self.game.deal_card())
                serene_value = self.game.calculate_hand_value(self.game.dealer_hand)
                temp_embed = self.game._create_game_embed(reveal_dealer=True)
                await self._update_game_message(interaction, temp_embed, view_to_use=self)
                await asyncio.sleep(1)

            result_message = ""
            kekchipz_change = 0

            if serene_value > 21:
                result_message = "Serene busts! You win!"
                kekchipz_change = 100
            elif player_value > serene_value:
                result_message = "You win!"
                kekchipz_change = 100
            elif serene_value > player_value:
                result_message = "Serene wins!"
                kekchipz_change = -50
            else:
                result_message = "It's a push (tie)!"
                kekchipz_change = 0

            final_embed = self.game._create_game_embed(reveal_dealer=True, result_message=result_message)
            self._end_game_buttons()
            await self._update_game_message(interaction, final_embed, view_to_use=self)
            await update_user_kekchipz(interaction.guild.id, interaction.user.id, kekchipz_change)
            
            del active_blackjack_games[self.game.channel_id]

        @discord.ui.button(label="Play Again", style=discord.ButtonStyle.blurple, custom_id="blackjack_play_again", disabled=True)
        async def play_again_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.game.player.id:
                await interaction.response.send_message("This is not your Blackjack game!", ephemeral=True)
                return

            await interaction.response.defer()

            await self.game.reset_game()
            self.game.player_hand = [self.game.deal_card(), self.game.deal_card()]
            self.game.dealer_hand = [self.game.deal_card(), self.game.deal_card()]

            for item in self.children:
                if item.custom_id in ["blackjack_hit", "blackjack_stay"]:
                    item.disabled = False
                elif item.custom_id == "blackjack_play_again":
                    item.disabled = True

            initial_embed = self.game._create_game_embed()

            player_card_codes = [card['code'] for card in self.game.player_hand if 'code' in card]
            player_combo_url = f"{self.game.game_data_url}?combo={','.join(player_card_codes)}" if player_card_codes else ""

            serene_display_cards = []
            if self.game.dealer_hand and 'code' in self.game.dealer_hand[0]:
                serene_display_cards.append(self.game.dealer_hand[0]['code'])
            serene_combo_url = f"{self.game.game_data_url}?combo={','.join(serene_display_cards)}" if serene_display_cards else ""
            
            cache_buster = int(time.time() * 1000)
            player_combo_url += f"&_cb={cache_buster}"
            serene_combo_url += f"&_cb={cache_buster}"

            if player_combo_url:
                initial_embed.set_image(url=player_combo_url)
            
            if serene_combo_url:
                initial_embed.set_thumbnail(url=serene_combo_url)

            try:
                await interaction.edit_original_response(embed=initial_embed, view=self)
                active_blackjack_games[self.game.channel_id] = self
            except discord.errors.NotFound:
                print("WARNING: Original game message not found during 'Play Again' edit.")
                await interaction.followup.send("Could not restart game. Please try `/serene game blackjack` again.", ephemeral=True)
                if self.game.channel_id in active_blackjack_games:
                    del active_blackjack_games[self.game.channel_id]
            except Exception as e:
                print(f"WARNING: An error occurred during 'Play Again' edit: {e}")
                await interaction.followup.send("An error occurred while restarting the game.", ephemeral=True)
                if self.game.channel_id in active_blackjack_games:
                    del active_blackjack_games[self.game.channel_id]


    class BlackjackGame:
        # ... (Your existing BlackjackGame class code) ...
        def __init__(self, channel_id: int, player: discord.User):
            self.channel_id = channel_id
            self.player = player
            self.deck = []
            self.player_hand = []
            self.dealer_hand = []
            self.game_message = None
            self.game_data_url = "https://serenekeks.com/serene_bot_games.php"
            self.game_over = False

        def _build_deck_from_codes(self, card_codes: list[str]) -> list[dict]:
            suits = {'S': 'Spades', 'D': 'Diamonds', 'C': 'Clubs', 'H': 'Hearts'}
            ranks = {
                'A': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9,
                'T': 10, 'J': 10, 'Q': 10, 'K': 10
            }
            rank_titles = {
                'A': 'Ace', '2': 'Two', '3': 'Three', '4': 'Four', '5': 'Five',
                '6': 'Six', '7': 'Seven', '8': 'Eight', '9': 'Nine', 'T': 'Ten',
                'J': 'Jack', 'Q': 'Queen', 'K': 'King'
            }
            suit_titles = {
                'S': 'Spades', 'D': 'Diamonds', 'C': 'Clubs', 'H': 'Hearts'
            }

            deck = []
            for code in card_codes:
                if len(code) == 2:
                    rank_code = code[0]
                    suit_code = code[1]
                elif len(code) == 3 and code[0] == '1' and code[1] == '0':
                    rank_code = 'T'
                    suit_code = code[2]
                else:
                    print(f"WARNING: Unrecognized card code format: {code}. Skipping.")
                    continue

                if rank_code not in ranks or suit_code not in suits:
                    print(f"WARNING: Invalid rank or suit code in {code}. Skipping.")
                    continue

                title = f"{rank_titles.get(rank_code, rank_code)} of {suits.get(suit_code, suit_code)}"
                card_number = ranks.get(rank_code, 0)

                deck.append({
                    "title": title,
                    "cardNumber": card_number,
                    "code": code
                })
            return deck

        async def _fetch_and_initialize_deck(self):
            deck_api_url = 'https://deckofcardsapi.com/api/deck/new/draw/?count=52'
            print(f"DEBUG: Fetching deck for Blackjack from {deck_api_url}")
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(deck_api_url) as response:
                        if response.status == 200:
                            data = await response.json()
                            
                            if 'cards' in data and isinstance(data['cards'], list):
                                card_codes = [str(card['code']) for card in data['cards'] if 'code' in card]
                                if card_codes:
                                    self.deck = self._build_deck_from_codes(card_codes)
                                    random.shuffle(self.deck)
                                    print(f"DEBUG: Deck initialized with {len(self.deck)} cards from deckofcardsapi.com for Blackjack.")
                                    return True
                                else:
                                    print(f"ERROR: No card codes found in deckofcardsapi.com response for Blackjack: {data}")
                                    self.deck = self._create_standard_deck_fallback()
                                    return False
                            else:
                                print(f"ERROR: Invalid response structure from deckofcardsapi.com for Blackjack: Missing 'cards' key or not a list. Response: {data}")
                                self.deck = self._create_standard_deck_fallback()
                                return False
                        else:
                            print(f"ERROR: Failed to fetch deck from deckofcardsapi.com for Blackjack: HTTP Status {response.status}. URL: {deck_api_url}")
                            self.deck = self._create_standard_deck_fallback()
                            return False
            except aiohttp.ClientError as e:
                print(f"ERROR: Network error fetching deck from deckofcardsapi.com for Blackjack: {e}. URL: {deck_api_url}")
                self.deck = self._create_standard_deck_fallback()
                return False
            except json.JSONDecodeError as e:
                print(f"ERROR: JSON decode error fetching deck from deckofcardsapi.com for Blackjack: {e}. Response was: {await response.text()}")
                self.deck = self._create_standard_deck_fallback()
                return False
            except Exception as e:
                print(f"ERROR: An unexpected error occurred fetching deck for Blackjack: {e}. URL: {deck_api_url}")
                self.deck = self._create_standard_deck_fallback()
                return False

        def _create_standard_deck_fallback(self) -> list[dict]:
            print("DEBUG: Using standard deck fallback for Blackjack.")
            suits = ['S', 'D', 'C', 'H']
            ranks = {
                'A': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9,
                'T': 10, 'J': 10, 'Q': 10, 'K': 10
            }
            rank_titles = {
                'A': 'Ace', '2': 'Two', '3': 'Three', '4': 'Four', '5': 'Five',
                '6': 'Six', '7': 'Seven', '8': 'Eight', '9': 'Nine', 'T': 'Ten',
                'J': 'Jack', 'Q': 'Queen', 'K': 'King'
            }
            suit_titles = {
                'S': 'Spades', 'D': 'Diamonds', 'C': 'Clubs', 'H': 'Hearts'
            }

            deck = []
            for suit_code in suits:
                for rank_code, num_value in ranks.items():
                    title = f"{rank_titles[rank_code]} of {suit_titles[suit_code]}"
                    card_code = f"{rank_code}{suit_code}"
                    deck.append({
                        "title": title,
                        "cardNumber": num_value,
                        "code": card_code
                    })
            return deck

        def deal_card(self) -> dict:
            if not self.deck:
                print("Warning: Deck is empty, cannot deal more cards.")
                return {"title": "No Card", "cardNumber": 0, "code": "NO_CARD"}
            
            card = random.choice(self.deck)
            self.deck.remove(card)
            return card

        def calculate_hand_value(self, hand: list[dict]) -> int:
            value = 0
            num_aces = 0
            for card in hand:
                card_number = card.get("cardNumber", 0)
                if card_number == 1:
                    num_aces += 1
                    value += 11
                elif card_number >= 10:
                    value += 10
                else:
                    value += card_number
            
            while value > 21 and num_aces > 0:
                value -= 10
                num_aces -= 1
            return value

        def _create_game_embed(self, reveal_dealer: bool = False, result_message: str = None) -> discord.Embed:
            player_value = self.calculate_hand_value(self.player_hand)
            serene_value = self.calculate_hand_value(self.dealer_hand)

            embed = discord.Embed(
                title="Blackjack Game",
                description=f"**{self.player.display_name} vs. Serene**",
                color=discord.Color.dark_green()
            )

            embed.add_field(
                name=f"{self.player.display_name}'s Hand",
                value=f"Value: {player_value}",
                inline=False
            )

            serene_hand_value_str = f"{serene_value}" if reveal_dealer else f"{self.calculate_hand_value([self.dealer_hand[0]])} + ?"
            serene_hand_titles = ', '.join([card['title'] for card in self.dealer_hand]) if reveal_dealer else f"{self.dealer_hand[0]['title']}, [Hidden Card]"
            
            embed.add_field(
                name=f"Serene's Hand (Value: {serene_hand_value_str})",
                value=serene_hand_titles,
                inline=False
            )
            
            if result_message:
                embed.set_footer(text=result_message)
            else:
                embed.set_footer(text="What would you like to do? (Hit or Stand)")
            
            return embed

        async def reset_game(self):
            await self._fetch_and_initialize_deck()
            self.player_hand = []
            self.dealer_hand = []
            self.game_over = False

        async def start_game(self, interaction: discord.Interaction):
            await self._fetch_and_initialize_deck()

            self.player_hand = [self.deal_card(), self.deal_card()]
            self.dealer_hand = [self.deal_card(), self.deal_card()]
            
            game_view = Games.BlackjackGameView(game=self) # Refer to BlackjackGameView via Games.BlackjackGameView
            
            initial_embed = self._create_game_embed()

            player_card_codes = [card['code'] for card in self.player_hand if 'code' in card]
            player_combo_url = f"{self.game_data_url}?combo={','.join(player_card_codes)}" if player_card_codes else ""

            serene_display_cards = []
            if self.dealer_hand and 'code' in self.dealer_hand[0]:
                serene_display_cards.append(self.dealer_hand[0]['code'])
            serene_combo_url = f"{self.game_data_url}?combo={','.join(serene_display_cards)}" if serene_display_cards else ""
            
            cache_buster = int(time.time() * 1000)
            player_combo_url += f"&_cb={cache_buster}"
            serene_combo_url += f"&_cb={cache_buster}"

            if player_combo_url:
                initial_embed.set_image(url=player_combo_url)
            
            if serene_combo_url:
                initial_embed.set_thumbnail(url=serene_combo_url)

            self.game_message = await interaction.followup.send(embed=initial_embed, view=game_view)
            game_view.message = self.game_message
            
            active_blackjack_games[self.channel_id] = game_view


    # --- New Texas Hold 'em Game Classes ---
    # (All TexasHoldEm-related classes and their methods would go here)
    class TexasHoldEmGameView(discord.ui.View):
        # ... (Your existing TexasHoldEmGameView class code) ...
        def __init__(self, game: 'TexasHoldEmGame'):
            super().__init__(timeout=300)
            self.game = game
            self.message = None

        async def _update_game_message(self, interaction: discord.Interaction, embed: discord.Embed, image_bytes: bytes, image_filename: str, view_to_use: discord.ui.View = None):
            try:
                file = discord.File(image_bytes, filename=image_filename)
                await interaction.edit_original_response(embed=embed, attachments=[file], view=view_to_use)
            except discord.errors.NotFound:
                print("WARNING: Original interaction message not found during edit, likely already deleted or inaccessible.")
            except Exception as e:
                print(f"WARNING: An error occurred editing original interaction response with image: {e}")

        def _enable_next_phase_button(self, current_phase: str):
            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True
                    if current_phase == "pre_flop" and item.custom_id == "holdem_flop":
                        item.disabled = False
                    elif current_phase == "flop" and item.custom_id == "holdem_turn":
                        item.disabled = False
                    elif current_phase == "turn" and item.custom_id == "holdem_river":
                        item.disabled = False
                    elif current_phase == "river" and item.custom_id == "holdem_showdown":
                        item.disabled = False
                    elif current_phase == "showdown" and item.custom_id == "holdem_play_again":
                        item.disabled = False

        def _end_game_buttons(self):
            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True
                    if item.custom_id == "holdem_play_again":
                        item.disabled = False
            return self

        async def on_timeout(self):
            if self.message:
                try:
                    for item in self.children:
                        item.disabled = True
                    if not any(item.custom_id == "holdem_play_again" for item in self.children):
                        self.add_item(discord.ui.Button(label="Play Again", style=discord.ButtonStyle.blurple, custom_id="holdem_play_again"))
                    for item in self.children:
                        if item.custom_id == "holdem_play_again":
                            item.disabled = False
                            break
                    await self.message.edit(content="Texas Hold 'em game timed out due to inactivity. Click 'Play Again' to start a new game.", view=self)
                except discord.errors.NotFound:
                    print("WARNING: Game message not found during timeout, likely already deleted.")
                except Exception as e:
                    print(f"WARNING: An error occurred editing game message on timeout: {e}")
            if self.game.channel_id in active_texasholdem_games:
                pass
            print(f"Texas Hold 'em game in channel {self.game.channel_id} timed out.")

        @discord.ui.button(label="Deal Flop", style=discord.ButtonStyle.primary, custom_id="holdem_flop", row=0)
        async def deal_flop_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.game.player.id:
                await interaction.response.send_message("This is not your Texas Hold 'em game!", ephemeral=True)
                return
            
            await interaction.response.defer()

            for item in self.children:
                item.disabled = True
            
            current_embed = self.message.embeds[0] if self.message and self.message.embeds else None
            if current_embed:
                await interaction.edit_original_response(embed=current_embed, view=self)
            else:
                await interaction.edit_original_response(content="Processing...", view=self)

            self.game.deal_flop()
            
            image_bytes, image_filename = await self.game._fetch_game_image()
            
            new_embed = self.game._create_game_embed()

            self._enable_next_phase_button("flop")

            await self._update_game_message(interaction, new_embed, image_bytes, image_filename, view_to_use=self)

        @discord.ui.button(label="Deal Turn", style=discord.ButtonStyle.primary, custom_id="holdem_turn", disabled=True, row=0)
        async def deal_turn_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.game.player.id:
                await interaction.response.send_message("This is not your Texas Hold 'em game!", ephemeral=True)
                return
            await interaction.response.defer()
            
            for item in self.children:
                item.disabled = True
            
            current_embed = self.message.embeds[0] if self.message and self.message.embeds else None
            if current_embed:
                await interaction.edit_original_response(embed=current_embed, view=self)
            else:
                await interaction.edit_original_response(content="Processing...", view=self)

            self.game.deal_turn()
            image_bytes, image_filename = await self.game._fetch_game_image()
            new_embed = self.game._create_game_embed()
            self._enable_next_phase_button("turn")
            await self._update_game_message(interaction, new_embed, image_bytes, image_filename, view_to_use=self)

        @discord.ui.button(label="Deal River", style=discord.ButtonStyle.primary, custom_id="holdem_river", disabled=True, row=0)
        async def deal_river_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.game.player.id:
                await interaction.response.send_message("This is not your Texas Hold 'em game!", ephemeral=True)
                return
            await interaction.response.defer()
            
            for item in self.children:
                item.disabled = True
            
            current_embed = self.message.embeds[0] if self.message and self.message.embeds else None
            if current_embed:
                await interaction.edit_original_response(embed=current_embed, view=self)
            else:
                await interaction.edit_original_response(content="Processing...", view=self)

            self.game.deal_river()
            image_bytes, image_filename = await self.game._fetch_game_image()
            new_embed = self.game._create_game_embed()
            self._enable_next_phase_button("river")
            await self._update_game_message(interaction, new_embed, image_bytes, image_filename, view_to_use=self)

        @discord.ui.button(label="Showdown", style=discord.ButtonStyle.red, custom_id="holdem_showdown", disabled=True, row=1)
        async def showdown_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.game.player.id:
                await interaction.response.send_message("This is not your Texas Hold 'em game!", ephemeral=True)
                return
            await interaction.response.defer()
            
            for item in self.children:
                item.disabled = True
            
            current_embed = self.message.embeds[0] if self.message and self.message.embeds else None
            if current_embed:
                await interaction.edit_original_response(embed=current_embed, view=self)
            else:
                await interaction.edit_original_response(content="Processing...", view=self)

            image_bytes, image_filename = await self.game._fetch_game_image(reveal_opponent=True)
            final_embed = self.game._create_game_embed(reveal_opponent=True)
            self._end_game_buttons()
            await self._update_game_message(interaction, final_embed, image_bytes, image_filename, view_to_use=self)
            del active_texasholdem_games[self.game.channel_id]
            self.stop()

        @discord.ui.button(label="Play Again", style=discord.ButtonStyle.blurple, custom_id="holdem_play_again", disabled=True, row=1)
        async def play_again_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.game.player.id:
                await interaction.response.send_message("This is not your Texas Hold 'em game!", ephemeral=True)
                return
            await interaction.response.defer()

            await self.game.reset_game()
            self.game.deal_hole_cards()

            for item in self.children:
                if item.custom_id == "holdem_flop":
                    item.disabled = False
                elif item.custom_id in ["holdem_turn", "holdem_river", "holdem_showdown", "holdem_play_again"]:
                    item.disabled = True
            
            image_bytes, image_filename = await self.game._fetch_game_image()
            initial_embed = self.game._create_game_embed()
            try:
                await self._update_game_message(interaction, initial_embed, image_bytes, image_filename, view_to_use=self)
                active_texasholdem_games[self.game.channel_id] = self
            except discord.errors.NotFound:
                print("WARNING: Original game message not found during 'Play Again' edit for Hold 'em.")
                await interaction.followup.send("Could not restart game. Please try `/serene game texas_hold_em` again.", ephemeral=True)
                if self.game.channel_id in active_texasholdem_games:
                    del active_texasholdem_games[self.game.channel_id]
            except Exception as e:
                print(f"WARNING: An error occurred during 'Play Again' edit for Hold 'em: {e}")
                await interaction.followup.send("An error occurred while restarting the game.", ephemeral=True)
                if self.game.channel_id in active_texasholdem_games:
                    del active_texasholdem_games[self.game.channel_id]


    class TexasHoldEmGame:
        # ... (Your existing TexasHoldEmGame class code) ...
        def __init__(self, channel_id: int, player: discord.User):
            self.channel_id = channel_id
            self.player = player
            self.bot_player = bot.user
            self.deck = []
            self.player_hole_cards = []
            self.bot_hole_cards = []
            self.community_cards = []
            self.game_message = None
            self.game_data_url = "https://serenekeks.com/serene_bot_games.php"
            self.game_phase = "pre_flop"

        def _build_deck_from_codes(self, card_codes: list[str]) -> list[dict]:
            suits = {'S': 'Spades', 'D': 'Diamonds', 'C': 'Clubs', 'H': 'Hearts'}
            ranks = {
                'A': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9,
                'T': 10, 'J': 10, 'Q': 10, 'K': 10
            }
            rank_titles = {
                'A': 'Ace', '2': 'Two', '3': 'Three', '4': 'Four', '5': 'Five',
                '6': 'Six', '7': 'Seven', '8': 'Eight', '9': 'Nine', 'T': 'Ten',
                'J': 'Jack', 'Q': 'Queen', 'K': 'King'
            }
            suit_titles = {
                'S': 'Spades', 'D': 'Diamonds', 'C': 'Clubs', 'H': 'Hearts'
            }

            deck = []
            for code in card_codes:
                if len(code) == 2:
                    rank_code = code[0]
                    suit_code = code[1]
                elif len(code) == 3 and code[0] == '1' and code[1] == '0':
                    rank_code = 'T'
                    suit_code = code[2]
                else:
                    print(f"WARNING: Unrecognized card code format: {code}. Skipping.")
                    continue

                if rank_code not in ranks or suit_code not in suits:
                    print(f"WARNING: Invalid rank or suit code in {code}. Skipping.")
                    continue

                title = f"{rank_titles.get(rank_code, rank_code)} of {suits.get(suit_code, suit_code)}"
                card_number = ranks.get(rank_code, 0)

                deck.append({
                    "title": title,
                    "cardNumber": card_number,
                    "code": code
                })
            return deck

        async def _fetch_and_initialize_deck(self):
            deck_api_url = 'https://deckofcardsapi.com/api/deck/new/draw/?count=52'
            print(f"DEBUG: Fetching deck for Texas Hold 'em from {deck_api_url}")
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(deck_api_url) as response:
                        if response.status == 200:
                            data = await response.json()
                            
                            if 'cards' in data and isinstance(data['cards'], list):
                                card_codes = [str(card['code']) for card in data['cards'] if 'code' in card]
                                if card_codes:
                                    self.deck = self._build_deck_from_codes(card_codes)
                                    random.shuffle(self.deck)
                                    print(f"DEBUG: Deck initialized with {len(self.deck)} cards from deckofcardsapi.com for Texas Hold 'em.")
                                    return True
                                else:
                                    print(f"ERROR: No card codes found in deckofcardsapi.com response for Texas Hold 'em: {data}")
                                    self.deck = self._create_standard_deck_fallback()
                                    return False
                            else:
                                print(f"ERROR: Invalid response structure from deckofcardsapi.com for Texas Hold 'em: Missing 'cards' key or not a list. Response: {data}")
                                self.deck = self._create_standard_deck_fallback()
                                return False
                        else:
                            print(f"ERROR: Failed to fetch deck from deckofcardsapi.com for Texas Hold 'em: HTTP Status {response.status}. URL: {deck_api_url}")
                            self.deck = self._create_standard_deck_fallback()
                            return False
            except aiohttp.ClientError as e:
                print(f"ERROR: Network error fetching deck from deckofcardsapi.com for Texas Hold 'em: {e}. URL: {deck_api_url}")
                self.deck = self._create_standard_deck_fallback()
                return False
            except json.JSONDecodeError as e:
                print(f"ERROR: JSON decode error fetching deck from deckofcardsapi.com for Texas Hold 'em: {e}. Response was: {await response.text()}")
                self.deck = self._create_standard_deck_fallback()
                return False
            except Exception as e:
                print(f"ERROR: An unexpected error occurred fetching deck for Texas Hold 'em: {e}. URL: {deck_api_url}")
                self.deck = self._create_standard_deck_fallback()
                return False

        def _create_standard_deck_fallback(self) -> list[dict]:
            print("DEBUG: Using standard deck fallback for Texas Hold 'em.")
            suits = ['S', 'D', 'C', 'H']
            ranks = {
                'A': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9,
                'T': 10, 'J': 10, 'Q': 10, 'K': 10
            }
            rank_titles = {
                'A': 'Ace', '2': 'Two', '3': 'Three', '4': 'Four', '5': 'Five',
                '6': 'Six', '7': 'Seven', '8': 'Eight', '9': 'Nine', 'T': 'Ten',
                'J': 'Jack', 'Q': 'Queen', 'K': 'King'
            }
            suit_titles = {
                'S': 'Spades', 'D': 'Diamonds', 'C': 'Clubs', 'H': 'Hearts'
            }

            deck = []
            for suit_code in suits:
                for rank_code, num_value in ranks.items():
                    title = f"{rank_titles[rank_code]} of {suit_titles[suit_code]}"
                    card_code = f"{rank_code}{suit_code}"
                    deck.append({
                        "title": title,
                        "cardNumber": num_value,
                        "code": card_code
                    })
            return deck

        def deal_card(self) -> dict:
            if not self.deck:
                print("Warning: Deck is empty, cannot deal more cards.")
                return {"title": "No Card", "cardNumber": 0, "code": "NO_CARD"}
            
            card = random.choice(self.deck)
            self.deck.remove(card)
            print(f"DEBUG: Dealt card: {card['code']}")
            return card

        def deal_hole_cards(self):
            self.player_hole_cards = [self.deal_card(), self.deal_card()]
            self.bot_hole_cards = [self.deal_card(), self.deal_card()]
            self.game_phase = "pre_flop"
            print(f"DEBUG: Player hole cards: {[c['code'] for c in self.player_hole_cards]}")
            print(f"DEBUG: Bot hole cards (hidden): {[c['code'] for c in self.bot_hole_cards]}")


        def deal_flop(self):
            self.community_cards.extend([self.deal_card(), self.deal_card(), self.deal_card()])
            self.game_phase = "flop"
            print(f"DEBUG: Flop dealt: {[c['code'] for c in self.community_cards]}")

        def deal_turn(self):
            self.community_cards.append(self.deal_card())
            self.game_phase = "turn"
            print(f"DEBUG: Turn dealt: {[c['code'] for c in self.community_cards]}")

        def deal_river(self):
            self.community_cards.append(self.deal_card())
            self.game_phase = "river"
            print(f"DEBUG: River dealt: {[c['code'] for c in self.community_cards]}")

        async def reset_game(self):
            print("DEBUG: Resetting game.")
            await self._fetch_and_initialize_deck()
            self.player_hole_cards = []
            self.bot_hole_cards = []
            self.community_cards = []
            self.game_phase = "pre_flop"

        async def _fetch_game_image(self, reveal_opponent: bool = False) -> tuple[bytes, str]:
            print(f"DEBUG: _fetch_game_image called with reveal_opponent={reveal_opponent}")
            community_card_codes = [card['code'] for card in self.community_cards if 'code' in card]
            player_card_codes = [card['code'] for card in self.player_hole_cards if 'code' in card]

            if reveal_opponent:
                dealer_card_codes = [card['code'] for card in self.bot_hole_cards if 'code' in card]
            else:
                dealer_card_codes = ["XX", "XX"]

            game_state_data = {
                "community": community_card_codes,
                "player": player_card_codes,
                "dealer": dealer_card_codes
            }
            game_state_json = json.dumps(game_state_data)
            encoded_game_state = urllib.parse.quote_plus(game_state_json)
            cache_buster = int(time.time() * 1000)
            full_game_image_url = f"{self.game_data_url}?game_data={encoded_game_state}&_cb={cache_buster}"

            print(f"DEBUG: Image fetch URL: {full_game_image_url}")

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(full_game_image_url, timeout=15) as response:
                        if response.status == 200 and response.content_type.startswith('image/'):
                            image_bytes = await response.read()
                            ext = response.content_type.split('/')[-1] if '/' in response.content_type else 'png'
                            filename = f"holdem_board.{ext}"
                            filename = filename.replace('\x00', '')
                            print(f"DEBUG: Texas Hold 'em image fetched successfully ({len(image_bytes)} bytes), filename: '{filename}'.")
                            return image_bytes, filename
                        else:
                            status_info = f"Status: {response.status}"
                            if not response.content_type.startswith('image/'):
                                status_info += f", Content-Type: {response.content_type}"
                            print(f"ERROR: Failed to fetch Texas Hold 'em image. URL: {full_game_image_url}, {status_info}")
                            return None, None
            except aiohttp.ClientError as e:
                print(f"ERROR: Network error fetching Texas Hold 'em image: {e}. URL: {full_game_image_url}")
                return None, None
            except asyncio.TimeoutError:
                print(f"ERROR: Timeout fetching Texas Hold 'em image. URL: {full_game_image_url}")
                return None, None
            except Exception as e:
                print(f"ERROR: Unexpected error fetching Texas Hold 'em image: {e}. URL: {full_game_image_url}")
                return None, None

        def _create_game_embed(self, reveal_opponent: bool = False) -> discord.Embed:
            embed = discord.Embed(
                title="Texas Hold 'em Poker",
                description=f"**{self.player.display_name} vs. Serene**",
                color=discord.Color.dark_blue()
            )
            
            embed.set_footer(text=f"Game Phase: {self.game_phase.replace('_', ' ').title()}")
            
            return embed

        async def start_game(self, interaction: discord.Interaction):
            print("DEBUG: start_game called for Texas Hold 'em.")
            await self._fetch_and_initialize_deck()
            self.deal_hole_cards()

            game_view = Games.TexasHoldEmGameView(game=self) # Refer to TexasHoldEmGameView via Games.TexasHoldEmGameView
            initial_embed = self._create_game_embed()

            image_bytes, image_filename = await self._fetch_game_image()

            if image_bytes and image_filename:
                file = discord.File(image_bytes, filename=image_filename)
                self.game_message = await interaction.followup.send(embed=initial_embed, files=[file], view=game_view)
                print("DEBUG: Initial Texas Hold 'em game message sent with image.")
            else:
                self.game_message = await interaction.followup.send(
                    embed=initial_embed.set_footer(text=f"Game Phase: {self.game_phase.replace('_', ' ').title()} | Error: Could not load card images."),
                    view=game_view
                )
                print("WARNING: Could not send initial Texas Hold 'em image due to fetch failure. Message sent without image.")

            game_view.message = self.game_message
            active_texasholdem_games[self.channel_id] = game_view
            game_view._enable_next_phase_button("pre_flop")


    @self.serene_group.command(name="game", description="Start a fun game with Serene!")
    @app_commands.choices(game_type=[
        app_commands.Choice(name="Tic-Tac-Toe", value="tic_tac_toe"),
        app_commands.Choice(name="Jeopardy", value="jeopardy"),
        app_commands.Choice(name="Blackjack", value="blackjack"),
        app_commands.Choice(name="Texas Hold 'em", value="texas_hold_em"),
    ])
    @app_commands.describe(game_type="The type of game to play.")
    async def game_command(self, interaction: discord.Interaction, game_type: str):
        await interaction.response.defer(ephemeral=True)

        if game_type == "tic_tac_toe":
            if interaction.channel.id in active_tictactoe_games:
                await interaction.followup.send(
                    "A Tic-Tac-Toe game is already active in this channel! Please finish it or wait.",
                    ephemeral=True
                )
                return

            player1 = interaction.user
            player2 = self.bot.user # Use self.bot.user for the bot's user object

            await interaction.followup.send(
                f"Starting Tic-Tac-Toe for {player1.display_name} vs. {player2.display_name}...",
                ephemeral=True
            )

            # Pass channel_id to TicTacToeView for cleanup in on_timeout
            game_view = Games.TicTacToeView(player_x=player1, player_o=player2)
            game_view.channel_id = interaction.channel.id # Store channel_id in view

            game_message = await interaction.channel.send(
                content=f"It's **{player1.display_name}**'s turn (X)",
                embed=game_view._start_game_message(),
                view=game_view
            )
            game_view.message = game_message
            active_tictactoe_games[interaction.channel.id] = game_view

        elif game_type == "jeopardy":
            if interaction.channel.id in active_jeopardy_games:
                await interaction.followup.send(
                    "A Jeopardy game is already active in this channel! Please finish it or wait.",
                    ephemeral=True
                )
                return
            
            await interaction.followup.send("Setting up Jeopardy game...", ephemeral=True)
            
            jeopardy_game = Games.NewJeopardyGame(interaction.channel.id, interaction.user) # Refer to NewJeopardyGame via Games.NewJeopardyGame
            
            success = await jeopardy_game.fetch_and_parse_jeopardy_data()

            if success:
                active_jeopardy_games[interaction.channel.id] = jeopardy_game
                
                jeopardy_view = Games.JeopardyGameView(jeopardy_game) # Refer to JeopardyGameView via Games.JeopardyGameView
                jeopardy_view.add_board_components()
                
                game_message = await interaction.channel.send(
                    content=f"**{jeopardy_game.player.display_name}**'s Score: **{'-' if jeopardy_game.score < 0 else ''}${abs(jeopardy_game.score)}**\n\n"
                            "Select a category and value from the dropdowns below!",
                    view=jeopardy_view
                )
                jeopardy_game.board_message = game_message

            else:
                await interaction.followup.send(
                    "Failed to load Jeopardy game data. Please try again later.",
                    ephemeral=True
                )
                return
        elif game_type == "blackjack":
            if interaction.channel.id in active_blackjack_games:
                await interaction.followup.send(
                    "A Blackjack game is already active in this channel! Please finish it or wait.",
                    ephemeral=True
                )
                return
            
            await interaction.followup.send("Setting up Blackjack game...", ephemeral=True)
            
            blackjack_game = Games.BlackjackGame(interaction.channel.id, interaction.user) # Refer to BlackjackGame via Games.BlackjackGame
            
            await blackjack_game.start_game(interaction)

        elif game_type == "texas_hold_em":
            if interaction.channel.id in active_texasholdem_games:
                await interaction.followup.send(
                    "A Texas Hold 'em game is already active in this channel! Please finish it or wait.",
                    ephemeral=True
                )
                return
            
            await interaction.followup.send("Setting up Texas Hold 'em game...", ephemeral=True)
            
            holdem_game = Games.TexasHoldEmGame(interaction.channel.id, interaction.user) # Refer to TexasHoldEmGame via Games.TexasHoldEmGame
            
            await holdem_game.start_game(interaction)

        else:
            await interaction.followup.send(
                f"Game type '{game_type}' is not yet implemented. Stay tuned!",
                ephemeral=True
            )

async def setup(bot):
    # This function is called when the cog is loaded
    await bot.add_cog(Games(bot))
