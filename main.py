import os
import random
import urllib.parse
import json
import asyncio
import re # Import the re module for regular expressions
import io # Import io for in-memory file operations
from itertools import combinations # Import combinations for poker hand evaluation
from collections import Counter # Import Counter for poker hand hand_evaluation

import discord
from discord.ext import commands, tasks # Import tasks for hourly execution
from discord import app_commands, ui
import aiohttp
import aiomysql # Import aiomysql for asynchronous MySQL connection
import aiomysql.cursors # Import for cursor type if needed, though default is fine for simple queries

# Import necessary libraries for image processing
from PIL import Image, ImageDraw, ImageFont # Pillow library for image manipulation

# Define intents
intents = discord.Intents.default()
intents.members = True # Ensure this intent is enabled to receive member events
intents.presences = True
intents.message_content = True

# Initialize the bot
bot = commands.Bot(command_prefix='!', intents=intents)

# --- Game State Storage ---
active_tictactoe_games = {}
active_jeopardy_games = {} # Re-introducing this for the new Jeopardy game
active_blackjack_games = {} # New storage for Blackjack games
active_texasholdem_games = {} # New storage for Texas Hold 'em games

# --- Helper for fuzzy matching (MODIFIED to use Levenshtein distance) ---
def levenshtein_distance(s1: str, s2: str) -> int:
    """
    Calculates the Levenshtein distance between two strings.
    This is the minimum number of single-character edits (insertions, deletions, or substitutions)
    required to change one word into the other.
    """
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    # Initialize the first row of the distance matrix
    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            # Calculate costs for insertion, deletion, and substitution
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2) # Cost is 0 if characters match, 1 otherwise
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]

def calculate_word_similarity(word1: str, word2: str) -> float:
    """
    Calculates a percentage of similarity between two words using Levenshtein distance.
    A higher percentage means more similarity.
    """
    word1_lower = word1.lower()
    word2_lower = word2.lower()

    max_len = max(len(word1_lower), len(word2_lower))
    if max_len == 0:
        return 100.0 # Both empty strings are 100% similar

    dist = levenshtein_distance(word1_lower, word2_lower)
    # Similarity is calculated as (max_length - distance) / max_length
    similarity_percentage = ((max_len - dist) / max_len) * 100.0
    return similarity_percentage


# --- New Jeopardy Game UI Components ---

class CategoryValueSelect(discord.ui.Select):
    """A dropdown (select) for choosing a question's value within a specific category."""
    def __init__(self, category_name: str, options: list[discord.SelectOption], placeholder: str, row: int):
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"jeopardy_select_{category_name.replace(' ', '_').lower()}_{row}", # Add row to custom_id for uniqueness
            row=row
        )
        self.category_name = category_name # Store category name for later use

    async def callback(self, interaction: discord.Interaction):
        """Handles a selection from the dropdown."""
        view: JeopardyGameView = self.view
        game: NewJeopardyGame = view.game

        # Ensure it's the active player's turn to select
        if interaction.user.id != game.player.id:
            await interaction.response.send_message("You are not the active player for this Jeopardy game.", ephemeral=True)
            return
        
        if game.current_question: # If a question is already being answered, prevent new selections
            await interaction.response.send_message("A question is currently active. Please wait for it to conclude.", ephemeral=True)
            return

        # Store the selected category and value in the view's state
        selected_value_str = self.values[0] # The selected value is always a string from SelectOption
        selected_value = int(selected_value_str) # Convert back to int

        # Find the actual question data
        question_data = None
        
        # Determine which data set to search based on current game phase
        categories_to_search = []
        if game.game_phase == "NORMAL_JEOPARDY":
            categories_to_search = game.normal_jeopardy_data.get("normal_jeopardy", [])
        elif game.game_phase == "DOUBLE_JEOPARDY":
            categories_to_search = game.double_jeopardy_data.get("double_jeopardy", [])

        for cat_data in categories_to_search:
            if cat_data["category"] == self.category_name:
                for q_data in cat_data["questions"]:
                    if q_data["value"] == selected_value and not q_data["guessed"]:
                        question_data = q_data
                        break
                if question_data:
                    break
        
        if question_data:
            # Respond immediately to the interaction to acknowledge the selection
            # This is crucial to avoid "Unknown interaction" errors.
            await interaction.response.send_message(
                f"**{game.player.display_name}** selected **{question_data['category']}** for **${question_data['value']}**.\n\n"
                "*Processing your selection...*",
                ephemeral=True # Make this initial response ephemeral
            )

            # Mark the question as guessed
            question_data["guessed"] = True
            game.current_question = question_data # Set current question in game state

            # Clear the view's internal selection state (not strictly necessary but good practice)
            view._selected_category = None
            view._selected_value = None

            # Delete the original board message that contained the dropdowns
            if game.board_message:
                try:
                    await game.board_message.delete()
                    game.board_message = None # Clear reference after deletion
                except discord.errors.NotFound:
                    print("WARNING: Original board message not found (already deleted or inaccessible).")
                    game.board_message = None
                except discord.errors.Forbidden:
                    print("WARNING: Missing permissions to delete the original board message. Please ensure the bot has 'Manage Messages' permission.")
                    # Keep game.board_message as is if deletion fails due to permissions,
                    # as it might still be visible but uneditable.
                except Exception as delete_e:
                    print(f"WARNING: An unexpected error occurred during original board message deletion: {delete_e}")
                    game.board_message = None # Assume it's gone or broken
            
            # --- Determine the correct prefix using Gemini ---
            determined_prefix = "What is" # Default fallback
            api_key = os.getenv('GEMINI_API_KEY')
            if api_key:
                try:
                    # Prompt Gemini to determine the single most appropriate prefix
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
                                    # Basic validation to ensure it's one of the expected prefixes
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

            # --- Daily Double Wager Logic ---
            is_daily_double = question_data.get("daily_double", False) # Corrected key name
            
            # Initialize game.current_wager with the question's value by default
            game.current_wager = question_data['value'] 

            if is_daily_double:
                # Send the initial Daily Double message using followup.send
                await interaction.followup.send(
                    f"**DAILY DOUBLE!** {game.player.display_name}, you found the Daily Double!\n"
                    f"Your current score is **{'-' if game.score < 0 else ''}${abs(game.score)}**." # Format negative score
                )

                max_wager = max(2000, game.score) if game.score >= 0 else 2000
                print(f"DEBUG: Player score: {game.score}, Calculated max_wager: {max_wager}") # DEBUG
                
                wager_prompt_message = await interaction.channel.send(
                    f"{game.player.display_name}, please enter your wager. "
                    f"You can wager any amount up to **${max_wager}** (must be positive)."
                )

                def check_wager(m: discord.Message):
                    return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id and m.content.isdigit()

                try:
                    wager_msg = await bot.wait_for('message', check=check_wager, timeout=30.0)
                    wager_input = int(wager_msg.content)
                    print(f"DEBUG: User entered wager: {wager_input}") # DEBUG

                    if wager_input <= 0:
                        await interaction.channel.send("Your wager must be a positive amount. Defaulting to $500.", delete_after=5)
                        game.current_wager = 500
                        print("DEBUG: Wager defaulted to 500 (<=0)") # DEBUG
                    elif wager_input > max_wager:
                        await interaction.channel.send(f"Your wager exceeds the maximum allowed (${max_wager}). Defaulting to max wager.", delete_after=5)
                        game.current_wager = max_wager
                        print(f"DEBUG: Wager defaulted to max_wager ({max_wager})") # DEBUG
                    else:
                        game.current_wager = wager_input
                        print(f"DEBUG: Wager set to user input: {game.current_wager}") # DEBUG
                    
                    # Attempt to delete messages, but handle potential errors gracefully
                    try:
                        await wager_prompt_message.delete()
                        await wager_msg.delete()
                    except discord.errors.Forbidden:
                        print("WARNING: Missing permissions to delete wager messages. Please ensure the bot has 'Manage Messages' permission.")
                        # Do not reset wager if deletion fails due to permissions
                    except Exception as delete_e:
                        print(f"WARNING: An unexpected error occurred during message deletion: {delete_e}")
                        # Do not reset wager for other deletion errors either

                except asyncio.TimeoutError:
                    print("DEBUG: Wager input timed out.") # DEBUG
                    await interaction.channel.send("Time's up! You didn't enter a wager. Defaulting to $500.", delete_after=5)
                    game.current_wager = 500
                except Exception as e:
                    # This block now only catches errors *during bot.wait_for* or initial processing of wager_input
                    print(f"DEBUG: Error getting wager (before deletion attempt): {e}") # DEBUG
                    await interaction.channel.send("An error occurred while getting your wager. Defaulting to $500.", delete_after=5)
                    game.current_wager = 500
                
                print(f"DEBUG: Final game.current_wager before sending question: {game.current_wager}") # DEBUG
                # Now send the question for Daily Double, reflecting the wager
                await interaction.followup.send(
                    f"You wagered **${game.current_wager}**.\n*For the Daily Double:*\n**{question_data['question']}**"
                )
            else: # Not a Daily Double, proceed as before
                # The wager is already set to question_data['value']
                await interaction.followup.send(
                    f"*For ${question_data['value']}:*\n**{question_data['question']}**"
                )


            # Define a list of valid Jeopardy prefixes for user answers
            valid_user_prefixes = (
                "what is", "who is", "what are", "who are",
                "what was", "who was", "what were", "who were"
            )

            def check_answer(m: discord.Message):
                # Check if message is in the same channel, from the same user
                if not (m.channel.id == interaction.channel.id and m.author.id == interaction.user.id):
                    return False
                
                # Check if the message content starts with any of the valid Jeopardy prefixes
                msg_content_lower = m.content.lower()
                for prefix in valid_user_prefixes:
                    if msg_content_lower.startswith(prefix):
                        return True
                return False

            try:
                # Wait for the user's response for a limited time (e.g., 30 seconds)
                user_answer_msg = await bot.wait_for('message', check=check_answer, timeout=30.0)
                user_raw_answer = user_answer_msg.content.lower()

                # Determine which prefix was used and strip it
                matched_prefix_len = 0
                for prefix in valid_user_prefixes:
                    if user_raw_answer.startswith(prefix):
                        matched_prefix_len = len(prefix)
                        break # Take the first match (order in tuple matters if there are overlaps, but for these prefixes, it's fine)
                
                processed_user_answer = user_raw_answer[matched_prefix_len:].strip()
                
                correct_answer_raw_lower = question_data['answer'].lower()
                # Remove text in parentheses from the correct answer for direct comparison
                correct_answer_for_comparison = re.sub(r'\s*\(.*\)', '', correct_answer_raw_lower).strip()

                is_correct = False
                # Check for exact match first (after stripping prefix and parentheses from correct answer)
                if processed_user_answer == correct_answer_for_comparison:
                    is_correct = True
                else:
                    # Tokenize answers and question for word-by-word comparison
                    # Remove punctuation from words before tokenizing
                    user_words = set(re.findall(r'\b\w+\b', processed_user_answer))
                    correct_words_full = set(re.findall(r'\b\w+\b', correct_answer_for_comparison))
                    question_words = set(re.findall(r'\b\w+\b', question_data['question'].lower()))

                    # Filter correct words: keep only those NOT in the question
                    # This creates a list of 'significant' words from the correct answer
                    significant_correct_words = [word for word in correct_words_full if word not in question_words]

                    # If the user's answer is a single word and it's an exact match for a significant correct word
                    if len(user_words) == 1 and list(user_words)[0] in significant_correct_words:
                        is_correct = True
                    else:
                        # Perform fuzzy matching for each user word against significant correct words
                        for user_word in user_words:
                            for sig_correct_word in significant_correct_words:
                                similarity = calculate_word_similarity(user_word, sig_correct_word)
                                if similarity >= 70.0:
                                    is_correct = True
                                    break
                            if is_correct:
                                break
                
                # Compare the processed user answer with the correct answer
                if is_correct:
                    game.score += game.current_wager # Use wager for score
                    await interaction.followup.send(
                        f"✅ Correct, {game.player.display_name}! Your score is now **{'-' if game.score < 0 else ''}${abs(game.score)}**."
                    )
                else:
                    game.score -= game.current_wager # Use wager for score
                    # Removed spoiler tags, added quotes, and ensured full answer is bold/underlined
                    full_correct_answer = f'"{determined_prefix} {question_data["answer"]}"'.strip()
                    await interaction.followup.send(
                        f"❌ Incorrect, {game.player.display_name}! The correct answer was: "
                        f"**__{full_correct_answer}__**. Your score is now **{'-' if game.score < 0 else ''}${abs(game.score)}**."
                    )

            except asyncio.TimeoutError:
                # No score change for timeout
                full_correct_answer = f'"{determined_prefix} {question_data["answer"]}"'.strip()
                await interaction.followup.send(
                    f"⏰ Time's up, {game.player.display_name}! You didn't answer in time for '${question_data['value']}' question. The correct answer was: "
                    f"**__{full_correct_answer}__**."
                )
            except Exception as e:
                print(f"Error waiting for answer: {e}")
                await interaction.followup.send("An unexpected error occurred while waiting for your answer.")
            finally:
                game.current_question = None # Clear current question state
                game.current_wager = 0 # Reset wager

                # Check if all questions in the current phase are guessed
                current_phase_completed = False
                if game.game_phase == "NORMAL_JEOPARDY" and game.is_all_questions_guessed("normal_jeopardy"):
                    current_phase_completed = True
                    game.game_phase = "DOUBLE_JEOPARDY"
                    await interaction.channel.send(f"**Double Jeopardy!** All normal jeopardy questions have been answered. Get ready for new challenges, {game.player.display_name}!")
                elif game.game_phase == "DOUBLE_JEOPARDY" and game.is_all_questions_guessed("double_jeopardy"):
                    current_phase_completed = True
                    
                    # --- Final Jeopardy Logic ---
                    if game.score <= 0:
                        await interaction.channel.send(
                            f"Thank you for playing Jeopardy, {game.player.display_name}! "
                            f"Your balance is **${game.score}**, and so here's where your game ends. "
                            "We hope to see you in Final Jeopardy very soon!"
                        )
                        if game.channel_id in active_jeopardy_games:
                            del active_jeopardy_games[game.channel_id]
                        view.stop() # Stop the current view's timeout
                        return # End the game here

                    # If player has positive earnings, proceed to Final Jeopardy
                    game.game_phase = "FINAL_JEOPARDY"
                    await interaction.channel.send(f"**Final Jeopardy!** All double jeopardy questions have been answered. Get ready for the final round, {game.player.display_name}!")

                    # Final Jeopardy Wager
                    final_max_wager = max(2000, game.score)
                    wager_prompt_message = await interaction.channel.send(
                        f"{game.player.display_name}, your current score is **{'-' if game.score < 0 else ''}${abs(game.score)}**. "
                        f"Please enter your Final Jeopardy wager. You can wager any amount up to **${final_max_wager}** (must be positive)."
                    )

                    def check_final_wager(m: discord.Message):
                        return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id and m.content.isdigit()

                    try:
                        final_wager_msg = await bot.wait_for('message', check=check_final_wager, timeout=60.0) # Longer timeout for wager
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
                        game.current_wager = 0 # Wager 0 if timeout
                    except Exception as e:
                        print(f"Error getting Final Jeopardy wager: {e}")
                        await interaction.channel.send("An error occurred while getting your wager. Defaulting to $0.", delete_after=5)
                        game.current_wager = 0

                    # Present Final Jeopardy Question
                    final_question_data = game.final_jeopardy_data.get("final_jeopardy")
                    if final_question_data:
                        await interaction.channel.send(
                            f"Your wager: **${game.current_wager}**.\n\n"
                            f"**Final Jeopardy Category:** {final_question_data['category']}\n\n"
                            f"**The Clue:** {final_question_data['question']}"
                        )

                        def check_final_answer(m: discord.Message):
                            # No prefix required for Final Jeopardy answers
                            return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id

                        try:
                            final_user_answer_msg = await bot.wait_for('message', check=check_final_answer, timeout=60.0) # Longer timeout for answer
                            final_user_raw_answer = final_user_answer_msg.content.lower().strip()

                            final_correct_answer_raw_lower = final_question_data['answer'].lower()
                            final_correct_answer_for_comparison = re.sub(r'\s*\(.*\)', '', final_correct_answer_raw_lower).strip()

                            final_is_correct = False
                            if final_user_raw_answer == final_correct_answer_for_comparison:
                                final_is_correct = True
                            else:
                                final_user_words = set(re.findall(r'\b\w+\b', final_user_raw_answer))
                                final_correct_words_full = set(re.findall(r'\b\w+\b', final_correct_answer_for_comparison))
                                
                                # For Final Jeopardy, all words in the correct answer are "significant"
                                final_significant_correct_words = list(final_correct_words_full) # Convert to list for iteration

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
                    
                    # End of Final Jeopardy
                    await interaction.channel.send(
                        f"Final Score for {game.player.display_name}: **{'-' if game.score < 0 else ''}${abs(game.score)}**.\n"
                        "Thank you for playing Jeopardy!"
                    )
                    # Add kekchipz based on final score if greater than 0
                    if game.score > 0:
                        await update_user_kekchipz(interaction.guild.id, interaction.user.id, game.score)

                    if game.channel_id in active_jeopardy_games:
                        del active_jeopardy_games[game.channel_id]
                    view.stop() # Stop the current view's timeout
                    return # Exit if Final Jeopardy is reached, as no more dropdowns are needed

                # Stop the current view before sending a new one
                view.stop()

                # Send a NEW message with the dropdowns for the next phase, or the current phase if not completed
                new_jeopardy_view = JeopardyGameView(game)
                new_jeopardy_view.add_board_components() # Rebuilds the view with updated options (guessed questions removed)

                # Determine the content for the new board message based on the game phase
                board_message_content = ""
                if game.game_phase == "NORMAL_JEOPARDY":
                    board_message_content = (
                        f"**{jeopardy_game.player.display_name}**'s Score: **{'-' if jeopardy_game.score < 0 else ''}${abs(jeopardy_game.score)}**\n\n"
                        "Select a category and value from the dropdowns below!"
                    )
                elif game.game_phase == "DOUBLE_JEOPARDY":
                    board_message_content = (
                        f"**{jeopardy_game.player.display_name}**'s Score: **{'-' if jeopardy_game.score < 0 else ''}${abs(jeopardy_game.score)}**\n\n"
                        "**Double Jeopardy!** Select a category and value from the dropdowns below!"
                    )
                
                if board_message_content: # Only send if there's content (i.e., not Final Jeopardy yet)
                    game.board_message = await interaction.channel.send(
                        content=board_message_content,
                        view=new_jeopardy_view
                    )
                else:
                    # If we reached Final Jeopardy and no board message is sent, clean up view
                    if new_jeopardy_view.children: # If there are still components, disable them
                        for item in new_jeopardy_view.children:
                            item.disabled = True
                        await interaction.channel.send("Game concluded. No more questions.", view=new_jeopardy_view)
                    else:
                        await interaction.channel.send("Game concluded. No more questions.")

        else:
            # If for some reason the question is not found or already guessed (race condition)
            await interaction.response.send_message(
                f"Question '{self.category_name}' for ${selected_value} not found or already picked. Please select another.",
                ephemeral=True
            )


