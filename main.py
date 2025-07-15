import os
import random
import urllib.parse
import json
import asyncio # Import asyncio for sleep

import discord
from discord.ext import commands
from discord import app_commands, ui
import aiohttp

# Define intents
intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.message_content = True # Needed to read user answers for Jeopardy

# Initialize the bot
bot = commands.Bot(command_prefix='!', intents=intents)

# --- Game State Storage ---
# Stores active Tic-Tac-Toe games. Key: channel_id, Value: TicTacToeView instance
active_tictactoe_games = {}
# Stores active Jeopardy games. Key: channel_id, Value: JeopardyGame instance
active_jeopardy_games = {}

# --- Jeopardy Game Classes ---

class CategoryValueSelect(discord.ui.Select):
    """A dropdown (select) for choosing a question's value within a specific category."""
    def __init__(self, category_name: str, options: list[discord.SelectOption], placeholder: str):
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"jeopardy_select_{category_name.replace(' ', '_').lower()}"
        )
        self.category_name = category_name # Store category name for later use

    async def callback(self, interaction: discord.Interaction):
        """Handles a selection from the dropdown."""
        view: JeopardyGameView = self.view
        game: JeopardyGame = view.game

        # Store the selected category and value in the view's state
        selected_value_str = self.values[0] # The selected value is always a string from SelectOption
        selected_value = int(selected_value_str) # Convert back to int

        view._selected_category = self.category_name
        view._selected_value = selected_value
        
        # Enable the "Pick Question" button once a selection is made
        for item in view.children:
            if isinstance(item, PickQuestionButton):
                item.disabled = False
                break
        
        # Respond to the interaction by editing the message to update the view
        # This makes the "Pick Question" button clickable
        await interaction.response.edit_message(view=view)

class PickQuestionButton(discord.ui.Button):
    """A button to confirm the selection of a Jeopardy question."""
    def __init__(self):
        # The button is initially disabled until a question is selected via dropdown
        # Removed row=4 to allow automatic placement on the next available row
        super().__init__(style=discord.ButtonStyle.green, label="Pick Question", disabled=True) 

    async def callback(self, interaction: discord.Interaction):
        """Handles the click on the 'Pick Question' button."""
        view: JeopardyGameView = self.view
        game: JeopardyGame = view.game

        # Acquire a lock to prevent multiple simultaneous question selections
        async with game.answer_lock:
            if game.game_over:
                await interaction.response.send_message("The game is over!", ephemeral=True)
                return

            if interaction.user.id != game.player.id:
                await interaction.response.send_message("You are not the active player for this Jeopardy game.", ephemeral=True)
                return

            if game.current_question:
                await interaction.response.send_message("A question is already active. Please answer it first!", ephemeral=True)
                return
            
            # Retrieve the selected category and value from the view's state
            selected_category = view._selected_category
            selected_value = view._selected_value

            if not selected_category or selected_value is None:
                await interaction.response.send_message("Please select a category and value first using the dropdowns!", ephemeral=True)
                return

            # Find the actual question data from the game's board data
            question_data = None
            categories_in_current_phase = []
            if game.game_phase == "NORMAL_JEOPARDY_SELECTION":
                categories_in_current_phase = game.board_data.get("normal_jeopardy", [])
            elif game.game_phase == "DOUBLE_JEOPARDY_SELECTION":
                categories_in_current_phase = game.board_data.get("double_jeopardy", [])

            for cat_data in categories_in_current_phase:
                if cat_data["category"] == selected_category:
                    for q_data in cat_data["questions"]:
                        if q_data["value"] == selected_value and not q_data["guessed"]:
                            question_data = q_data
                            break
                    if question_data:
                        break
            
            if question_data:
                # Mark the question as guessed immediately in the game state
                question_data["guessed"] = True
                
                # Clear the view's internal selection state
                view._selected_category = None
                view._selected_value = None

                # Present the selected question to the user
                await game.present_question(interaction, question_data)
            else:
                # If for some reason the question is not found or already guessed (race condition)
                await interaction.response.send_message(
                    f"Question '{selected_category}' for ${selected_value} not found or already guessed. Please select another.",
                    ephemeral=True
                )

