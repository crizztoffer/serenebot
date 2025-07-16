import os
import random
import urllib.parse
import json
import asyncio
import re # Import the re module for regular expressions

import discord
from discord.ext import commands
from discord import app_commands, ui
import aiohttp

# Define intents
intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.message_content = True

# Initialize the bot
bot = commands.Bot(command_prefix='!', intents=intents)

# --- Game State Storage ---
active_tictactoe_games = {}
active_jeopardy_games = {} # Re-introducing this for the new Jeopardy game

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
                                if similarity >= 70.0: # Threshold for similarity
                                    is_correct = True
                                    break # Found a sufficiently similar significant word
                            if is_correct:
                                break # No need to check further user words if a match is found
                
                # Compare the processed user answer with the correct answer
                if is_correct:
                    game.score += game.current_wager # Use wager for score
                    await interaction.followup.send(
                        f"✅ Correct, {game.player.display_name}! Your score is now **{'-' if game.score < 0 else ''}${abs(game.score)}**." # Format negative score
                    )
                else:
                    game.score -= game.current_wager # Use wager for score
                    # Removed spoiler tags, added quotes, and ensured full answer is bold/underlined
                    full_correct_answer = f'"{determined_prefix} {question_data["answer"]}"'.strip()
                    await interaction.followup.send(
                        f"❌ Incorrect, {game.player.display_name}! The correct answer was: "
                        f"**__{full_correct_answer}__**. Your score is now **{'-' if game.score < 0 else ''}${abs(game.score)}**." # Format negative score
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
                            del active_jeopardy_games[game.channel.id]
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
                    if game.channel.id in active_jeopardy_games:
                        del active_jeopardy_games[game.channel.id]
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
                    board_message_content = f"**{game.player.display_name}**'s Score: **{'-' if game.score < 0 else ''}${abs(game.score)}**\n\n" \
                                            "Select a category and value from the dropdowns below!"
                elif game.game_phase == "DOUBLE_JEOPARDY":
                    board_message_content = f"**{game.player.display_name}**'s Score: **{'-' if game.score < 0 else ''}${abs(game.score)}**\n\n" \
                                            "**Double Jeopardy!** Select a category and value from the dropdowns below!"
                
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
        self._selected_category = None # Stores the category selected by the user
        self._selected_value = None # Stores the value selected by the user

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
        if self.game.board_message:
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
            async with aiohttp.ClientSession() as session:
                async with session.get(self.jeopardy_data_url) as response:
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
                        self.double_jeopardy_data = {"double_jeopardy": full_data.get("double_jeopardy", [])}
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
            self.style = discord.ButtonStyle.primary # Blue for X
        else: # self.player_mark == "O"
            self.style = discord.ButtonStyle.danger # Red for O
            
        self.disabled = True
        view.board[self.row][self.col] = self.player_mark # Update internal board state

        # Defer the interaction response to allow time for bot's move if needed
        await interaction.response.defer()

        # Check for win or draw after human's move
        if view._check_winner():
            winner = view.players[view.current_player].display_name
            await interaction.edit_original_response(
                content=f"🎉 **{winner} wins!** 🎉",
                embed=view._start_game_message(),
                view=view._end_game()
            )
            del active_tictactoe_games[interaction.channel.id] # End the game
        elif view._check_draw():
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
        self.board = [[" ", " ", " "], [" ", " ", " "], [" ", " ", " "]] # Internal board uses " " for empty
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
        if all(board[i][i] == player for i in range(3)): return True # Diagonal \
        if all(board[i][2-i] == player for i in range(3)): return True # Diagonal /
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
            self.board[r][c] = " " # Undo hypothetical move

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
                winner = self.players[self.current_player].display_name
                await interaction.edit_original_response(
                    content=f"🎉 **{winner} wins!** 🎉",
                    embed=self._start_game_message(),
                    view=self._end_game()
                )
                del active_tictactoe_games[interaction.channel.id]
            elif self._check_draw():
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
        if self.message:
            try:
                await self.message.edit(content="Game timed out due to inactivity.", view=None, embed=None)
            except discord.errors.NotFound:
                print("WARNING: Board message not found during timeout, likely already deleted.")
            except Exception as e:
                print(f"WARNING: An error occurred editing board message on timeout: {e}")
        
        # Changed self.game.channel_id to self.game.channel_id
        if self.game.channel_id in active_tictactoe_games:
            del active_tictactoe_games[self.game.channel_id]
        print(f"Tic-Tac-Toe game in channel {self.game.channel_id} timed out.")


# --- Bot Events ---
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

@bot.event
async def on_message(message: discord.Message):
    """Listens for messages to handle Jeopardy answers."""
    # Ignore messages from the bot itself
    if message.author.id == bot.user.id:
        return

    # Process other commands normally
    await bot.process_commands(message)


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
    else: # Simplified to avoid complex CVC rule that caused "weatherred"
        return verb + 'ed'


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
        - "kissed Crizz P."
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


@serene_group.command(name="game", description="Start a fun game with Serene!")
@app_commands.choices(game_type=[
    app_commands.Choice(name="Tic-Tac-Toe", value="tic_tac_toe"),
    app_commands.Choice(name="Jeopardy", value="jeopardy"),
])
@app_commands.describe(game_type="The type of game to play.")
async def game_command(interaction: discord.Interaction, game_type: str):
    """
    Handles the /serene game slash command.
    Starts the selected game directly.
    """
    await interaction.response.defer(ephemeral=True)

    if game_type == "tic_tac_toe":
        if interaction.channel.id in active_tictactoe_games:
            await interaction.followup.send(
                "A Tic-Tac-Toe game is already active in this channel! Please finish it or wait.",
                ephemeral=True
            )
            return

        player1 = interaction.user
        player2 = bot.user

        await interaction.followup.send(
            f"Starting Tic-Tac-Toe for {player1.display_name} vs. {player2.display_name}...",
            ephemeral=True
        )

        game_view = TicTacToeView(player_x=player1, player_o=player2)
        
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
        
        jeopardy_game = NewJeopardyGame(interaction.channel.id, interaction.user)
        
        success = await jeopardy_game.fetch_and_parse_jeopardy_data()

        if success:
            active_jeopardy_games[interaction.channel.id] = jeopardy_game
            
            jeopardy_view = JeopardyGameView(jeopardy_game)
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
    else:
        await interaction.followup.send(
            f"Game type '{game_type}' is not yet implemented. Stay tuned!",
            ephemeral=True
        )

# Load environment variables for the token
BOT_TOKEN = os.getenv('BOT_TOKEN')

if BOT_TOKEN is None:
    print("Error: BOT_TOKEN environment variable not set.")
else:
    bot.run(BOT_TOKEN)