class JeopardyGameView(discord.ui.View):
    """The Discord UI View that holds the interactive Jeopardy board dropdowns."""
    def __init__(self, game: 'NewJeopardyGame'):
        # Increased timeout to 15 minutes (900 seconds)
        super().__init__(timeout=900)
        self.game = game # Reference to the NewJeopardyGame instance
        self.message = None # To store the message containing the board UI
        self.play_again_timeout_task = None # To store the task for the "Play Again" timeout

    def add_board_components(self):
        """
        Dynamically adds dropdowns (selects) for categories to the view.
        Each dropdown is placed on its own row, up to a maximum of 5 rows (0-4).
        """
        self.clear_items()  # Clear existing items before rebuilding the board

        # Determine which data set to use based on current game phase
        categories_to_process = []
        if self.game.game_phase == "NORMAL_JEOPARDY":
            categories_to_process = self.game.normal_jeopardy_data.get("normal_jeopardy", [])
        elif self.game.game_phase == "DOUBLE_JEOPARDY":
            categories_to_process = self.game.double_jeopardy_data.get("double_jeopardy", [])
        else:
            # No dropdowns for Final Jeopardy or other phases
            return

        # Iterate through categories and assign each to a new row, limiting to 5 rows for Discord UI
        for i, category_data in enumerate(categories_to_process):
            if i >= 5: # Discord UI has a maximum of 5 rows (0-4) for components
                break

            category_name = category_data["category"]
            options = [
                discord.SelectOption(label=f"${q['value']}", value=str(q['value']))
                for q in category_data["questions"] if not q["guessed"] # Only show unguessed questions
            ]

            if options: # Only add a dropdown if there are available questions in the category
                # Place each category's dropdown on its own row (i.e., row=0, row=1, row=2, etc.)
                self.add_item(CategoryValueSelect(
                    category_name,
                    options,
                    f"Pick for {category_name}",
                    row=i
                ))

    async def on_timeout(self):
        """Called when the view times out due to inactivity."""
        if self.message:
            try:
                # Added try-except for NotFound error
                await self.game.board_message.edit(content="Jeopardy game timed out due to inactivity.", view=None)
            except discord.errors.NotFound:
                print("WARNING: Board message not found during timeout, likely already deleted.")
            except Exception as e:
                print(f"WARNING: An error occurred editing board message on timeout: {e}")
        
        # Changed self.game.channel.id to self.game.channel_id
        if self.game.channel_id in active_jeopardy_games:
            # Clean up the game state
            del active_jeopardy_games[self.game.channel_id]
        print(f"Jeopardy game in channel {self.game.channel_id} timed out.")


# --- Placeholder for new Jeopardy Game Class ---
class NewJeopardyGame:
    """
    A placeholder class for the new Jeopardy game.
    Currently, its primary function is to fetch and parse the Jeopardy data
    from the external API and store it in separate attributes.
    """
    def __init__(self, channel_id: int, player: discord.User):
        self.channel_id = channel_id
        self.player = player
        self.score = 0 # Initialize player score
        self.normal_jeopardy_data = None
        self.double_jeopardy_data = None
        self.final_jeopardy_data = None
        self.jeopardy_data_url = "https://serenekeks.com/serene_bot_games.php"
        self.board_message = None # To store the message containing the board UI
        self.current_question = None # Stores the question currently being presented
        self.current_wager = 0 # Stores the wager for Daily Double/Final Jeopardy
        self.game_phase = "NORMAL_JEOPARDY" # Tracks the current phase of the game

    async def fetch_and_parse_jeopardy_data(self) -> bool:
        """
        Fetches the full Jeopardy JSON data from the backend URL.
        Parses the JSON and separates it into three distinct data structures:
        normal_jeopardy, double_jeopardy, and final_jeopardy, storing them
        as attributes of this class.
        Initializes 'guessed' status for all questions.
        Returns True if data is successfully fetched and parsed, False otherwise.
        """
        try:
            # Construct the URL with the 'jeopardy' parameter
            params = {"jeopardy": "true"}
            encoded_params = urllib.parse.urlencode(params)
            full_url = f"{self.jeopardy_data_url}?{encoded_params}"

            async with aiohttp.ClientSession() as session:
                async with session.get(full_url) as response:
                    if response.status == 200:
                        full_data = await response.json()
                        
                        # Initialize 'guessed' status for all questions and add category name
                        for category_type in ["normal_jeopardy", "double_jeopardy"]:
                            if category_type in full_data:
                                for category in full_data[category_type]:
                                    for question_data in category["questions"]:
                                        question_data["guessed"] = False
                                        question_data["category"] = category["category"] # Store category name in question
                        if "final_jeopardy" in full_data:
                            full_data["final_jeopardy"]["guessed"] = False
                            full_data["final_jeopardy"]["category"] = full_data["final_jeopardy"].get("category", "Final Jeopardy")

                        self.normal_jeopardy_data = {"normal_jeopardy": full_data.get("normal_jeopardy", [])}
                        self.double_jeopardy_data = {"double_data": full_data.get("double_jeopardy", [])} # Fixed typo here
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
        """
        Checks if all questions in a given phase (normal_jeopardy or double_jeopardy)
        have been guessed.
        """
        data_to_check = []
        if phase_type == "normal_jeopardy":
            data_to_check = self.normal_jeopardy_data.get("normal_jeopardy", [])
        elif phase_type == "double_jeopardy":
            data_to_check = self.double_jeopardy_data.get("double_jeopardy", [])
        else:
            return False # Invalid phase type

        if not data_to_check: # If there's no data for this phase, consider it "completed"
            return True

        for category in data_to_check:
            for question_data in category["questions"]:
                if not question_data["guessed"]:
                    return False # Found an unguessed question
        return True # All questions are guessed


# --- Tic-Tac-Toe Game Classes ---

class TicTacToeButton(discord.ui.Button):
    """Represents a single square on the Tic-Tac-Toe board."""
    def __init__(self, row: int, col: int, player_mark: str = "⬜"):
        super().__init__(style=discord.ButtonStyle.secondary, label=player_mark, row=row)
        self.row = row
        self.col = col
        self.player_mark = player_mark # This will be ' ', 'X', or 'O'

    async def callback(self, interaction: discord.Interaction):
        """Handle button click for a Tic-Tac-Toe square."""
        view: TicTacToeView = self.view
        
        # Ensure it's the correct player's turn
        if interaction.user.id != view.players[view.current_player].id:
            await interaction.response.send_message("It's not your turn!", ephemeral=True)
            return

        # Ensure the spot is empty (check against the actual board state, not just button label)
        if view.board[self.row][self.col] != " ": # Check the internal board state
            await interaction.response.send_message("That spot is already taken!", ephemeral=True)
            return

        # Update the button and board
        self.player_mark = view.current_player # Update button's internal state
        self.label = self.player_mark # Update button's visible label
        
        # Set button style based on player mark
        if self.player_mark == "X":
            self.style = discord.ButtonStyle.primary
        else: # self.player_mark == "O"
            self.style = discord.ButtonStyle.danger
            
        self.disabled = True
        view.board[self.row][self.col] = self.player_mark # Update internal board state

        # Defer the interaction response to allow time for bot's move if needed
        await interaction.response.defer()

        # Check for win or draw after human's move
        if view._check_winner():
            winner_player = view.players[view.current_player]
            loser_player = view.players["O" if view.current_player == "X" else "X"] # The other player is the loser

            # Only update human player's kekchipz
            if winner_player.id == interaction.user.id: # Human wins
                await update_user_kekchipz(interaction.guild.id, interaction.user.id, 100)
            elif loser_player.id == interaction.user.id: # Human loses (bot wins)
                await update_user_kekchipz(interaction.guild.id, interaction.user.id, 10)

            await interaction.edit_original_response(
                content=f"🎉 **{winner_player.display_name} wins!** 🎉",
                embed=view._start_game_message(),
                view=view._end_game()
            )
            del active_tictactoe_games[interaction.channel.id] # End the game
        elif view._check_draw():
            await update_user_kekchipz(interaction.guild.id, interaction.user.id, 25) # Human player gets kekchipz for a draw
            await interaction.edit_original_response(
                content="It's a **draw!** 🤝",
                embed=view._start_game_message(),
                view=view._end_game()
            )
            del active_tictactoe_games[interaction.channel.id] # End the game
        else:
            # Switch player
            view.current_player = "O" if view.current_player == "X" else "X"
            next_player_obj = view.players[view.current_player]

            # Update message for next turn
            await interaction.edit_original_response(
                content=f"It's **{next_player_obj.display_name}**'s turn ({view.current_player})",
                embed=view._start_game_message(),
                view=view
            )

            # If it's the bot's turn, make its move
            if view.players[view.current_player].id == bot.user.id:
                await asyncio.sleep(1) # Small delay for natural feel
                await view._bot_make_move(interaction)