class JeopardyGameView(discord.ui.View):
    """The Discord UI View that holds the interactive Jeopardy board dropdowns and pick button."""
    def __init__(self, game: 'JeopardyGame'):
        super().__init__(timeout=300) # View times out after 5 minutes of inactivity
        self.game = game # Reference to the JeopardyGame instance
        self._selected_category = None # Stores the category selected by the user
        self._selected_value = None # Stores the value selected by the user

    def add_buttons_from_board(self):
        """Dynamically adds dropdowns (selects) for categories and the 'Pick Question' button to the view."""
        self.clear_items() # Clear existing items before rebuilding the board

        categories_to_display = []
        if self.game.game_phase == "NORMAL_JEOPARDY_SELECTION":
            # Limit to the first 5 categories for dropdown display to fit Discord's row limit
            categories_to_display = self.game.board_data.get("normal_jeopardy", [])[:5] 
        elif self.game.game_phase == "DOUBLE_JEOPARDY_SELECTION":
            # Limit to the first 5 categories for dropdown display
            categories_to_display = self.game.board_data.get("double_jeopardy", [])[:5]
        else:
            # No interactive components (dropdowns/buttons) for Final Jeopardy or other phases
            return

        # Add a Select (dropdown) component for each category
        # Discord allows up to 5 components per row.
        # By limiting categories_to_display to 5, all dropdowns will fit on a single row (Row 0).
        
        for category_data in categories_to_display:
            category_name = category_data["category"]
            options = []
            # Populate options with available (unguessed) question values for this category
            for q in category_data["questions"]:
                if not q["guessed"]:
                    options.append(discord.SelectOption(label=f"${q['value']}", value=str(q['value'])))
            
            # Only add a dropdown if there are available questions in the category
            # If options is empty, it means all questions in this category are guessed, so we don't add its dropdown.
            if options: 
                self.add_item(CategoryValueSelect(category_name, options, f"Pick for {category_name}"))
        
        # Add the "Pick Question" button if there are any active dropdowns (meaning there are still questions to pick)
        # and if the game is in a selection phase.
        if (self.game.game_phase == "NORMAL_JEOPARDY_SELECTION" or 
            self.game.game_phase == "DOUBLE_JEOPARDY_SELECTION") and any(isinstance(item, CategoryValueSelect) for item in self.children):
            self.add_item(PickQuestionButton())

    async def on_timeout(self):
        """Called when the view times out due to inactivity."""
        if self.game.board_message:
            # Edit the message to remove the interactive components and indicate timeout
            await self.game.board_message.edit(content="Jeopardy game timed out due to inactivity.", view=None, embed=None)
        if self.game.channel_id in active_jeopardy_games:
            # Clean up the game state
            del active_jeopardy_games[self.game.channel_id]
        print(f"Jeopardy game in channel {self.game.channel_id} timed out.")


class JeopardyGame:
    """Manages the state and logic for a single Jeopardy game."""
    def __init__(self, channel_id: int, player: discord.User):
        self.channel_id = channel_id
        self.player = player
        self.score = 0
        self.board_data = None # Will store the fetched JSON data
        self.current_question = None # Stores the question currently being answered
        self.board_message = None # Stores the Discord message for the main board display
        self.question_message = None # Stores the Discord message for the current question being asked
        self.timer_task = None # asyncio task for the countdown timer
        self.answer_lock = asyncio.Lock() # To prevent multiple answers/selections at once
        self.game_over = False
        # Game phases:
        # LOADING: Initial state while fetching data.
        # NORMAL_JEOPARDY_SELECTION: Player can select questions from Normal Jeopardy.
        # QUESTION_ACTIVE: A question is currently being presented for answer.
        # DOUBLE_JEOPARDY_SELECTION: Player can select questions from Double Jeopardy.
        # FINAL_JEOPARDY_WAGER: Player needs to place a wager for Final Jeopardy.
        # FINAL_JEOPARDY_ACTIVE: Final Jeopardy question is presented for answer.
        # GAME_OVER: Game has concluded.
        self.game_phase = "LOADING" 

        # URL to fetch Jeopardy data from the PHP backend
        self.jeopardy_data_url = "https://serenekeks.com/serene_bot_games.php"
        
    async def load_board_data(self):
        """Fetches Jeopardy questions from the backend and initializes game state."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.jeopardy_data_url) as response:
                    if response.status == 200:
                        self.board_data = await response.json()
                        # Initialize 'guessed' status for all questions and add category name to each question
                        for category_type in ["normal_jeopardy", "double_jeopardy"]:
                            if category_type in self.board_data:
                                for category in self.board_data[category_type]:
                                    for question_data in category["questions"]:
                                        question_data["guessed"] = False
                                        question_data["category"] = category["category"] # Store category name in question for easy access
                        if "final_jeopardy" in self.board_data:
                            self.board_data["final_jeopardy"]["guessed"] = False
                            # Ensure Final Jeopardy also has a category name for display
                            self.board_data["final_jeopardy"]["category"] = self.board_data["final_jeopardy"].get("category", "Final Jeopardy")
                        
                        # Set initial game phase to Normal Jeopardy selection
                        self.game_phase = "NORMAL_JEOPARDY_SELECTION"
                    else:
                        print(f"Error fetching Jeopardy data: HTTP Status {response.status}")
                        self.board_data = None
                        self.game_phase = "GAME_OVER" # Indicate failure
        except Exception as e:
            print(f"Error loading Jeopardy data: {e}")
            self.board_data = None
            self.game_phase = "GAME_OVER" # Indicate failure

    def _get_board_display_embed(self) -> discord.Embed:
        """Creates an embed to display the current Jeopardy board based on the current phase."""
        if not self.board_data:
            return discord.Embed(title="Jeopardy Board", description="Error loading game data.", color=discord.Color.red())

        embed = discord.Embed(
            title="Jeopardy Board",
            description=f"Player: **{self.player.display_name}** | Score: **${self.score}**\n\n",
            color=discord.Color.gold()
        )

        categories_to_display = []
        if self.game_phase == "NORMAL_JEOPARDY_SELECTION":
            embed.description += "__**Jeopardy!**__\nSelect a category and value from the dropdowns below, then click 'Pick Question'!"
            categories_to_display = self.board_data.get("normal_jeopardy", [])
        elif self.game_phase == "DOUBLE_JEOPARDY_SELECTION":
            embed.description += "__**Double Jeopardy!**__\nSelect a category and value from the dropdowns below, then click 'Pick Question'!"
            categories_to_display = self.board_data.get("double_jeopardy", [])
        elif self.game_phase in ["FINAL_JEOPARDY_WAGER", "FINAL_JEOPARDY_ACTIVE"]:
            embed.description += "__**Final Jeopardy!**__\n"
            embed.color = discord.Color.purple() # Change color for Final Jeopardy
            # No board display fields for Final Jeopardy, as the question is presented separately
            return embed 

        # Add category fields for Normal/Double Jeopardy for text-based overview
        # This is separate from the interactive dropdowns in the View.
        # Limit to 5 categories for visual consistency, as Discord embeds display 3 fields per row.
        for i, category in enumerate(categories_to_display):
            if i >= 5: # Display up to 5 categories in the embed fields
                break
            category_name = category["category"]
            questions_display = []
            for q in category["questions"]:
                if q["guessed"]:
                    questions_display.append(f"~~${q['value']}~~") # Strikethrough guessed questions
                else:
                    questions_display.append(f"${q['value']}")
            embed.add_field(name=category_name, value="\n".join(questions_display), inline=True)
        
        # Add blank fields for spacing if the number of displayed categories is not a multiple of 3
        # This helps maintain a consistent grid-like layout in Discord's embed display
        while len(embed.fields) % 3 != 0:
            embed.add_field(name="\u200b", value="\u200b", inline=True)

        return embed

    def _normalize_answer(self, answer_text: str) -> str:
        """Normalizes an answer string for comparison by lowercasing and removing common prefixes/punctuation."""
        normalized = answer_text.lower()
        prefixes = ["what is ", "who is ", "where is ", "when is ", "what are ", "who are ", "where are ", "when are "]
        for prefix in prefixes:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):]
                break

        # Remove punctuation (keep alphanumeric characters and spaces)
        normalized = ''.join(char for char in normalized if char.isalnum() or char.isspace())
        # Replace multiple spaces with a single space and strip leading/trailing whitespace
        normalized = ' '.join(normalized.split()).strip()
        return normalized

    async def present_question(self, interaction: discord.Interaction, question_data: dict):
        """Presents the question to the user and starts the timer."""
        self.current_question = question_data
        self.game_phase = "QUESTION_ACTIVE"
        
        # When a question is active, remove the interactive view (dropdowns/buttons) from the board message
        # The embed will still show the board state, but interaction is paused.
        if self.board_message:
            await self.board_message.edit(view=None)

        question_embed = discord.Embed(
            title=f"Category: {question_data['category']} for ${question_data['value']}",
            description=f"**Question:** {question_data['question']}\n\n"
                        f"You have **{question_data['seconds']}** seconds to answer. "
                        "Type your answer in the chat, starting with 'what is', 'who is', 'what are', etc.",
            color=discord.Color.blue()
        )
        # Send a new message specifically for the question, separate from the main board message
        self.question_message = await interaction.channel.send(embed=question_embed)

        # Start the countdown timer for the question
        self.timer_task = bot.loop.create_task(
            self._question_timer(interaction.channel, question_data['seconds'], question_data['value'])
        )

    async def _question_timer(self, channel: discord.TextChannel, seconds: int, value: int):
        """Manages the countdown timer for an active question."""
        try:
            await asyncio.sleep(seconds) # Wait for the specified number of seconds
            
            async with self.answer_lock: # Acquire lock to ensure no race conditions with answer handling
                if self.current_question and not self.current_question["guessed"]:
                    # If time runs out and question hasn't been guessed
                    self.current_question["guessed"] = True
                    self.score -= value # Deduct points for incorrect/no answer
                    await channel.send(
                        f"Time's up! The correct answer was **{self.current_question['answer']}**. "
                        f"You lost ${value}. Your score is now **${self.score}**."
                    )
                    self.current_question = None # Clear the currently active question
                    
                    # Delete the question message after timeout
                    if self.question_message:
                        try:
                            await self.question_message.delete()
                        except discord.NotFound:
                            pass # Message might have already been deleted
                        self.question_message = None
                    
                    # Re-evaluate game phase and update the board display
                    self._check_game_progression(channel)
                    if not self.game_over: # Only update board if game is still active
                        await self.update_board_message() 
        except asyncio.CancelledError:
            pass # The timer was cancelled because the user provided an answer in time

    async def handle_answer(self, message: discord.Message, user_answer: str):
        """Handles a user's attempt to answer an active question."""
        async with self.answer_lock: # Acquire lock to ensure only one answer is processed
            if not self.current_question or self.game_phase != "QUESTION_ACTIVE":
                await message.channel.send("There is no active question right now or it's not the answering phase.", ephemeral=True)
                return

            if message.author.id != self.player.id:
                await message.channel.send("You are not the active player for this Jeopardy game.", ephemeral=True)
                return

            correct_answer = self.current_question["answer"]
            value = self.current_question["value"]
            
            normalized_user_answer = self._normalize_answer(user_answer)
            normalized_correct_answer = self._normalize_answer(correct_answer)

            if normalized_user_answer == normalized_correct_answer:
                self.score += value
                response_content = f"That's correct! You gained ${value}. Your score is now **${self.score}**."
            else:
                self.score -= value
                response_content = (
                    f"That's incorrect. The correct answer was **{correct_answer}**. "
                    f"You lost ${value}. Your score is now **${self.score}**."
                )
            
            self.current_question["guessed"] = True # Mark the question as guessed
            self.current_question = None # Clear the active question
            
            if self.timer_task and not self.timer_task.done():
                self.timer_task.cancel() # Cancel the timer as an answer was received
            
            await message.channel.send(response_content) # Send public response
            # Delete the question message after answer
            if self.question_message:
                try:
                    await self.question_message.delete()
                except discord.NotFound:
                    pass # Message might have already been deleted
                self.question_message = None

            # Re-evaluate game phase and update board
            self._check_game_progression(message.channel) 
            if not self.game_over: # Only update board if game is still active
                await self.update_board_message()

    async def update_board_message(self):
        """Updates the main board message with the current state and interactive components."""
        if self.board_message:
            new_view = JeopardyGameView(self)
            new_view.add_buttons_from_board() # Rebuild the view with updated dropdowns/buttons
            await self.board_message.edit(embed=self._get_board_display_embed(), view=new_view)

    def _are_all_questions_guessed_in_category_type(self, category_type: str) -> bool:
        """Checks if all questions in a given category type (e.g., 'normal_jeopardy') have been guessed."""
        if category_type not in self.board_data:
            return True # If the category type doesn't exist in the data, consider it "guessed"
        
        for category in self.board_data[category_type]:
            for q in category["questions"]:
                if not q["guessed"]:
                    return False # Found an unguessed question
        return True # All questions in this category type are guessed

    def _check_game_progression(self, channel: discord.TextChannel):
        """Checks if the game should transition to the next phase (Double Jeopardy, Final Jeopardy, or end)."""
        if self.game_phase == "NORMAL_JEOPARDY_SELECTION" and \
           self._are_all_questions_guessed_in_category_type("normal_jeopardy"):
            
            self.game_phase = "DOUBLE_JEOPARDY_SELECTION"
            bot.loop.create_task(channel.send("__**All Normal Jeopardy questions guessed! Moving to Double Jeopardy!**__"))
            bot.loop.create_task(self.update_board_message()) # Update board for Double Jeopardy
            return

        if self.game_phase == "DOUBLE_JEOPARDY_SELECTION" and \
           self._are_all_questions_guessed_in_category_type("double_jeopardy"):
            
            self.game_phase = "FINAL_JEOPARDY_WAGER"
            bot.loop.create_task(channel.send("__**All Double Jeopardy questions guessed! Prepare for Final Jeopardy!**__"))
            bot.loop.create_task(self.start_final_jeopardy(channel)) # Start Final Jeopardy sequence
            return

        # If all normal and double jeopardy questions are guessed, and Final Jeopardy exists and hasn't been guessed
        # This condition is primarily for ensuring Final Jeopardy starts if the previous transitions somehow missed it.
        if self._are_all_questions_guessed_in_category_type("normal_jeopardy") and \
           self._are_all_questions_guessed_in_category_type("double_jeopardy") and \
           "final_jeopardy" in self.board_data and \
           not self.board_data["final_jeopardy"]["guessed"]:
            # This case is primarily handled by the transition to FINAL_JEOPARDY_WAGER and then ACTIVE
            pass # Do nothing, as the transition should have already been triggered.
        
        # If all questions (including Final Jeopardy if present) are guessed, and game is not already over
        if self._are_all_questions_guessed_in_category_type("normal_jeopardy") and \
           self._are_all_questions_guessed_in_category_type("double_jeopardy") and \
           ("final_jeopardy" not in self.board_data or self.board_data["final_jeopardy"]["guessed"]) and \
           not self.game_over:
            
            self.game_over = True # Mark game as over
            bot.loop.create_task(channel.send(
                f"The Jeopardy game has ended! Your final score is **${self.score}**."
            ))
            if self.channel_id in active_jeopardy_games:
                del active_jeopardy_games[self.channel_id] # Remove game from active list
            if self.board_message:
                bot.loop.create_task(self.board_message.edit(view=None)) # Remove components from board message

    async def start_final_jeopardy(self, channel: discord.TextChannel):
        """Initiates the Final Jeopardy round."""
        final_jeopardy_data = self.board_data.get("final_jeopardy")
        if not final_jeopardy_data or final_jeopardy_data["guessed"]:
            await channel.send("Final Jeopardy is not available or already played.")
            self.game_over = True
            if self.channel_id in active_jeopardy_games:
                del active_jeopardy_games[self.channel_id]
            return

        self.current_question = final_jeopardy_data
        self.game_phase = "FINAL_JEOPARDY_WAGER"
        
        # Prompt the player to enter their wager for Final Jeopardy
        await channel.send(
            f"It's **Final Jeopardy!** Your current score is **${self.score}**. "
            "Please enter your wager using `/jeopardy_wager amount:VALUE`."
            f"You can wager up to ${max(self.score, 0)}."
        )

    async def handle_wager(self, interaction: discord.Interaction, wager: int):
        """Handles the user's wager for Final Jeopardy."""
        if self.game_phase != "FINAL_JEOPARDY_WAGER":
            await interaction.response.send_message("It's not time to wager for Final Jeopardy.", ephemeral=True)
            return

        if interaction.user.id != self.player.id:
            await interaction.response.send_message("You are not the active player for this Jeopardy game.", ephemeral=True)
            return

        max_wager = max(self.score, 0)
        if not (0 <= wager <= max_wager):
            await interaction.response.send_message(
                f"Invalid wager. You must wager between $0 and ${max_wager}.",
                ephemeral=True
            )
            return

        self.final_jeopardy_wager = wager
        await interaction.response.send_message(f"You have wagered **${wager}** for Final Jeopardy.", ephemeral=True)

        self.game_phase = "FINAL_JEOPARDY_ACTIVE"
        # Now present the Final Jeopardy question after the wager is placed
        question_embed = discord.Embed(
            title=f"Final Jeopardy: {self.current_question['category']}",
            description=f"**Question:** {self.current_question['question']}\n\n"
                        f"You have **{self.current_question['seconds']}** seconds to answer. "
                        "Type your answer in the chat, starting with 'what is', 'who is', 'what are', etc.",
            color=discord.Color.purple()
        )
        self.question_message = await interaction.channel.send(embed=question_embed)

        # Start the Final Jeopardy timer
        self.timer_task = bot.loop.create_task(
            self._final_jeopardy_timer(interaction.channel, self.current_question['seconds'])
        )

    async def _final_jeopardy_timer(self, channel: discord.TextChannel, seconds: int):
        """Countdown timer for Final Jeopardy."""
        try:
            await asyncio.sleep(seconds)
            
            async with self.answer_lock:
                if self.current_question and not self.current_question["guessed"]:
                    self.current_question["guessed"] = True
                    # For Final Jeopardy, score is only affected by correct/incorrect answer, not timeout
                    await channel.send(
                        f"Time's up for Final Jeopardy! The correct answer was **{self.current_question['answer']}**."
                    )
                    self.current_question = None
                    self.game_over = True
                    if self.channel_id in active_jeopardy_games:
                        del active_jeopardy_games[self.channel_id]
                    if self.board_message:
                        await self.board_message.edit(view=None) # Remove components
        except asyncio.CancelledError:
            pass # Timer was cancelled because an answer was received

    async def handle_final_jeopardy_answer(self, message: discord.Message, user_answer: str):
        """Handles user's answer for Final Jeopardy."""
        async with self.answer_lock:
            if self.game_phase != "FINAL_JEOPARDY_ACTIVE":
                await message.channel.send("It's not time to answer Final Jeopardy.", ephemeral=True)
                return

            if message.author.id != self.player.id:
                await message.channel.send("You are not the active player for this Jeopardy game.", ephemeral=True)
                return

            correct_answer = self.current_question["answer"]
            normalized_user_answer = self._normalize_answer(user_answer)
            normalized_correct_answer = self._normalize_answer(correct_answer)

            if normalized_user_answer == normalized_correct_answer:
                self.score += self.final_jeopardy_wager
                response_content = (
                    f"That's correct! You added your wager of ${self.final_jeopardy_wager}. "
                    f"Your final score is **${self.score}**."
                )
            else:
                self.score -= self.final_jeopardy_wager
                response_content = (
                    f"That's incorrect. The correct answer was **{correct_answer}**. "
                    f"You lost your wager of ${self.final_jeopardy_wager}. "
                    f"Your final score is **${self.score}**."
                )
            
            self.current_question["guessed"] = True
            self.current_question = None
            
            if self.timer_task and not self.timer_task.done():
                self.timer_task.cancel()
            
            await message.channel.send(response_content)
            
            if self.question_message:
                try:
                    await self.question_message.delete()
                except discord.NotFound:
                    pass
                self.question_message = None

            self.game_over = True # Game ends after Final Jeopardy answer
            if self.channel_id in active_jeopardy_games:
                del active_jeopardy_games[self.channel_id]
            if self.board_message:
                await self.board_message.edit(view=None) # Remove components from board