class TicTacToeView(discord.ui.View):
    """Manages the Tic-Tac-Toe game board and logic."""
    def __init__(self, player_x: discord.User, player_o: discord.User):
        super().__init__(timeout=300) # Game times out after 5 minutes of inactivity
        self.players = {"X": player_x, "O": player_o}
        self.current_player = "X"
        self.board = [[" ", " ", ""], [" ", " ", " "], [" ", " ", " "]] # Internal board uses " " for empty
        self.message = None # To store the message containing the board

        self._create_board()

    def _create_board(self):
        """Initializes the 3x3 grid of buttons."""
        for row in range(3):
            for col in range(3):
                # Pass " " as the initial label for the button
                self.add_item(TicTacToeButton(row, col, player_mark="⬜"))

    def _update_board_display(self):
        """Updates the labels and styles of the buttons to reflect the current board state.
           This method is called by the button's callback, not directly by the view.
           The button itself updates its label and style.
        """
        # This method is no longer strictly needed as buttons update themselves on click
        # However, we can use it to refresh all buttons from the internal board state
        for item in self.children:
            if isinstance(item, TicTacToeButton):
                mark = self.board[item.row][item.col]
                item.label = mark
                if mark == "X":
                    item.style = discord.ButtonStyle.primary
                elif mark == "O":
                    item.style = discord.ButtonStyle.danger
                else:
                    item.style = discord.ButtonStyle.secondary
                item.disabled = mark != " " # Disable if already marked


    def _start_game_message(self) -> discord.Embed:
        """Generates the embed for the game board."""
        embed = discord.Embed(
            title="Tic-Tac-Toe",
            description=f"**{self.players['X'].display_name}** (X) vs. **{self.players['O'].display_name}** (O)\n"
                        f"Current Turn: **{self.players[self.current_player].display_name}** ({self.current_player})",
            color=discord.Color.blue()
        )
        # Graphical board representation in the embed
        board_str = ""
        for r in range(3):
            for c in range(3):
                mark = self.board[r][c]
                if mark == "X":
                    board_str += "🇽 " # Regional indicator x
                elif mark == "O":
                    board_str += "🅾️ " # Regional indicator o
                else:
                    board_str += "⬜ " # White square (using emoji here is fine as it's a string literal not a variable)
            board_str += "\n"
        embed.add_field(name="Board", value=board_str, inline=False)
        return embed

    def _check_win_state(self, board, player) -> bool:
        """Checks if a given player has won on the provided board."""
        # Check rows, columns, and diagonals
        for i in range(3):
            if all(board[i][j] == player for j in range(3)): return True # Row
            if all(board[j][i] == player for j in range(3)): return True # Column
            if all(board[k][k] == player for k in range(3)): return True # Diagonal \
            if all(board[k][2-k] == player for k in range(3)): return True # Diagonal /
        return False

    def _check_winner(self) -> bool:
        """Checks if the current player has won."""
        return self._check_win_state(self.board, self.current_player)

    def _check_draw(self) -> bool:
        """Checks if the game is a draw."""
        for row in self.board:
            if " " in row:
                return False # Still empty spots
        return not self._check_winner() # Only a draw if no winner and board is full

    def _get_empty_cells(self, board):
        """Returns a list of (row, col) tuples for empty cells."""
        empty_cells = []
        for r in range(3):
            for c in range(3):
                if board[r][c] == " ":
                    empty_cells.append((r, c))
        return empty_cells

    def _minimax(self, board, is_maximizing_player):
        """
        Minimax algorithm to determine the best move.
        is_maximizing_player: True for bot ('O'), False for human ('X')
        """
        # Base cases: Check for win/loss/draw
        if self._check_win_state(board, "O"): # Bot wins
            return 1
        if self._check_win_state(board, "X"): # Human wins
            return -1
        if not self._get_empty_cells(board): # Draw
            return 0

        if is_maximizing_player: # Bot's turn ('O')
            best_eval = -float('inf')
            for r, c in self._get_empty_cells(board):
                board[r][c] = "O"
                evaluation = self._minimax(board, False) # Recurse for human's turn
                board[r][c] = " " # Undo move (backtrack)
                best_eval = max(best_eval, evaluation)
            return best_eval
        else: # Human's turn ('X')
            best_eval = float('inf')
            for r, c in self._get_empty_cells(board):
                board[r][c] = "X"
                evaluation = self._minimax(board, True) # Recurse for bot's turn
                board[r][c] = " " # Undo move (backtrack)
                best_eval = min(best_eval, evaluation)
            return best_eval

    async def _bot_make_move(self, interaction: discord.Interaction):
        """Calculates and makes the bot's optimal move."""
        best_score = -float('inf')
        best_move = None

        # Iterate through all possible moves to find the best one
        for r, c in self._get_empty_cells(self.board):
            self.board[r][c] = "O" # Make hypothetical move for bot
            score = self._minimax(self.board, False) # Evaluate human's response to this move
            self.board[r][c] = " ", # Undo hypothetical move

            if score > best_score:
                best_score = score
                best_move = (r, c)
        
        if best_move:
            row, col = best_move
            self.board[row][col] = "O" # Apply the best move to the actual board

            # Find the corresponding button and update its state
            for item in self.children:
                if isinstance(item, TicTacToeButton) and item.row == row and item.col == col:
                    item.label = "O"
                    item.style = discord.ButtonStyle.danger # Red for O
                    item.disabled = True
                    break
            
            # Check for win or draw after bot's move
            if self._check_winner():
                winner_player = self.players[self.current_player]
                loser_player = self.players["X" if self.current_player == "O" else "O"] # The other player is the loser

                # Only update human player's kekchipz
                if winner_player.id == interaction.user.id: # Human wins
                    await update_user_kekchipz(interaction.guild.id, interaction.user.id, 100)
                elif loser_player.id == interaction.user.id: # Human loses (bot wins)
                    await update_user_kekchipz(interaction.guild.id, interaction.user.id, 10)

                await interaction.edit_original_response(
                    content=f"🎉 **{winner_player.display_name} wins!** 🎉",
                    embed=self._start_game_message(),
                    view=self._end_game()
                )
                del active_tictactoe_games[interaction.channel.id]
            elif self._check_draw():
                await update_user_kekchipz(interaction.guild.id, interaction.user.id, 25) # Human player gets kekchipz for a draw
                await interaction.edit_original_response(
                    content="It's a **draw!** 🤝",
                    embed=self._start_game_message(),
                    view=self._end_game()
                )
                del active_tictactoe_games[interaction.channel.id]
            else:
                # Switch player back to human
                self.current_player = "X"
                next_player_obj = self.players[self.current_player]
                await interaction.edit_original_response(
                    content=f"It's **{next_player_obj.display_name}**'s turn ({self.current_player})",
                    embed=self._start_game_message(),
                    view=self
                )


    def _end_game(self):
        """Disables all buttons and removes the view from the active games."""
        for item in self.children:
            item.disabled = True
        return self # Return self to update the view with disabled buttons

    async def on_timeout(self):
        """Called when the view times out due to inactivity."""
        print(f"DEBUG: on_timeout called for channel {self.message.channel.id}")
        if self.message:
            try:
                await self.message.edit(content="Game timed out due to inactivity.", view=None, embed=None)
            except discord.errors.NotFound:
                print("WARNING: Board message not found during timeout, likely already deleted.")
            except Exception as e:
                print(f"WARNING: An error occurred editing board message on timeout: {e}")
        
        # Changed self.game.channel.id to self.game.channel_id
        if self.message.channel.id in active_tictactoe_games: # Use message.channel.id for consistency
            del active_tictactoe_games[self.message.channel.id]
        print(f"Tic-Tac-Toe game in channel {self.message.channel.id} timed out.")


# --- Database Operations ---

async def add_user_to_db_if_not_exists(guild_id: int, user_name: str, discord_id: int):
    """
    Checks if a user exists in the 'discord_users' table for a given guild.
    If not, inserts a new row for the user with default values.
    """
    db_user = os.getenv('DB_USER')
    db_password = os.getenv('DB_PASSWORD')
    db_host = os.getenv('DB_HOST')
    db_name = "serene_users" # The database name where discord_users table resides
    table_name = "discord_users" # The table name as specified by the user

    if not all([db_user, db_password, db_host]):
        print("Database operation failed: Missing one or more environment variables (DB_USER, DB_PASSWORD, DB_HOST).")
        return

    conn = None
    try:
        conn = await aiomysql.connect(
            host=db_host,
            user=db_user,
            password=db_password,
            db=db_name,
            charset='utf8mb4', # Crucial for handling all Unicode characters
            autocommit=True # Set autocommit to True for simple connection check and inserts
        )
        async with conn.cursor() as cursor:
            # Check if user already exists for this guild
            # Use %s placeholders for parameters to prevent SQL injection
            await cursor.execute(
                f"SELECT COUNT(*) FROM {table_name} WHERE channel_id = %s AND discord_id = %s",
                (str(guild_id), str(discord_id)) # Convert IDs to string as per VARCHAR column type
            )
            (count,) = await cursor.fetchone()

            if count == 0:
                # User does not exist, insert them
                initial_json_data = json.dumps({"warnings": {}}) # Initialize json_data as {"warnings":{}}
                await cursor.execute(
                    f"INSERT INTO {table_name} (channel_id, user_name, discord_id, kekchipz, json_data) VALUES (%s, %s, %s, %s, %s)",
                    (str(guild_id), user_name, str(discord_id), 0, initial_json_data)
                )
                print(f"Added new user '{user_name}' (ID: {discord_id}) to '{table_name}' in guild {guild_id}.")
            # else:
            #     print(f"User '{user_name}' (ID: {discord_id}) already exists in '{table_name}' for guild {guild_id}. Skipping insertion.")

    except aiomysql.Error as e:
        print(f"Database operation failed for user {user_name} (ID: {discord_id}): MySQL Error: {e}")
    except Exception as e:
        print(f"Database operation failed for user {discord_id}): An unexpected error occurred: {e}")
    finally:
        if conn:
            conn.close()

async def update_user_kekchipz(guild_id: int, discord_id: int, amount: int):
    """
    Updates the kekchipz balance for a user in the database.
    """
    db_user = os.getenv('DB_USER')
    db_password = os.getenv('DB_PASSWORD')
    db_host = os.getenv('DB_HOST')
    db_name = "serene_users"
    table_name = "discord_users"

    if not all([db_user, db_password, db_host]):
        print("Database operation failed: Missing one or more environment variables (DB_USER, DB_PASSWORD, DB_HOST).")
        return

    conn = None
    try:
        conn = await aiomysql.connect(
            host=db_host,
            user=db_user,
            password=db_password,
            db=db_name,
            charset='utf8mb4',
            autocommit=True
        )
        async with conn.cursor() as cursor:
            # Fetch current kekchipz
            await cursor.execute(
                f"SELECT kekchipz FROM {table_name} WHERE channel_id = %s AND discord_id = %s",
                (str(guild_id), str(discord_id))
            )
            result = await cursor.fetchone()
            
            current_kekchipz = result[0] if result else 0
            new_kekchipz = current_kekchipz + amount

            # Update kekchipz
            await cursor.execute(
                f"UPDATE {table_name} SET kekchipz = %s WHERE channel_id = %s AND discord_id = %s",
                (new_kekchipz, str(guild_id), str(discord_id))
            )
            print(f"Updated kekchipz for user {discord_id} in guild {guild_id}: {current_kekchipz} -> {new_kekchipz}")

    except aiomysql.Error as e:
        print(f"Database update failed for user {discord_id}: MySQL Error: {e}")
    except Exception as e:
        print(f"Database update failed for user {discord_id}): An unexpected error occurred: {e}")
    finally:
        if conn:
            conn.close()

async def get_user_kekchipz(guild_id: int, discord_id: int) -> int:
    """
    Fetches the kekchipz balance for a user from the database.
    Returns 0 if the user is not found or an error occurs.
    """
    db_user = os.getenv('DB_USER')
    db_password = os.getenv('DB_PASSWORD')
    db_host = os.getenv('DB_HOST')
    db_name = "serene_users"
    table_name = "discord_users"

    if not all([db_user, db_password, db_host]):
        print("Database operation failed: Missing one or more environment variables (DB_USER, DB_PASSWORD, DB_HOST).")
        return 0

    conn = None
    try:
        conn = await aiomysql.connect(
            host=db_host,
            user=db_user,
            password=db_password,
            db=db_name,
            charset='utf8mb4',
            autocommit=True
        )
        async with conn.cursor() as cursor:
            await cursor.execute(
                f"SELECT kekchipz FROM {table_name} WHERE channel_id = %s AND discord_id = %s",
                (str(guild_id), str(discord_id))
            )
            result = await cursor.fetchone()
            return result[0] if result else 0
    except aiomysql.Error as e:
        print(f"Database fetch failed for user {discord_id}: MySQL Error: {e}")
        return 0
    except Exception as e:
        print(f"Database fetch failed for user {discord_id}): An unexpected error occurred: {e}")
        return 0
    finally:
        if conn:
            conn.close()


# --- Bot Events ---
@bot.event
async def on_ready():
    """
    Event handler that runs when the bot is ready.
    It prints the bot's login information, syncs slash commands,
    starts the hourly database connection check, and
    adds all existing guild members to the database if they don't exist.
    """
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    print('------')
    try:
        # Sync slash commands with Discord. This makes the commands available in guilds.
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    
    # Start the hourly database connection check
    hourly_db_check.start()

    # --- Add existing members to database on startup ---
    # Wait until the bot has cached all guilds and members
    await bot.wait_until_ready() 
    print("Checking existing guild members for database entry...")
    for guild in bot.guilds:
        print(f"Processing guild: {guild.name} (ID: {guild.id})")
        for member in guild.members:
            if not member.bot: # Only add actual users, not other bots
                await add_user_to_db_if_not_exists(member.guild.id, member.display_name, member.id)
    print("Finished checking existing guild members.")