# --- Tic-Tac-Toe Game Classes (unchanged from previous version) ---

class TicTacToeButton(discord.ui.Button):
    """Represents a single square on the Tic-Tac-Toe board."""
    def __init__(self, row: int, col: int, player_mark: str = "⬜"):
        super().__init__(style=discord.ButtonStyle.secondary, label=player_mark, row=row)
        self.row = row
        self.col = col
        self.player_mark = player_mark # This will be '⬜', 'X', or 'O'

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
                content="It's a **draw!** �",
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
                # Pass "⬜" as the initial label for the button
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
                    board_str += "⬜ " # White square
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
            elif view._check_draw():
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
            await self.message.edit(content="Game timed out due to inactivity.", view=None, embed=None)
        if self.message and self.message.channel.id in active_tictactoe_games:
            del active_tictactoe_games[self.message.channel.id]
        print(f"Tic-Tac-Toe game in channel {self.message.channel.id} timed out.")


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

    # Check if there's an active Jeopardy game in this channel
    if message.channel.id in active_jeopardy_games:
        game = active_jeopardy_games[message.channel.id]
        # Check if there's a question active and it's the player's turn to answer
        if game.current_question and message.author.id == game.player.id:
            # Check if the message starts with a valid Jeopardy answer prefix
            content_lower = message.content.lower()
            if game.game_phase == "QUESTION_ACTIVE" and \
               content_lower.startswith(("what is", "who is", "what are", "who are", "where is", "where are", "when is", "when are")):
                await game.handle_answer(message, message.content)
            elif game.game_phase == "FINAL_JEOPARDY_ACTIVE" and \
                 content_lower.startswith(("what is", "who is", "what are", "who are", "where is", "where are", "when is", "when are")):
                await game.handle_final_jeopardy_answer(message, message.content)
    
    # Process other commands normally
    await bot.process_commands(message)


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
    else: # Simplified to avoid complex CVC rule that caused "weatherred"
        return verb + 'ed'


# --- MODIFIED /serene_story command (MODIFIED to use Gemini API and PHP JSON output) ---
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
        - "rocketed right into their face—so hard that they [verb_past_tense]"
        - "crossed over the great divide, gave Jesus a high five, and flew back down with such velocity, that they [verb_past_tense]"
        - "told such a bad joke that they [verb_past_tense]"
        - "whispered so quietly that they [verb_past_tense]"
        - "pissed so loudly that they [verb_past_tense]"
        - "took a cock so big that they [verb_past_tense]"
        - "put their thing down, flipped it, and reversed it so perfectly, that they [verb_past_tense]"
        - "waffle-spanked a vagrant so hard that they [verb_past_tense]"
        - "kissed Crizz P. so fast that he [verb_past_tense]"
        - "ate a dong so long that they [verb_past_tense]"
        - "spun around so fast that they [verb_past_tense]"
        "vomitted so loudly that they [verb_past_tense]"
        "sand-blasted out a power-shart so strong, that they [verb_past_tense]"
        "slipped off the roof above—and with a thump—they [verb_past_tense]"

        Avoid verbs that are passive, imply a state of being, or require complex grammatical structures (e.g., phrasal verbs that depend heavily on prepositions) to make sense in these direct contexts. Focus on verbs that are direct and complete actions.

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
        
        # Load the API key from environment variables
        # This is essential for deployment on platforms like Railway
        api_key = os.getenv('GEMINI_API_KEY')
        if api_key is None:
            print("Error: GEMINI_API_KEY environment variable not set. Gemini API calls will fail.")
            # Fallback to default words if API key is not set
            nouns = ["creature", "forest", "adventure"]
            verbs_infinitive = ["walk", "discover"]
        
        # Only attempt API call if API key is available
        if api_key:
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
                            # Convert to lowercase explicitly as a safeguard
                            nouns = [n.lower() for n in generated_words.get("nouns", ["thing", "place", "event"])]
                            verbs_infinitive = [v.lower() for v in generated_words.get("verbs", ["do", "happen"])]
                            
                            # Ensure we have exactly 3 nouns and 2 verbs, using fallbacks if needed
                            nouns = (nouns + ["thing", "place", "event"])[:3]
                            verbs_infinitive = (verbs_infinitive + ["do", "happen"])[:2] 

                        else:
                            print("Warning: Gemini response structure unexpected. Using fallback words.")

                    else:
                        print(f"Warning: Gemini API call failed with status {response.status}. Using fallback words.")

    except Exception as e:
        print(f"Error calling Gemini API: {e}. Using fallback words.")

    # Conjugate verbs based on PHP's requirements (obtained from php_story_structure)
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