@bot.event
async def on_member_join(member: discord.Member):
    """
    Event handler that runs when a new member joins a guild.
    Adds the new member to the database if they don't already exist.
    """
    if member.bot: # Do not add bots to the database
        return
    print(f"New member joined: {member.display_name} (ID: {member.id}) in guild {member.guild.name} (ID: {member.guild.id}).")
    await add_user_to_db_if_not_exists(member.guild.id, member.display_name, member.id)


@bot.event
async def on_message(message: discord.Message):
    """Listens for messages to handle Jeopardy answers."""
    # Ignore messages from the bot itself
    if message.author.id == bot.user.id:
        return

    # Process other commands normally
    await bot.process_commands(message)


# --- Hourly Database Connection Check ---
@tasks.loop(hours=1)
async def hourly_db_check():
    """
    Attempts to connect to the MySQL database every hour using environment variables.
    Logs success or failure to the console.
    This is primarily for monitoring database connectivity.
    """
    print("Attempting hourly database connection check...")
    db_user = os.getenv('DB_USER')
    db_password = os.getenv('DB_PASSWORD')
    db_host = os.getenv('DB_HOST')
    db_name = "serene_users" # The user specified "serene_users" datatable

    if not all([db_user, db_password, db_host]):
        print("Database connection failed: Missing one or more environment variables (DB_USER, DB_PASSWORD, DB_HOST).")
        return

    conn = None
    try:
        conn = await aiomysql.connect(
            host=db_host,
            user=db_user,
            password=db_password,
            db=db_name,
            charset='utf8mb4', # Crucial for handling all Unicode characters
            autocommit=True # Set autocommit to True for simple connection check
        )
        print(f"Successfully connected to MySQL database '{db_name}' on host '{db_host}' as user '{db_user}'.")
    except aiomysql.Error as e:
        print(f"Database connection failed: MySQL Error: {e}")
    except Exception as e:
        print(f"Database connection failed: An unexpected error occurred: {e}")
    finally:
        if conn:
            conn.close()
            print("Database connection closed.")

@hourly_db_check.error
async def hourly_db_check_error(exception):
    """Error handler for the hourly_db_check task."""
    print(f"An error occurred in hourly_db_check task: {exception}")


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
        "think": "thought", "told": "told", "become": "became", "show": "showed",
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
    else: # Simplified to avoid complex CVC rule that caused "weatherred"
        return verb + 'ed'


# --- Consolidate commands under a single /serene command group ---
# This creates a group named 'serene'
serene_group = app_commands.Group(name="serene", description="Commands for Serene Bot.")
bot.tree.add_command(serene_group) # Add the group to the bot's command tree

@serene_group.command(name="talk", description="Interact with the Serene bot backend.")
@app_commands.describe(text_input="Your message or question for Serene.")
async def talk_command(interaction: discord.Interaction, text_input: str):
    """
    Handles the /serene talk slash command.
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


@serene_group.command(name="hail", description="Hail Serene!")
async def hail_command(interaction: discord.Interaction):
    """
    Handles the /serene hail slash command.
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


@serene_group.command(name="roast", description="Get roasted by Serene!")
async def roast_command(interaction: discord.Interaction):
    """
    Handles the /serene roast slash command.
    Sends a predefined "roast me" message to the backend with a 'roast' parameter.
    """
    await interaction.response.defer() # Acknowledge the interaction

    php_backend_url = "https://serenekeks.com/serene_bot.php"
    player_name = interaction.user.display_name

    text_to_send = "roast me"  # Predefined text for this command
    param_name = "roast"  # Use 'roast' as the parameter name

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


@serene_group.command(name="story", description="Generate a story with contextually appropriate nouns and verbs.")
async def story_command(interaction: discord.Interaction):
    """
    Handles the /serene story slash command.
    Fetches sentence structure from PHP, generates nouns and verbs using Gemini API,
    then constructs and displays the story.
    """
    await interaction.response.defer() # Acknowledge the interaction

    php_backend_url = "https://serenekeks.com/serene_bot_2.php"
    player_name = interaction.user.display_name

    # Initialize nouns and verbs with fallbacks in case of API failure
    nouns = ["dragon", "wizard", "monster"]
    verbs_infinitive = ["fly", "vanish"]

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
                    
                    # Extract verb form requirements from PHP response (though currently static, good practice)
                    v1_form_required = php_story_structure.get("verb_forms", {}).get("v1_form", "infinitive")
                    v2_form_required = php_story_structure.get("verb_forms", {}).get("v2_form", "past_tense")

                else:
                    print(f"Warning: PHP backend call failed with status {response.status}. Using default verb forms and structure.")

    except aiohttp.ClientError as e:
        print(f"Error connecting to PHP backend: {e}. Using default story structure and verb forms.")
    except Exception as e:
        print(f"An unexpected error occurred while fetching PHP structure: {e}. Using default story structure and verb forms.")


    try:
        # Prompt for the Gemini API to get contextually appropriate words
        # The prompt is significantly refined to ensure variety and contextual cohesion
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
        - "waffle-spanked a vagrant so hard that they [verb_past_tense]"
        "kissed Crizz P."
        "spun around so fast that they [verb_past_tense]"
        "vomitted so loudly that they [verb_past_tense]"
        "sand-blastd out a power-shart so strong, that they [verb_past_tense]"
        "slipped off the roof above—and with a thump—they [verb_past_tense]"

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


# --- Image Generation Function ---
async def create_card_combo_image(combo_str: str, scale_factor: float = 1.0, overlap_percent: float = 0.2) -> Image.Image:
    """
    Creates a combined image of playing cards from a comma-separated string of card codes.
    Fetches PNG images from deckofcardsapi.com and combines them using Pillow.

    Args:
        combo_str (str): A comma-separated string of card codes (e.g., "AS,KD,TH").
                         "XX" can be used for a hidden card (back of card).
        scale_factor (float): Factor to scale the card images (e.g., 1.0 for original size).
        overlap_percent (float): The percentage of card width that cards should overlap.

    Returns:
        PIL.Image.Image: A Pillow Image object containing the combined cards.

    Raises:
        ValueError: If no valid card codes are provided and it's not a special "XX" case.
    """
    cards = [card.strip().upper() for card in combo_str.split(',') if card.strip()]

    # Define a default size for cards in case the first fetch fails
    default_card_width, default_card_height = 73, 98 # Standard playing card dimensions in pixels (approx)

    if not cards:
        # If no valid card codes are provided (e.g., empty string), return a transparent placeholder.
        # The "XX" case is now handled within the loop if it's explicitly in the combo_str.
        return Image.new('RGBA', (default_card_width, default_card_height), (0, 0, 0, 0))


    card_images = []
    first_card_width, first_card_height = None, None

    for i, card in enumerate(cards):
        if card == "XX":
            png_url = "https://deckofcardsapi.com/static/img/back.png"
        else:
            png_url = f"https://deckofcardsapi.com/static/img/{card}.png"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(png_url) as response:
                    response.raise_for_status()  # Raise an exception for HTTP errors (4xx or 5xx)

                    # Open the image directly using Pillow
                    pil_image = Image.open(io.BytesIO(await response.read()))

                    # Set background to transparent if it's not already
                    if pil_image.mode != 'RGBA':
                        pil_image = pil_image.convert('RGBA')

                    # Get initial dimensions from the first successfully loaded card
                    if first_card_width is None:
                        first_card_width, first_card_height = pil_image.size
                        # If this is the first card, set defaults if not already
                        if first_card_width is None: # This inner check is redundant if pil_image.size is always valid here.
                            first_card_width = default_card_width
                            first_card_height = default_card_height

                    # Scale the image based on the first card's dimensions
                    scaled_width = int(first_card_width * scale_factor)
                    scaled_height = int(first_card_height * scale_factor)

                    # Resize the image if scaling is applied
                    if scaled_width != pil_image.width or scaled_height != pil_image.height:
                        pil_image = pil_image.resize((scaled_width, scaled_height), Image.Resampling.LANCZOS)

                    card_images.append(pil_image)

        except aiohttp.ClientError as e:
            print(f"Failed to fetch PNG for card '{card}' from {png_url}: {e}")
            # If the first card fails, ensure default dimensions are set
            if first_card_width is None:
                first_card_width = default_card_width
                first_card_height = default_card_height
            # Append a placeholder for failed cards to avoid breaking the layout
            card_images.append(Image.new('RGBA', (int(first_card_width * scale_factor), int(first_card_height * scale_factor)), (255, 0, 0, 128))) # Red transparent placeholder
        except Exception as e:
            print(f"Error processing PNG for card '{card}' from {png_url}: {e}")
            # If the first card fails, ensure default dimensions are set
            if first_card_width is None:
                first_card_width = default_card_width
                first_card_height = default_card_height
            card_images.append(Image.new('RGBA', (int(first_card_width * scale_factor), int(first_card_height * scale_factor)), (0, 255, 0, 128))) # Green transparent placeholder

    # If no images were successfully loaded at all, return a generic transparent placeholder
    if not card_images:
        return Image.new('RGBA', (default_card_width, default_card_height), (0, 0, 0, 0))

    # Calculate dimensions for the combined image
    num_cards = len(card_images)
    
    # Calculate overlap based on scaled card width
    # Use the width of the first successfully loaded card, or default if none
    base_card_width = card_images[0].width if card_images else default_card_width
    overlap_px = int(base_card_width * overlap_percent)
    
    # Ensure overlap is not too large
    if overlap_px >= base_card_width:
        overlap_px = int(base_card_width * 0.1) # Default to 10% if overlap is too aggressive

    combined_width = base_card_width + (num_cards - 1) * overlap_px
    combined_height = card_images[0].height if card_images else default_card_height

    # Create a new blank transparent image
    combined_image = Image.new('RGBA', (combined_width, combined_height), (0, 0, 0, 0)) # RGBA for transparency

    # Paste each card onto the combined image
    x_offset = 0
    for img in card_images:
        combined_image.paste(img, (x_offset, 0), img) # Use img as mask for transparency
        x_offset += overlap_px

    return combined_image


# --- New Blackjack Game UI Components ---