# --- MODIFIED /serene_game command ---
@bot.tree.command(name="serene_game", description="Start a fun game with Serene!")
@app_commands.choices(game_type=[ # This decorator should come first for the parameter
    app_commands.Choice(name="Tic-Tac-Toe", value="tic_tac_toe"),
    app_commands.Choice(name="Jeopardy", value="jeopardy"), # Added Jeopardy choice
])
@app_commands.describe(game_type="The type of game to play.") # Then this one
async def serene_game_command(interaction: discord.Interaction, game_type: str):
    """
    Handles the /serene_game slash command.
    Starts the selected game directly.
    """
    await interaction.response.defer(ephemeral=True) # Acknowledge privately

    if game_type == "tic_tac_toe":
        # Check if a game is already active in this channel
        if interaction.channel.id in active_tictactoe_games:
            await interaction.followup.send(
                "A Tic-Tac-Toe game is already active in this channel! Please finish it or wait.",
                ephemeral=True
            )
            return

        player1 = interaction.user
        player2 = bot.user # Bot plays as the second player

        # Send initial private message (now part of followup)
        await interaction.followup.send(
            f"Starting Tic-Tac-Toe for {player1.display_name} vs. {player2.display_name}...",
            ephemeral=True
        )

        game_view = TicTacToeView(player_x=player1, player_o=player2)
        
        # Send the public game board message
        game_message = await interaction.channel.send(
            content=f"It's **{player1.display_name}**'s turn (X)",
            embed=game_view._start_game_message(),
            view=game_view
        )
        game_view.message = game_message # Store the message for later updates
        active_tictactoe_games[interaction.channel.id] = game_view # Store active game

    elif game_type == "jeopardy":
        if interaction.channel.id in active_jeopardy_games:
            await interaction.followup.send(
                "A Jeopardy game is already active in this channel! Please finish it or wait.",
                ephemeral=True
            )
            return
        
        await interaction.followup.send("Starting Jeopardy game...", ephemeral=True)
        
        jeopardy_game = JeopardyGame(interaction.channel.id, interaction.user)
        await jeopardy_game.load_board_data()

        if jeopardy_game.board_data and jeopardy_game.game_phase != "GAME_OVER":
            active_jeopardy_games[interaction.channel.id] = jeopardy_game
            # Send the initial Jeopardy board with dropdowns and a pick button
            jeopardy_view = JeopardyGameView(jeopardy_game)
            jeopardy_view.add_buttons_from_board()
            game_message = await interaction.channel.send(embed=jeopardy_game._get_board_display_embed(), view=jeopardy_view)
            jeopardy_game.board_message = game_message # Store the message for updates
        else:
            await interaction.followup.send(
                "Failed to load Jeopardy game data or game is not ready. Please try again later.",
                ephemeral=True
            )
            return
    else:
        await interaction.followup.send(
            f"Game type '{game_type}' is not yet implemented. Stay tuned!",
            ephemeral=True
        )