class BlackjackGameView(discord.ui.View):
    """
    The Discord UI View that holds the interactive Blackjack game buttons.
    """
    def __init__(self, game: 'BlackjackGame'):
        super().__init__(timeout=300) # Game times out after 5 minutes of inactivity
        self.game = game # Reference to the BlackjackGame instance
        self.message = None # To store the message containing the game UI
        self.play_again_timeout_task = None # To store the task for the "Play Again" timeout

    async def _update_game_message(self, embed: discord.Embed, player_file: discord.File, dealer_file: discord.File, view_to_use: discord.ui.View = None):
        """Helper to update the main game message by editing the original response, including image files."""
        try:
            if self.message: # self.message holds the actual discord.Message object
                await self.message.edit(embed=embed, view=view_to_use, attachments=[player_file, dealer_file])
            else:
                print("WARNING: self.message is not set. Cannot update game message.")
        except discord.errors.NotFound:
            print("WARNING: Game message not found during edit, likely already deleted.")
        except Exception as e:
            print(f"WARNING: An error occurred editing game message: {e}")

    def _set_button_states(self, game_state: str):
        """
        Sets the disabled state of all buttons based on the current game state.
        game_state: "playing", "game_over"
        """
        for item in self.children:
            if item.custom_id == "blackjack_hit":
                item.disabled = (game_state != "playing")
            elif item.custom_id == "blackjack_stay":
                item.disabled = (game_state != "playing")
            elif item.custom_id == "blackjack_play_again":
                item.disabled = (game_state != "game_over") # Enabled only when game is over
        
        # Manage the "Play Again" timeout task
        if game_state == "game_over":
            if self.play_again_timeout_task and not self.play_again_timeout_task.done():
                self.play_again_timeout_task.cancel() # Cancel any existing task
            self.play_again_timeout_task = bot.loop.create_task(self._handle_play_again_timeout())
        elif self.play_again_timeout_task and not self.play_again_timeout_task.done():
            self.play_again_timeout_task.cancel() # Cancel if game is no longer over

    async def _handle_play_again_timeout(self):
        """Handles the timeout for the 'Play Again' button."""
        try:
            await asyncio.sleep(10) # Wait for 10 seconds
            
            # If we reach here, the "Play Again" button was not pressed in time
            if self.game.channel_id in active_blackjack_games:
                # Disable all buttons
                for item in self.children:
                    item.disabled = True
                
                # Update the message to indicate timeout
                if self.message:
                    try:
                        await self.message.edit(content="Blackjack game ended due to inactivity (Play Again not pressed).", view=self, embed=self.message.embed)
                    except discord.errors.NotFound:
                        print("WARNING: Game message not found during play again timeout, likely already deleted.")
                    except Exception as e:
                        print(f"WARNING: An error occurred editing game message on play again timeout: {e}")
                
                # Clean up the game state
                del active_blackjack_games[self.game.channel_id]
                self.stop() # Stop the view's main timeout as well
                print(f"Blackjack game in channel {self.game.channel_id} ended due to Play Again timeout.")
        except asyncio.CancelledError:
            # Task was cancelled because "Play Again" was clicked
            print(f"Play Again timeout task for channel {self.game.channel_id} cancelled.")
        except Exception as e:
            print(f"An unexpected error occurred in _handle_play_again_timeout: {e}")


    async def on_timeout(self):
        """Called when the view times out due to inactivity."""
        if self.message:
            try:
                # Disable all buttons and add a play again button if it's not already there
                self._set_button_states("game_over") # Set buttons for game over (Play Again enabled)
                await self.message.edit(content="Blackjack game timed out due to inactivity. Click 'Play Again' to start a new game.", view=self, embed=self.message.embed)

            except discord.errors.NotFound:
                print("WARNING: Game message not found during timeout, likely already deleted.")
            except Exception as e:
                print(f"WARNING: An error occurred editing board message on timeout: {e}")
        
        if self.game.channel_id in active_blackjack_games:
            # We don't delete the game from active_blackjack_games here,
            # as we want the "Play Again" button to be functional.
            # The game will be removed when "Play Again" is clicked or a new game starts.
            pass
        print(f"Blackjack game in channel {self.game.channel_id} timed out.")


    @discord.ui.button(label="Hit", style=discord.ButtonStyle.green, custom_id="blackjack_hit")
    async def hit_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handles the 'Hit' button click."""
        if interaction.user.id != self.game.player.id:
            await interaction.response.send_message("This is not your Blackjack game!", ephemeral=True)
            return
        
        # Disable all action buttons immediately for feedback, re-enable if game continues
        for item in self.children:
            if item.custom_id in ["blackjack_hit", "blackjack_stay"]:
                item.disabled = True
        await interaction.response.edit_message(view=self) # Immediate visual update

        self.game.player_hand.append(self.game.deal_card())
        player_value = self.game.calculate_hand_value(self.game.player_hand)

        if player_value > 21:
            self._set_button_states("game_over") # Set buttons for game over
            embed, player_file, dealer_file = await self.game._create_game_embed_with_images()
            embed.set_footer(text="BUST! Serene wins.")
            await self._update_game_message(embed, player_file, dealer_file, self) # Use helper
            await update_user_kekchipz(interaction.guild.id, interaction.user.id, -50)
            # Game is over, cancel any pending play_again_timeout_task
            if self.play_again_timeout_task and not self.play_again_timeout_task.done():
                self.play_again_timeout_task.cancel()
            del active_blackjack_games[self.game.channel_id]
        else:
            self._set_button_states("playing") # Set buttons for continuing game
            embed, player_file, dealer_file = await self.game._create_game_embed_with_images()
            await self._update_game_message(embed, player_file, dealer_file, self) # Use helper

    @discord.ui.button(label="Stay", style=discord.ButtonStyle.red, custom_id="blackjack_stay")
    async def stay_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handles the 'Stay' button click."""
        if interaction.user.id != self.game.player.id:
            await interaction.response.send_message("This is not your Blackjack game!", ephemeral=True)
            return
        
        # Disable all action buttons immediately for feedback
        for item in self.children:
            if item.custom_id in ["blackjack_hit", "blackjack_stay"]:
                item.disabled = True
        await interaction.response.edit_message(view=self) # Immediate visual update

        # Serene's turn
        player_value = self.game.calculate_hand_value(self.game.player_hand)
        serene_value = self.game.calculate_hand_value(self.game.dealer_hand)

        # Serene hits until 17 or more
        while serene_value < 17:
            self.game.dealer_hand.append(self.game.deal_card())
            serene_value = self.game.calculate_hand_value(self.game.dealer_hand)
            embed, player_file, dealer_file = await self.game._create_game_embed_with_images(reveal_dealer=True)
            await self._update_game_message(embed, player_file, dealer_file, self) # Use helper
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

        self._set_button_states("game_over") # Set buttons for game over
        embed, player_file, dealer_file = await self.game._create_game_embed_with_images(reveal_dealer=True)
        embed.set_footer(text=result_message)
        await self._update_game_message(embed, player_file, dealer_file, self) # Use helper
        await update_user_kekchipz(interaction.guild.id, interaction.user.id, kekchipz_change)
        # Game is over, cancel any pending play_again_timeout_task
        if self.play_again_timeout_task and not self.play_again_timeout_task.done():
            self.play_again_timeout_task.cancel()
        del active_blackjack_games[self.game.channel_id]

    @discord.ui.button(label="Play Again", style=discord.ButtonStyle.blurple, custom_id="blackjack_play_again", disabled=True)
    async def play_again_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handles the 'Play Again' button click by resetting the game and updating the current message."""
        if interaction.user.id != self.game.player.id:
            await interaction.response.send_message("This is not your Blackjack game!", ephemeral=True)
            return

        # Cancel the play_again_timeout_task if it's running
        if self.play_again_timeout_task and not self.play_again_timeout_task.done():
            self.play_again_timeout_task.cancel()
            self.play_again_timeout_task = None # Clear the reference

        # Disable Play Again button immediately
        button.disabled = True
        await interaction.response.edit_message(view=self) # Immediate visual update

        self.game.reset_game()
        self.game.player_hand = [self.game.deal_card(), self.game.deal_card()]
        self.game.dealer_hand = [self.game.deal_card(), self.game.deal_card()]

        self._set_button_states("playing") # Reset buttons for new game
        
        embed, player_file, dealer_file = await self.game._create_game_embed_with_images()

        try:
            await self._update_game_message(embed, player_file, dealer_file, self) # Use helper
            active_blackjack_games[self.game.channel_id] = self
        except discord.errors.NotFound:
            print("WARNING: Original game messages not found during 'Play Again' edit.")
            await interaction.followup.send("Could not restart game. Please try `/serene game blackjack` again.", ephemeral=True)
            if self.game.channel_id in active_blackjack_games:
                del active_blackjack_games[self.game.channel_id]
        except Exception as e:
            print(f"WARNING: An error occurred during 'Play Again' edit: {e}")
            await interaction.followup.send("An error occurred while restarting the game.", ephemeral=True)
            if self.game.channel_id in active_blackjack_games:
                del active_blackjack_games[self.channel_id]
        

class BlackjackGame:
    """
    Represents a single Blackjack game instance.
    Manages game state, player and Serene hands, and card deck.
    """
    def __init__(self, channel_id: int, player: discord.User):
        self.channel_id = channel_id
        self.player = player
        self.deck = self._create_standard_deck() # Initialize deck locally
        self.player_hand = []
        self.dealer_hand = [] # This will be Serene's hand
        self.game_message = None # To store the message containing the game UI
        # self.game_data_url = "https://serenekeks.com/serene_bot_games.php" # No longer needed
        self.game_over = False # New flag to track if the game has ended

    def _create_standard_deck(self) -> list[dict]:
        """
        Generates a standard 52-card deck with titles, numbers, and codes.
        """
        suits = ['S', 'D', 'C', 'H'] # Spades, Diamonds, Clubs, Hearts
        ranks = {
            'A': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9,
            '0': 10, 'J': 10, 'Q': 10, 'K': 10
        }
        rank_titles = {
            'A': 'Ace', '2': 'Two', '3': 'Three', '4': 'Four', '5': 'Five',
            '6': 'Six', '7': 'Seven', '8': 'Eight', '9': 'Nine', '0': 'Ten',
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
        """
        Deals a random card from the deck. Removes the card from the deck.
        Returns the dealt card (dict with 'title', 'cardNumber', and 'code').
        """
        if not self.deck:
            # Handle case where deck is empty (e.g., reshuffle or end game)
            print("Warning: Deck is empty, cannot deal more cards.")
            # Return a dummy card with empty image and code for graceful failure
            return {"title": "No Card", "cardNumber": 0, "code": "NO_CARD"} 
        
        card = random.choice(self.deck)
        self.deck.remove(card) # Remove the dealt card from the deck
        return card

    def calculate_hand_value(self, hand: list[dict]) -> int:
        """
        Calculates the value of a Blackjack hand.
        Handles Aces (1 or 11) dynamically.
        """
        value = 0
        num_aces = 0
        for card in hand:
            card_number = card.get("cardNumber", 0)
            if card_number == 1: # Ace
                num_aces += 1
                value += 11 # Assume 11 initially
            elif card_number >= 10: # Face cards (10, Jack, Queen, King)
                value += 10
            else: # Number cards
                value += card_number
        
        # Adjust for Aces if hand value exceeds 21
        while value > 21 and num_aces > 0:
            value -= 10 # Change an Ace from 11 to 1
            num_aces -= 1
        return value

    async def _create_game_embed_with_images(self, reveal_dealer: bool = False) -> tuple[discord.Embed, discord.File]:
        """
        Creates and returns a Discord Embed object and Discord.File objects
        representing the current game state with combined card images.
        :param reveal_dealer: If True, reveals Serene's hidden card.
        :return: A tuple of (discord.Embed, player_image_file, dealer_image_file).
        """
        player_value = self.calculate_hand_value(self.player_hand)
        serene_value = self.calculate_hand_value(self.dealer_hand)

        # Fetch player's kekchipz
        player_kekchipz = await get_user_kekchipz(self.player.guild.id, self.player.id)

        # Generate player's hand image
        player_card_codes = [card['code'] for card in self.player_hand if 'code' in card]
        player_image_pil = await create_card_combo_image(','.join(player_card_codes), scale_factor=0.4, overlap_percent=0.4) # Changed scale_factor
        player_image_bytes = io.BytesIO()
        player_image_pil.save(player_image_bytes, format='PNG')
        player_image_bytes.seek(0) # Rewind to the beginning of the BytesIO object
        player_file = discord.File(player_image_bytes, filename="player_hand.png")

        # Generate Serene's hand image
        serene_display_cards_codes = []
        if reveal_dealer:
            serene_display_cards_codes = [card['code'] for card in self.dealer_hand if 'code' in card]
        else:
            # Only show the first card and a back card
            if self.dealer_hand and 'code' in self.dealer_hand[0]:
                serene_display_cards_codes.append(self.dealer_hand[0]['code'])
            serene_display_cards_codes.append("XX") # Placeholder for back of card

        serene_image_pil = await create_card_combo_image(','.join(serene_display_cards_codes), scale_factor=0.4, overlap_percent=0.4) # Changed scale_factor
        serene_image_bytes = io.BytesIO()
        serene_image_pil.save(serene_image_bytes, format='PNG')
        serene_image_bytes.seek(0)
        dealer_file = discord.File(serene_image_bytes, filename="serene_hand.png")

        # Create an embed for the game display
        embed = discord.Embed(
            title="Blackjack Game",
            description=f"**{self.player.display_name} vs. Serene**\n\n"
                        f"**{self.player.display_name}'s Kekchipz:** ${player_kekchipz}", # Display kekchipz here
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

        # Reference the attachments in the embed
        embed.set_image(url="attachment://player_hand.png")
        embed.set_thumbnail(url="attachment://serene_hand.png")
        
        embed.set_footer(text="What would you like to do? (Hit or Stand)")
        
        return embed, player_file, dealer_file

    def reset_game(self):
        """Resets the game state for a new round."""
        self.deck = self._create_standard_deck()
        random.shuffle(self.deck)
        self.player_hand = []
        self.dealer_hand = []
        self.game_over = False

    async def start_game(self, interaction: discord.Interaction):
        """
        Starts the Blackjack game: shuffles, deals initial hands,
        and displays the initial state using an embed with combined card images.
        """
        # Deck is already created in __init__, just shuffle it
        random.shuffle(self.deck) 

        # Deal initial hands
        self.player_hand = [self.deal_card(), self.deal_card()]
        self.dealer_hand = [self.deal_card(), self.deal_card()] # This is Serene's hand
        
        # Create the view for the game
        game_view = BlackjackGameView(game=self)
        
        # Create the initial embed and get image files
        initial_embed, player_file, dealer_file = await self._create_game_embed_with_images()

        # Send the message as a follow-up to the deferred slash command interaction
        self.game_message = await interaction.followup.send(embed=initial_embed, view=game_view, files=[player_file, dealer_file])
        game_view.message = self.game_message # Store message in the view for updates
        
        active_blackjack_games[self.channel_id] = game_view # Store the view instance, not the game itself


# --- Texas Hold 'em Game Classes ---

# Poker Hand Evaluation Functions (from user's provided code)
RANKS = {'2': 2, '3': 3, '4': 4, '5': 5, '6': 6,
         '7': 7, '8': 8, '9': 9, '0': 10, 'J': 11,
         'Q': 12, 'K': 13, 'A': 14}

HAND_NAMES = {
    1: "high card",
    2: "one pair",
    3: "two pair",
    4: "three of a kind",
    5: "straight",
    6: "flush",
    7: "full house",
    8: "four of a kind",
    9: "straight flush"
}

def get_card_value(card):
    """Extracts the numerical value of a card from its code (e.g., 'AS' -> 14)."""
    return RANKS[card[0]]

def get_card_suit(card):
    """Extracts the suit of a card from its code (e.g., 'AS' -> 'S')."""
    return card[1]

def hand_name(rank):
    """Returns the descriptive name of a poker hand given its rank."""
    return HAND_NAMES.get(rank, "unknown")

def score_hand(cards):
    """
    Scores a 5-card poker hand.
    Returns a list representing the hand's rank and kickers for comparison.
    """
    values = sorted([get_card_value(c) for c in cards])
    suits = [get_card_suit(c) for c in cards]
    counts = Counter(values)
    counts_by_value = sorted(counts.items(), key=lambda x: (-x[1], -x[0]))
    sorted_by_count = []
    for val, count in counts_by_value:
        sorted_by_count.extend([val] * count)

    is_flush = len(set(suits)) == 1
    unique_values = sorted(set(values))

    # Check for straight
    is_straight = False
    high_card = None
    if len(unique_values) >= 5: # Ensure there are enough unique cards for a straight
        for i in range(len(unique_values) - 4):
            window = unique_values[i:i+5]
            if window[-1] - window[0] == 4 and len(set(window)) == 5: # Check for consecutive and unique
                is_straight = True
                high_card = window[-1]

    # Special case: A-2-3-4-5 (Ace treated as 1)
    # Check if the hand contains A, 2, 3, 4, 5 and is a straight
    if set([14, 2, 3, 4, 5]).issubset(values) and len(unique_values) == 5: # Ace (14) is present, and 2,3,4,5
        # Ensure it's not a higher straight that just happens to contain these
        if not is_straight or high_card != 14: # If it's not already a higher straight
            is_straight = True
            high_card = 5 # For A-2-3-4-5, the high card for comparison is 5

    # Straight flush
    if is_straight and is_flush:
        return [9, high_card]

    # Four of a kind
    if 4 in counts.values():
        quad_rank = counts_by_value[0][0] # Rank of the four of a kind
        kicker = [v for v in sorted_by_count if v != quad_rank][0] # The remaining card
        return [8, quad_rank, kicker]

    # Full house
    if 3 in counts.values() and 2 in counts.values():
        trip_rank = counts_by_value[0][0] # Rank of the three of a kind
        pair_rank = counts_by_value[1][0] # Rank of the pair
        return [7, trip_rank, pair_rank]

    # Flush
    if is_flush:
        return [6] + sorted(values, reverse=True)[:5] # Top 5 cards for tie-breaking

    # Straight
    if is_straight:
        return [5, high_card]

    # Three of a kind
    if 3 in counts.values():
        trip_rank = counts_by_value[0][0]
        kickers = [v for v in sorted_by_count if v != trip_rank][:2] # Top 2 kickers
        return [4, trip_rank] + sorted(kickers, reverse=True)

    # Two pair
    if list(counts.values()).count(2) == 2:
        pair1_rank = counts_by_value[0][0]
        pair2_rank = counts_by_value[1][0]
        kicker = [v for v in sorted_by_count if v not in [pair1_rank, pair2_rank]][0]
        return [3, max(pair1_rank, pair2_rank), min(pair1_rank, pair2_rank), kicker]

    # One pair
    if 2 in counts.values():
        pair_rank = counts_by_value[0][0]
        kickers = [v for v in sorted_by_count if v != pair_rank][:3] # Top 3 kickers
        return [2, pair_rank] + sorted(kickers, reverse=True)

    # High card
    return [1] + sorted(values, reverse=True)[:5] # Top 5 high cards

def evaluate_best_hand(seven_cards):
    """
    Evaluates the best 5-card poker hand from a given 7 cards.
    """
    best = None
    for combo in combinations(seven_cards, 5):
        score = score_hand(combo)
        if not best or compare_scores(score, best) > 0:
            best = score
    return best

def compare_scores(score1, score2):
    """
    Compares two poker hand scores.
    Returns 1 if score1 is better, -1 if score2 is better, 0 if tie.
    """
    for a, b in zip(score1, score2):
        if a > b: return 1
        if a < b: return -1
    return 0


class TexasHoldEmGameView(discord.ui.View):
    """
    The Discord UI View that holds the interactive Texas Hold 'em game buttons.
    """
    def __init__(self, game: 'TexasHoldEmGame'):
        super().__init__(timeout=300) # Game times out after 5 minutes of inactivity
        self.game = game # Reference to the TexasHoldEmGame instance
        # Buttons will be added dynamically by _set_button_states

    def _set_button_states(self, phase: str, betting_buttons_visible: bool = False, call_after_raise_enabled: bool = False):
        """
        Dynamically adds and sets the disabled state of buttons based on the current game phase.
        Buttons that should not be seen are not added.
        """
        self.clear_items() # Crucial: Clear all existing buttons
        print(f"DEBUG: _set_button_states called. Phase: {phase}, Betting Visible: {betting_buttons_visible}, Call After Raise: {call_after_raise_enabled}")

        # Always add Fold and Play Again
        self.add_item(discord.ui.Button(label="Fold", style=discord.ButtonStyle.red, custom_id="holdem_fold_main", row=0))
        self.add_item(discord.ui.Button(label="Play Again", style=discord.ButtonStyle.blurple, custom_id="holdem_play_again", row=2, disabled=True))

        if phase == "pre_flop":
            self.add_item(discord.ui.Button(label="Raise", style=discord.ButtonStyle.green, custom_id="holdem_raise_main", row=0))
            self.add_item(discord.ui.Button(label="Call", style=discord.ButtonStyle.blurple, custom_id="holdem_call_main", row=0))
            # Check button is NOT added pre-flop
            
            # Betting amount buttons (initially disabled, only enabled if Raise is clicked)
            self.add_item(discord.ui.Button(label="$5", style=discord.ButtonStyle.secondary, custom_id="holdem_bet_5", row=1, disabled=True))
            self.add_item(discord.ui.Button(label="$10", style=discord.ButtonStyle.secondary, custom_id="holdem_bet_10", row=1, disabled=True))
            self.add_item(discord.ui.Button(label="$25", style=discord.ButtonStyle.secondary, custom_id="holdem_bet_25", row=1, disabled=True))

        elif phase in ["flop", "turn", "river"]:
            self.add_item(discord.ui.Button(label="Raise", style=discord.ButtonStyle.green, custom_id="holdem_raise_main", row=0))
            self.add_item(discord.ui.Button(label="Call", style=discord.ButtonStyle.blurple, custom_id="holdem_call_main", row=0))
            self.add_item(discord.ui.Button(label="Check", style=discord.ButtonStyle.gray, custom_id="holdem_check_main", row=0))

            # Betting amount buttons (initially disabled, only enabled if Raise is clicked)
            self.add_item(discord.ui.Button(label="$5", style=discord.ButtonStyle.secondary, custom_id="holdem_bet_5", row=1, disabled=True))
            self.add_item(discord.ui.Button(label="$10", style=discord.ButtonStyle.secondary, custom_id="holdem_bet_10", row=1, disabled=True))
            self.add_item(discord.ui.Button(label="$25", style=discord.ButtonStyle.secondary, custom_id="holdem_bet_25", row=1, disabled=True))

            # Adjust disabled states based on sub-phase (betting_buttons_visible, call_after_raise_enabled)
            if betting_buttons_visible:
                for item in self.children:
                    if item.custom_id in ["holdem_raise_main", "holdem_call_main", "holdem_check_main", "holdem_fold_main"]:
                        item.disabled = True # Disable main actions if bet buttons are active
                    elif item.custom_id.startswith("holdem_bet_"):
                        item.disabled = False # Enable bet buttons
            elif call_after_raise_enabled:
                for item in self.children:
                    if item.custom_id == "holdem_call_main" or item.custom_id == "holdem_fold_main": # Player must call or fold
                        item.disabled = False
                    elif item.custom_id in ["holdem_raise_main", "holdem_check_main"] or item.custom_id.startswith("holdem_bet_"):
                        item.disabled = True # Disable raise/check/bet if player must call
            else: # Normal state for flop/turn/river if no raise is pending
                for item in self.children:
                    if item.custom_id in ["holdem_raise_main", "holdem_check_main", "holdem_fold_main"]:
                        item.disabled = False
                    elif item.custom_id == "holdem_call_main": # Call disabled if no raise to call
                        item.disabled = True
                    elif item.custom_id.startswith("holdem_bet_"):
                        item.disabled = True # Betting buttons disabled by default

        elif phase == "showdown" or phase == "folded":
            # Only Play Again button should be enabled, others remain disabled or not added
            for item in self.children:
                item.disabled = True
                if item.custom_id == "holdem_play_again":
                    item.disabled = False
        
        # Ensure Play Again is always disabled unless explicitly in end game state
        for item in self.children:
            if item.custom_id == "holdem_play_again" and phase not in ["showdown", "folded"]:
                item.disabled = True
        
        print(f"DEBUG: Buttons after _set_button_states:")
        for item in self.children:
            print(f"  Button: {item.custom_id}, Label: {item.label}, Disabled: {item.disabled}, Row: {item.row}")


    def _end_game_buttons(self):
        """Disables all game progression buttons and enables 'Play Again'."""
        self.clear_items()
        self.add_item(discord.ui.Button(label="Play Again", style=discord.ButtonStyle.blurple, custom_id="holdem_play_again", row=2, disabled=False))
        return self

    async def on_timeout(self):
        """Called when the view times out due to inactivity."""
        print(f"DEBUG: on_timeout called for channel {self.game.channel_id}")
        if self.game.game_message:
            try:
                self._end_game_buttons() # Enable Play Again, disable others
                await self.game.game_message.edit(content=f"{self.game.player.display_name}'s turn timed out. Click 'Play Again' to start a new game.", view=self, attachments=[])
            except discord.errors.NotFound:
                print("WARNING: Game message not found during timeout, likely already deleted.")
            except Exception as e:
                print(f"WARNING: An error occurred editing game message on timeout: {e}")
        
        if self.game.channel_id in active_texasholdem_games:
            pass # Keep for Play Again functionality
        print(f"Texas Hold 'em game in channel {self.game.channel_id} timed out.")

    @discord.ui.button(label="Raise", style=discord.ButtonStyle.green, custom_id="holdem_raise_main", row=0)
    async def raise_main_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        print(f"DEBUG: raise_main_callback called by {interaction.user.display_name}")
        try:
            if interaction.user.id != self.game.player.id:
                await interaction.response.send_message("This is not your Texas Hold 'em game!", ephemeral=True)
                print(f"DEBUG: Not player's turn for raise_main_callback. User: {interaction.user.id}, Player: {self.game.player.id}")
                return
            
            await interaction.response.defer() # Acknowledge the interaction
            print(f"DEBUG: Interaction deferred in raise_main_callback.")

            self.game.current_bet_buttons_visible = True
            self._set_button_states(self.game.game_phase, betting_buttons_visible=True)
            print(f"DEBUG: After _set_button_states in raise_main_callback. g_total: {self.game.g_total}")
            
            # Update the message and view (no direct edit_message here)
            await self.game._update_display_message(interaction, self)
            print(f"DEBUG: End of raise_main_callback, display updated.")
        except Exception as e:
            print(f"ERROR in raise_main_callback: {e}")
            if not interaction.response.is_done():
                await interaction.followup.send("An error occurred during your Raise action. Please try again or contact support.", ephemeral=True)


    @discord.ui.button(label="Call", style=discord.ButtonStyle.blurple, custom_id="holdem_call_main", row=0)
    async def call_main_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        print(f"DEBUG: call_main_callback called by {interaction.user.display_name}")
        try:
            if interaction.user.id != self.game.player.id:
                await interaction.response.send_message("This is not your Texas Hold 'em game!", ephemeral=True)
                print(f"DEBUG: Not player's turn for call_main_callback. User: {interaction.user.id}, Player: {self.game.player.id}")
                return
            
            await interaction.response.defer() # Defer to allow time for updates
            print(f"DEBUG: Interaction deferred in call_main_callback.")

            if self.game.game_phase == "pre_flop":
                self.game.g_total = self.game.minimum_bet * 2 # Player calls big blind, pot becomes 20
                print(f"DEBUG: Pre-flop Call. g_total updated to: {self.game.g_total}")
                self.game.deal_flop()
                self._set_button_states("flop")
                print(f"DEBUG: After deal_flop and _set_button_states in call_main_callback (pre-flop).")
            elif self.game.player_action_pending and self.game.dealer_raise_amount > 0:
                self.game.g_total += self.game.dealer_raise_amount * 2 # Player matches dealer's raise, dealer matches player's call
                self.game.dealer_raise_amount = 0 # Reset dealer's raise
                self.game.player_action_pending = False
                print(f"DEBUG: Call after dealer raise. g_total updated to: {self.game.g_total}")
                
                if self.game.game_phase == "flop":
                    self.game.deal_turn()
                    self._set_button_states("turn")
                elif self.game.game_phase == "turn":
                    self.game.deal_river()
                    self._set_button_states("river")
                else:
                    self._set_button_states(self.game.game_phase)
                print(f"DEBUG: After phase advance and _set_button_states in call_main_callback (post-flop).")
            else:
                await interaction.followup.send("Invalid call action.", ephemeral=True)
                self._set_button_states(self.game.game_phase) # Reset buttons
                await self.game._update_display_message(interaction, self)
                print(f"DEBUG: Invalid call action detected.")
                return

            if self.game.game_phase == "river" and not self.game.player_action_pending:
                self.game.game_phase = "showdown"
                self._end_game_buttons()
                await self.game._update_display_message(interaction, self, reveal_opponent=True)
                del active_texasholdem_games[self.game.channel_id]
                self.stop()
                print(f"DEBUG: Game ended via Showdown after Call on River.")
            else:
                await self.game._update_display_message(interaction, self)
                print(f"DEBUG: End of call_main_callback, display updated.")
        except Exception as e:
            print(f"ERROR in call_main_callback: {e}")
            if not interaction.response.is_done():
                await interaction.followup.send("An error occurred during your Call action. Please try again or contact support.", ephemeral=True)


    @discord.ui.button(label="Fold", style=discord.ButtonStyle.red, custom_id="holdem_fold_main", row=0)
    async def fold_main_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        print(f"DEBUG: fold_main_callback called by {interaction.user.display_name}")
        try:
            if interaction.user.id != self.game.player.id:
                await interaction.response.send_message("This is not your Texas Hold 'em game!", ephemeral=True)
                print(f"DEBUG: Not player's turn for fold_main_callback. User: {interaction.user.id}, Player: {self.game.player.id}")
                return
            
            await interaction.response.defer() # Defer to allow time for updates
            print(f"DEBUG: Interaction deferred in fold_main_callback.")

            kekchipz_lost = self.game.minimum_bet if self.game.game_phase == "pre_flop" else self.game.g_total / 2
            await update_user_kekchipz(interaction.guild.id, interaction.user.id, -int(kekchipz_lost))
            print(f"DEBUG: Kekchipz lost for fold: {int(kekchipz_lost)}. New kekchipz: {await get_user_kekchipz(interaction.guild.id, interaction.user.id)}")
            
            self._end_game_buttons()
            self.game.game_phase = "folded"
            await self.game._update_display_message(interaction, self, reveal_opponent=True)
            await interaction.followup.send(f"{self.game.player.display_name} folded. You lost ${int(kekchipz_lost)} kekchipz. Game over.")
            del active_texasholdem_games[self.game.channel_id]
            self.stop()
            print(f"DEBUG: Game ended via Fold.")
        except Exception as e:
            print(f"ERROR in fold_main_callback: {e}")
            if not interaction.response.is_done():
                await interaction.followup.send("An error occurred during your Fold action. Please try again or contact support.", ephemeral=True)


    @discord.ui.button(label="Check", style=discord.ButtonStyle.gray, custom_id="holdem_check_main", row=0)
    async def check_main_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        print(f"DEBUG: check_main_callback called by {interaction.user.display_name}")
        try:
            if interaction.user.id != self.game.player.id:
                await interaction.response.send_message("This is not your Texas Hold 'em game!", ephemeral=True)
                print(f"DEBUG: Not player's turn for check_main_callback. User: {interaction.user.id}, Player: {self.game.player.id}")
                return
            
            await interaction.response.defer()
            print(f"DEBUG: Interaction deferred in check_main_callback.")

            dealer_action = random.choice([1, 2])
            print(f"DEBUG: Dealer action: {dealer_action}")

            if dealer_action == 1:
                if self.game.game_phase == "flop":
                    self.game.deal_turn()
                    self._set_button_states("turn")
                elif self.game.game_phase == "turn":
                    self.game.deal_river()
                    self._set_button_states("river")
                elif self.game.game_phase == "river":
                    self.game.game_phase = "showdown"
                    self._end_game_buttons()
                    await self.game._update_display_message(interaction, self, reveal_opponent=True)
                    del active_texasholdem_games[self.game.channel_id]
                    self.stop()
                    print(f"DEBUG: Game ended via Showdown after Dealer Check on River.")
                    return
                
                await self.game._update_display_message(interaction, self)
                await interaction.followup.send("Serene checks.")
                print(f"DEBUG: Serene checked. g_total: {self.game.g_total}")
            else:
                raise_amount = random.choice([5, 10, 25])
                self.game.dealer_raise_amount = raise_amount
                self.game.player_action_pending = True
                print(f"DEBUG: Serene raises by {raise_amount}. g_total: {self.game.g_total}")

                self._set_button_states(self.game.game_phase, call_after_raise_enabled=True)
                await self.game._update_display_message(interaction, self)
                await interaction.followup.send(f"Serene raises by ${raise_amount}! You must Call or Fold.")
            print(f"DEBUG: End of check_main_callback.")
        except Exception as e:
            print(f"ERROR in check_main_callback: {e}")
            if not interaction.response.is_done():
                await interaction.followup.send("An error occurred during your Check action. Please try again or contact support.", ephemeral=True)


    @discord.ui.button(label="$5", style=discord.ButtonStyle.secondary, custom_id="holdem_bet_5", row=1)
    @discord.ui.button(label="$10", style=discord.ButtonStyle.secondary, custom_id="holdem_bet_10", row=1)
    @discord.ui.button(label="$25", style=discord.ButtonStyle.secondary, custom_id="holdem_bet_25", row=1)
    async def bet_amount_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        print(f"DEBUG: bet_amount_callback called by {interaction.user.display_name}. Button: {button.label}")
        try:
            if interaction.user.id != self.game.player.id:
                await interaction.response.send_message("This is not your Texas Hold 'em game!", ephemeral=True)
                print(f"DEBUG: Not player's turn for bet_amount_callback. User: {interaction.user.id}, Player: {self.game.player.id}")
                return
            
            await interaction.response.defer()
            print(f"DEBUG: Interaction deferred in bet_amount_callback.")

            bet_amount = int(button.label.replace('$', ''))
            print(f"DEBUG: Bet amount selected: {bet_amount}")
            
            self.game.handle_player_raise(bet_amount)
            print(f"DEBUG: After handle_player_raise. g_total: {self.game.g_total}")

            if self.game.game_phase == "pre_flop":
                self.game.deal_flop()
                self._set_button_states("flop")
                print(f"DEBUG: Advanced to Flop phase.")
            elif self.game.game_phase == "flop":
                self.game.deal_turn()
                self._set_button_states("turn")
                print(f"DEBUG: Advanced to Turn phase.")
            elif self.game.game_phase == "turn":
                self.game.deal_river()
                self._set_button_states("river")
                print(f"DEBUG: Advanced to River phase.")
            elif self.game.game_phase == "river":
                self.game.game_phase = "showdown"
                self._end_game_buttons()
                await self.game._update_display_message(interaction, self, reveal_opponent=True)
                del active_texasholdem_games[self.game.channel_id]
                self.stop()
                print(f"DEBUG: Game ended via Showdown after Bet on River.")
                return
            
            await self.game._update_display_message(interaction, self)
            print(f"DEBUG: End of bet_amount_callback, display updated.")
        except Exception as e:
            print(f"ERROR in bet_amount_callback: {e}")
            if not interaction.response.is_done():
                await interaction.followup.send("An error occurred during your Bet action. Please try again or contact support.", ephemeral=True)


    @discord.ui.button(label="Play Again", style=discord.ButtonStyle.blurple, custom_id="holdem_play_again", row=2, disabled=True)
    async def play_again_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        print(f"DEBUG: play_again_callback called by {interaction.user.display_name}")
        if interaction.user.id != self.game.player.id:
            await interaction.response.send_message("This is not your Texas Hold 'em game!", ephemeral=True)
            print(f"DEBUG: Not player's turn for play_again_callback. User: {interaction.user.id}, Player: {self.game.player.id}")
            return
        
        await interaction.response.defer()
        print(f"DEBUG: Interaction deferred in play_again_callback.")

        self.game.reset_game()
        self.game.deal_hole_cards()

        self._set_button_states("pre_flop")
        print(f"DEBUG: Game reset. New g_total: {self.game.g_total}")
        
        try:
            await self.game._update_display_message(interaction, self)
            active_texasholdem_games[self.game.channel_id] = self
            print(f"DEBUG: Game restarted successfully.")
        except discord.errors.NotFound:
            print("WARNING: Original game messages not found during 'Play Again' edit for Hold 'em.")
            await interaction.followup.send("Could not restart game. Please try `/serene game texas_hold_em` again.", ephemeral=True)
            if self.game.channel_id in active_texasholdem_games:
                del active_texasholdem_games[self.game.channel_id]
        except Exception as e:
            print(f"WARNING: An error occurred during 'Play Again' edit for Hold 'em: {e}")
            await interaction.followup.send("An error occurred while restarting the game.", ephemeral=True)
            if self.game.channel_id in active_texasholdem_games:
                del active_texasholdem_games[self.game.channel_id]


class TexasHoldEmGame:
    """
    Represents a single Texas Hold 'em game instance.
    Manages game state, player hands, and community cards.
    """
    def __init__(self, channel_id: int, player: discord.User):
        print(f"DEBUG: Initializing TexasHoldEmGame for channel {channel_id}, player {player.display_name}")
        self.channel_id = channel_id
        self.player = player # Human player
        self.bot_player = bot.user # Serene bot as opponent
        self.deck = self._create_standard_deck()
        self.player_hole_cards = []
        self.bot_hole_cards = []
        self.community_cards = []
        
        # Game state for betting
        self.minimum_bet = 10 # Represents the big blind
        self.g_total = 0 # Represents the total pot
        self.current_bet_buttons_visible = False # Flag to control visibility of $5, $10, $25 buttons
        self.dealer_raise_amount = 0 # Stores the amount dealer raised
        self.player_action_pending = False # True if player needs to respond to dealer's raise

        # Store reference to the single game message
        self.game_message = None
        
        self.game_phase = "pre_flop" # pre_flop, flop, turn, river, showdown, folded
        print(f"DEBUG: TexasHoldEmGame initialized. minimum_bet: {self.minimum_bet}, g_total: {self.g_total}")


    def _create_standard_deck(self) -> list[dict]:
        """
        Generates a standard 52-card deck with titles, numbers, and codes.
        """
        suits = ['S', 'D', 'C', 'H'] # Spades, Diamonds, Clubs, Hearts
        ranks = {
            'A': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9,
            '0': 10, 'J': 10, 'Q': 12, 'K': 13, 'A': 14 # '0' for Ten (as per deckofcardsapi.com)
        }
        rank_titles = {
            'A': 'Ace', '2': 'Two', '3': 'Three', '4': 'Four', '5': 'Five',
            '6': 'Six', '7': 'Seven', '8': 'Eight', '9': 'Nine', '0': 'Ten',
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
        """
        Deals a random card from the deck. Removes the card from the deck.
        Returns the dealt card (dict with 'title', 'cardNumber', and 'code').
        """
        if not self.deck:
            print("Warning: Deck is empty, cannot deal more cards.")
            return {"title": "No Card", "cardNumber": 0, "code": "NO_CARD"} 
        
        card = random.choice(self.deck)
        self.deck.remove(card)
        return card

    def deal_hole_cards(self):
        """Deals 2 hole cards to each player."""
        self.player_hole_cards = [self.deal_card(), self.deal_card()]
        self.bot_hole_cards = [self.deal_card(), self.deal_card()]
        self.game_phase = "pre_flop"
        print(f"DEBUG: Hole cards dealt. Player: {[c['code'] for c in self.player_hole_cards]}, Bot: {[c['code'] for c in self.bot_hole_cards]}")


    def deal_flop(self):
        """Deals 3 community cards (the flop)."""
        self.community_cards.extend([self.deal_card(), self.deal_card(), self.deal_card()])
        self.game_phase = "flop"
        print(f"DEBUG: Flop dealt. Community cards: {[c['code'] for c in self.community_cards]}")

    def deal_turn(self):
        """Deals 1 community card (the turn)."""
        self.community_cards.append(self.deal_card())
        self.game_phase = "turn"
        print(f"DEBUG: Turn dealt. Community cards: {[c['code'] for c in self.community_cards]}")

    def deal_river(self):
        """Deals 1 community card (the river)."""
        self.community_cards.append(self.deal_card())
        self.game_phase = "river"
        print(f"DEBUG: River dealt. Community cards: {[c['code'] for c in self.community_cards]}")

    def handle_player_raise(self, bet_amount: int):
        """Handles player's raise action."""
        print(f"DEBUG: handle_player_raise called. Current g_total: {self.g_total}, Bet amount: {bet_amount}")
        if self.game_phase == "pre_flop":
            self.g_total = (self.minimum_bet * 2) + (bet_amount * 2)
            print(f"DEBUG: Pre-flop raise. New g_total: {self.g_total}")
        else:
            self.g_total += (bet_amount * 2)
            print(f"DEBUG: Post-flop raise. New g_total: {self.g_total}")

        self.current_bet_buttons_visible = False
        self.dealer_raise_amount = 0
        self.player_action_pending = False

    def handle_player_fold(self):
        """Handles player's fold action."""
        pass

    def reset_game(self):
        """Resets the game state for a new round."""
        print("DEBUG: Resetting game state.")
        self.deck = self._create_standard_deck()
        random.shuffle(self.deck)
        self.player_hole_cards = []
        self.bot_hole_cards = []
        self.community_cards = []
        self.game_phase = "pre_flop"
        self.g_total = 0
        self.current_bet_buttons_visible = False
        self.dealer_raise_amount = 0
        self.player_action_pending = False
        print(f"DEBUG: Game state reset. g_total: {self.g_total}")


    async def _create_combined_holdem_image(self, player_name: str, bot_name: str, reveal_opponent: bool = False) -> Image.Image:
        """
        Creates a single combined image for Texas Hold 'em, showing dealer's cards,
        community cards, and player's cards, along with text labels.

        Args:
            player_name (str): The display name of the human player.
            bot_name (str): The display name of the bot player.
            reveal_opponent (bool): If True, reveals the bot's hole cards.

        Returns:
            PIL.Image.Image: A Pillow Image object containing the combined game state.
        """
        print(f"DEBUG: _create_combined_holdem_image called. Reveal opponent: {reveal_opponent}, Game phase: {self.game_phase}")
        # Define image scaling and padding
        card_scale_factor = 1.0
        card_overlap_percent = 0.33
        vertical_padding = 40
        text_padding_x = 20
        text_padding_y = 30

        # Get individual card images
        # Bot's hand
        bot_display_card_codes = [card['code'] for card in self.bot_hole_cards if 'code' in card] if reveal_opponent else ["XX", "XX"]
        bot_hand_img = await create_card_combo_image(','.join(bot_display_card_codes), scale_factor=card_scale_factor, overlap_percent=card_overlap_percent)
        print(f"DEBUG: Bot hand image created. Codes: {bot_display_card_codes}")

        # Community cards
        community_card_codes = [card['code'] for card in self.community_cards if 'code' in card]
        community_img = await create_card_combo_image(','.join(community_card_codes), scale_factor=card_scale_factor, overlap_percent=card_overlap_percent)
        print(f"DEBUG: Community cards image created. Codes: {community_card_codes}")
        
        # Player's hand
        player_card_codes = [card['code'] for card in self.player_hole_cards if 'code' in card]
        player_hand_img = await create_card_combo_image(','.join(player_card_codes), scale_factor=card_scale_factor, overlap_percent=card_overlap_percent)
        print(f"DEBUG: Player hand image created. Codes: {player_card_codes}")

        # --- Font Loading ---
        font_url = "http://serenekeks.com/OpenSans-CondBold.ttf"
        font_large = ImageFont.load_default()
        font_medium = ImageFont.load_default()
        font_small = ImageFont.load_default()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(font_url) as response:
                    response.raise_for_status()
                    font_bytes = await response.read()
                    font_io = io.BytesIO(font_bytes)
                    font_large = ImageFont.truetype(font_io, 48)
                    font_io.seek(0)
                    font_medium = ImageFont.truetype(font_io, 36)
                    font_io.seek(0)
                    font_small = ImageFont.truetype(font_io, 28)
                    print(f"Successfully loaded font from {font_url}")
        except aiohttp.ClientError as e:
            print(f"WARNING: Failed to fetch font from {font_url}: {e}. Using default Pillow font.")
        except Exception as e:
            print(f"WARNING: Error loading font from bytes: {e}. Using default Pillow font.")

        # Define Discord purple color (R, G, B)
        discord_purple = (114, 137, 218)

        # Calculate text heights and widths for layout
        dummy_img = Image.new('RGBA', (1, 1))
        dummy_draw = ImageDraw.Draw(dummy_img)

        dealer_text = "Serene's Hand"
        player_text = f"{player_name}'s Hand"
        
        # Determine showdown result if applicable
        showdown_result_text = ""
        if reveal_opponent and self.game_phase == "showdown":
            player_all_cards = self.player_hole_cards + self.community_cards
            bot_all_cards = self.bot_hole_cards + self.community_cards

            player_best_hand = evaluate_best_hand([c['code'] for c in player_all_cards])
            bot_best_hand = evaluate_best_hand([c['code'] for c in bot_all_cards])

            player_hand_name = hand_name(player_best_hand[0])
            bot_hand_name = hand_name(bot_best_hand[0])

            comparison = compare_scores(player_best_hand, bot_best_hand)

            if comparison > 0:
                showdown_result_text = f"{player_name} wins with {player_hand_name}!"
                await update_user_kekchipz(self.player.guild.id, self.player.id, 200)
            elif comparison < 0:
                showdown_result_text = f"Serene wins with {bot_hand_name}!"
                await update_user_kekchipz(self.player.guild.id, self.player.id, -100)
            else:
                showdown_result_text = f"It's a tie with {player_hand_name}!"
                await update_user_kekchipz(self.player.guild.id, self.player.id, 50)
        print(f"DEBUG: Showdown result text: '{showdown_result_text}'")

        # Calculate text dimensions
        showdown_text_width = 0
        showdown_text_height = 0
        if showdown_result_text:
            bbox = dummy_draw.textbbox((0,0), showdown_result_text, font=font_large)
            showdown_text_width = bbox[2] - bbox[0]
            showdown_text_height = bbox[3] - bbox[1]

        bbox = dummy_draw.textbbox((0,0), dealer_text, font=font_medium)
        dealer_text_width = bbox[2] - bbox[0]
        dealer_text_height = bbox[3] - bbox[1]

        bbox = dummy_draw.textbbox((0,0), player_text, font=font_medium)
        player_text_width = bbox[2] - bbox[0]
        player_text_height = bbox[3] - bbox[1]
        
        # Determine overall image dimensions
        max_content_width = max(
            bot_hand_img.width,
            community_img.width,
            player_hand_img.width,
            showdown_text_width,
            dealer_text_width,
            player_text_width
        )
        combined_image_width = max_content_width + text_padding_x * 4
        
        total_height = (
            vertical_padding +
            showdown_text_height + text_padding_y +
            dealer_text_height + text_padding_y +
            bot_hand_img.height + vertical_padding +
            community_img.height + vertical_padding +
            player_text_height + text_padding_y +
            player_hand_img.height + vertical_padding
        )
        print(f"DEBUG: Combined image dimensions: {combined_image_width}x{total_height}")

        combined_image = Image.new('RGBA', (combined_image_width, total_height), (0, 0, 0, 0))

        draw = ImageDraw.Draw(combined_image)

        current_y_offset = vertical_padding

        if showdown_result_text:
            showdown_x_offset = (combined_image.width - showdown_text_width) // 2
            draw.text((showdown_x_offset, current_y_offset), showdown_result_text, font=font_large, fill=(255, 255, 0))
            current_y_offset += showdown_text_height + text_padding_y


        dealer_text_x_offset = (combined_image.width - dealer_text_width) // 2
        draw.text((dealer_text_x_offset, current_y_offset), dealer_text, font=font_medium, fill=discord_purple)
        current_y_offset += dealer_text_height + text_padding_y

        dealer_img_x_offset = (combined_image.width - bot_hand_img.width) // 2

        if self.dealer_raise_amount > 0 and not reveal_opponent:
            dealer_raise_text = f"Raise: ${self.dealer_raise_amount}"
            bbox = dummy_draw.textbbox((0,0), dealer_raise_text, font=font_small)
            dealer_raise_text_width = bbox[2] - bbox[0]
            dealer_raise_text_height = bbox[3] - bbox[1]
            dealer_raise_x = dealer_img_x_offset - dealer_raise_text_width - text_padding_x
            draw.text((dealer_raise_x, current_y_offset + bot_hand_img.height // 2 - dealer_raise_text_height // 2),
                      dealer_raise_text, font=font_small, fill=(255, 165, 0))

        combined_image.paste(bot_hand_img, (dealer_img_x_offset, current_y_offset), bot_hand_img)
        current_y_offset += bot_hand_img.height + vertical_padding

        community_img_x_offset = (combined_image.width - community_img.width) // 2
        combined_image.paste(community_img, (community_img_x_offset, current_y_offset), community_img)
        current_y_offset += community_img.height + vertical_padding

        player_text_x_offset = (combined_image.width - player_text_width) // 2
        draw.text((player_text_x_offset, current_y_offset), player_text, font=font_medium, fill=discord_purple)
        current_y_offset += player_text_height + text_padding_y

        player_img_x_offset = (combined_image.width - player_hand_img.width) // 2

        min_text = f"Minimum: ${self.minimum_bet}"
        gtotal_text = f"Gtotal: ${self.g_total}"

        bbox_min = dummy_draw.textbbox((0,0), min_text, font=font_small)
        min_text_width = bbox_min[2] - bbox_min[0]
        min_text_height = bbox_min[3] - bbox_min[1]

        bbox_gtotal = dummy_draw.textbbox((0,0), gtotal_text, font=font_small)
        gtotal_text_width = bbox_gtotal[2] - bbox_gtotal[0]
        gtotal_text_height = bbox_gtotal[3] - bbox_gtotal[1]

        player_info_x = player_img_x_offset - max(min_text_width, gtotal_text_width) - text_padding_x
        player_info_y_start = current_y_offset + player_hand_img.height // 2 - (min_text_height + gtotal_text_height + 5) // 2

        draw.text((player_info_x, player_info_y_start), min_text, font=font_small, fill=(255, 255, 255))
        draw.text((player_info_x, player_info_y_start + min_text_height + 5), gtotal_text, font=font_small, fill=(0, 255, 0))


        combined_image.paste(player_hand_img, (player_img_x_offset, current_y_offset), player_hand_img)
        current_y_offset += player_hand_img.height + vertical_padding
        print(f"DEBUG: Image creation complete.")
        return combined_image

    async def _update_display_message(self, interaction: discord.Interaction, view: TexasHoldEmGameView, reveal_opponent: bool = False):
        """
        Updates the single game message for Texas Hold 'em with the combined image.
        """
        print(f"DEBUG: _update_display_message called. Current g_total: {self.g_total}")
        try:
            player_kekchipz = await get_user_kekchipz(self.player.guild.id, self.player.id)
            print(f"DEBUG: Player kekchipz: {player_kekchipz}")
            combined_image_pil = await self._create_combined_holdem_image(
                self.player.display_name,
                self.bot_player.display_name,
                reveal_opponent=reveal_opponent
            )
            print("DEBUG: Combined image PIL created.")

            combined_image_bytes = io.BytesIO()
            combined_image_pil.save(combined_image_bytes, format='PNG')
            combined_image_bytes.seek(0)
            combined_file = discord.File(combined_image_bytes, filename="texas_holdem_game.png")
            print("DEBUG: Combined image file created.")

            message_content = f"**{self.player.display_name}'s Kekchipz:** ${player_kekchipz}"
            print(f"DEBUG: Message content: {message_content}")

            if self.game_message:
                print(f"DEBUG: Editing existing game message {self.game_message.id}.")
                try:
                    await self.game_message.edit(content=message_content, view=view, attachments=[combined_file])
                    print("DEBUG: Message edited successfully.")
                except discord.errors.NotFound:
                    print("WARNING: Game message not found during edit. Attempting to re-send.")
                    self.game_message = await interaction.channel.send(content=message_content, view=view, files=[combined_file])
                    print(f"DEBUG: Message re-sent. New message ID: {self.game_message.id}")
                except Exception as e:
                    print(f"WARNING: Error editing game message: {e}")
                    self.game_message = await interaction.channel.send(content="An error occurred updating the game display.", view=view, files=[combined_file])
                    print(f"DEBUG: Error fallback: message re-sent. New message ID: {self.game_message.id}")
            else:
                print("DEBUG: Sending new game message.")
                self.game_message = await interaction.channel.send(content=message_content, view=view, files=[combined_file])
                print(f"DEBUG: New game message sent. ID: {self.game_message.id}")
        except Exception as e:
            print(f"ERROR in _update_display_message: {e}")
            if not interaction.response.is_done():
                await interaction.followup.send("An internal error occurred while updating the game display. Please try again.", ephemeral=True)


    async def start_game(self, interaction: discord.Interaction):
        """
        Starts the Texas Hold 'em game: shuffles, deals initial hands,
        and displays the initial state in a single message with a combined image.
        """
        print(f"DEBUG: start_game called for channel {self.channel_id}")
        random.shuffle(self.deck)
        self.deal_hole_cards()
        
        self.g_total = self.minimum_bet
        print(f"DEBUG: Initial g_total after bot's blind: {self.g_total}")

        game_view = TexasHoldEmGameView(game=self)
        
        game_view._set_button_states("pre_flop")
        print("DEBUG: Initial button states set for pre_flop.")

        combined_image_pil = await self._create_combined_holdem_image(self.player.display_name, self.bot_player.display_name)
        combined_image_bytes = io.BytesIO()
        combined_image_pil.save(combined_image_bytes, format='PNG')
        combined_image_bytes.seek(0)
        combined_file = discord.File(combined_image_bytes, filename="texas_holdem_game.png")
        print("DEBUG: Initial combined image file prepared.")

        self.game_message = await interaction.followup.send(
            content=f"**{self.player.display_name}'s Kekchipz:** ${await get_user_kekchipz(self.player.guild.id, self.player.id)}",
            view=game_view,
            files=[combined_file]
        )
        game_view.message = self.game_message
        print(f"DEBUG: Initial game message sent. Message ID: {self.game_message.id}")

        active_texasholdem_games[self.channel_id] = game_view
        print(f"DEBUG: Game started successfully for channel {self.channel_id}.")


@serene_group.command(name="game", description="Start a fun game with Serene!")
@app_commands.choices(game_type=[
    app_commands.Choice(name="Tic-Tac-Toe", value="tic_tac_toe"),
    app_commands.Choice(name="Jeopardy", value="jeopardy"),
    app_commands.Choice(name="Blackjack", value="blackjack"),
    app_commands.Choice(name="Texas Hold 'em", value="texas_hold_em"),
])
@app_commands.describe(game_type="The type of game to play.")
async def game_command(interaction: discord.Interaction, game_type: str):
    """
    Handles the /serene game slash command.
    Starts the selected game directly.
    """
    print(f"DEBUG: game_command called for game_type: {game_type}")
    await interaction.response.defer(ephemeral=True)
    print("DEBUG: Interaction deferred (ephemeral).")

    if game_type == "tic_tac_toe":
        if interaction.channel.id in active_tictactoe_games:
            await interaction.followup.send(
                "A Tic-Tac-Toe game is already active in this channel! Please finish it or wait.",
                ephemeral=True
            )
            print("DEBUG: Tic-Tac-Toe game already active.")
            return

        player1 = interaction.user
        player2 = bot.user

        await interaction.followup.send(
            f"Starting Tic-Tac-Toe for {player1.display_name} vs. {player2.display_name}...",
            ephemeral=True
        )
        print("DEBUG: Starting Tic-Tac-Toe.")

        game_view = TicTacToeView(player_x=player1, player_o=player2)
        
        game_message = await interaction.channel.send(
            content=f"It's **{player1.display_name}**'s turn (X)",
            embed=game_view._start_game_message(),
            view=game_view
        )
        game_view.message = game_message
        active_tictactoe_games[interaction.channel.id] = game_view
        print(f"DEBUG: Tic-Tac-Toe game started in channel {interaction.channel.id}.")

    elif game_type == "jeopardy":
        if interaction.channel.id in active_jeopardy_games:
            await interaction.followup.send(
                "A Jeopardy game is already active in this channel! Please finish it or wait.",
                ephemeral=True
            )
            print("DEBUG: Jeopardy game already active.")
            return
        
        await interaction.followup.send("Setting up Jeopardy game...", ephemeral=True)
        print("DEBUG: Setting up Jeopardy game.")
        
        jeopardy_game = NewJeopardyGame(interaction.channel.id, interaction.user)
        
        success = await jeopardy_game.fetch_and_parse_jeopardy_data()

        if success:
            active_jeopardy_games[interaction.channel.id] = jeopardy_game
            
            jeopardy_view = JeopardyGameView(jeopardy_game)
            jeopardy_view.add_board_components()
            
            game_message = await interaction.channel.send(
                content=(
                    f"**{jeopardy_game.player.display_name}**'s Score: **{'-' if jeopardy_game.score < 0 else ''}${abs(jeopardy_game.score)}**\n\n"
                    "Select a category and value from the dropdowns below!"
                ),
                view=jeopardy_view
            )
            jeopardy_game.board_message = game_message
            print(f"DEBUG: Jeopardy game started in channel {interaction.channel.id}.")

        else:
            await interaction.followup.send(
                "Failed to load Jeopardy game data. Please try again later.",
                ephemeral=True
            )
            print("DEBUG: Failed to load Jeopardy game data.")
            return
    elif game_type == "blackjack":
        if interaction.channel.id in active_blackjack_games:
            await interaction.followup.send(
                "A Blackjack game is already active in this channel! Please finish it or wait.",
                ephemeral=True
            )
            print("DEBUG: Blackjack game already active.")
            return
        
        await interaction.followup.send("Setting up Blackjack game...", ephemeral=True)
        print("DEBUG: Setting up Blackjack game.")
        
        blackjack_game = BlackjackGame(interaction.channel.id, interaction.user)
        
        await blackjack_game.start_game(interaction)
        print(f"DEBUG: Blackjack game started in channel {interaction.channel.id}.")

    elif game_type == "texas_hold_em":
        if interaction.channel.id in active_texasholdem_games:
            await interaction.followup.send(
                "A Texas Hold 'em game is already active in this channel! Please finish it or wait.",
                ephemeral=True
            )
            print("DEBUG: Texas Hold 'em game already active.")
            return
        
        await interaction.followup.send("Setting up Texas Hold 'em game...", ephemeral=True)
        print("DEBUG: Setting up Texas Hold 'em game.")
        
        holdem_game = TexasHoldEmGame(interaction.channel.id, interaction.user)
        
        await holdem_game.start_game(interaction)
        print(f"DEBUG: Texas Hold 'em game started in channel {interaction.channel.id}.")

    else:
        await interaction.followup.send(
            f"Game type '{game_type}' is not yet implemented. Stay tuned!",
            ephemeral=True
        )
        print(f"DEBUG: Game type '{game_type}' not implemented.")

# Load environment variables for the token
BOT_TOKEN = os.getenv('BOT_TOKEN')

if BOT_TOKEN is None:
    print("Error: BOT_TOKEN environment variable not set.")
else:
    bot.run(BOT_TOKEN)