# --- REMOVED /jeopardy_select command (now handled by dropdowns and pick button) ---

# --- NEW /jeopardy_wager command (for Final Jeopardy) ---
@bot.tree.command(name="jeopardy_wager", description="Place your wager for Final Jeopardy.")
@app_commands.describe(amount="The amount to wager.")
async def jeopardy_wager_command(interaction: discord.Interaction, amount: int):
    """
    Allows a user to place their wager for Final Jeopardy.
    """
    await interaction.response.defer(ephemeral=True)

    if interaction.channel.id not in active_jeopardy_games:
        await interaction.followup.send("No Jeopardy game is active in this channel.", ephemeral=True)
        return

    game = active_jeopardy_games[interaction.channel.id]

    if interaction.user.id != game.player.id:
        await interaction.response.send_message("You are not the active player for this Jeopardy game.", ephemeral=True)
        return
    
    if game.game_phase != "FINAL_JEOPARDY_WAGER":
        await interaction.response.send_message("It's not time to wager for Final Jeopardy.", ephemeral=True)
        return

    await game.handle_wager(interaction, amount)


# Load environment variables for the token
BOT_TOKEN = os.getenv('BOT_TOKEN')

if BOT_TOKEN is None:
    print("Error: BOT_TOKEN environment variable not set.")
else:
    bot.run(BOT_TOKEN)
�
