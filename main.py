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

class JeopardyGame:
    """Manages the state and logic for a single Jeopardy game."""
    def __init__(self, channel_id: int, player: discord.User):
        self.channel_id = channel_id
        self.player = player
        self.score = 0
        self.board_data = None # Will store the fetched JSON data
        self.current_question = None # Stores the question currently being answered
        self.question_message = None # Stores the Discord message for the current question
        self.timer_task = None # asyncio task for the countdown timer
        self.answer_lock = asyncio.Lock() # To prevent multiple answers at once
        self.game_over = False

        # Fetch Jeopardy data from the PHP backend
        # In a real application, you might cache this or handle errors more robustly
        self.jeopardy_data_url = "https://serenekeks.com/serene_bot_games.php"
        
    async def load_board_data(self):
        """Fetches Jeopardy questions from the backend."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.jeopardy_data_url) as response:
                    if response.status == 200:
                        self.board_data = await response.json()
                        # Initialize 'guessed' status for all questions
                        for category_type in ["normal_jeopardy", "double_jeopardy"]:
                            if category_type in self.board_data:
                                for category in self.board_data[category_type]:
                                    for question_data in category["questions"]:
                                        question_data["guessed"] = False
                        if "final_jeopardy" in self.board_data:
                            self.board_data["final_jeopardy"]["guessed"] = False
                    else:
                        print(f"Error fetching Jeopardy data: HTTP Status {response.status}")
                        self.board_data = None
        except Exception as e:
            print(f"Error loading Jeopardy data: {e}")
            self.board_data = None

    def _get_board_display_embed(self) -> discord.Embed:
        """Creates an embed to display the current Jeopardy board."""
        if not self.board_data:
            return discord.Embed(title="Jeopardy Board", description="Error loading game data.", color=discord.Color.red())

        embed = discord.Embed(
            title="Jeopardy Board",
            description=f"Player: **{self.player.display_name}** | Score: **${self.score}**\n\n"
                        "Use `/jeopardy_select category:\"Category Name\" value:Value` to pick a question.\n"
                        "Example: `/jeopardy_select category:\"PRESIDENTIAL INAUGURATIONS\" value:200`",
            color=discord.Color.gold()
        )

        # Normal Jeopardy categories
        if "normal_jeopardy" in self.board_data:
            for category in self.board_data["normal_jeopardy"]:
                category_name = category["category"]
                questions_display = []
                for q in category["questions"]:
                    if q["guessed"]:
                        questions_display.append(f"~~${q['value']}~~")
                    else:
                        questions_display.append(f"${q['value']}")
                embed.add_field(name=category_name, value="\n".join(questions_display), inline=True)
        
        # Add a blank field for spacing if needed (Discord embeds display 3 fields per row)
        # This can make the layout cleaner if you have a number of categories not divisible by 3
        if len(embed.fields) % 3 == 1:
            embed.add_field(name="\u200b", value="\u200b", inline=True)
            embed.add_field(name="\u200b", value="\u200b", inline=True)
        elif len(embed.fields) % 3 == 2:
            embed.add_field(name="\u200b", value="\u200b", inline=True)

        # Double Jeopardy categories (if any) - similar display
        if "double_jeopardy" in self.board_data:
            embed.add_field(name="\u200b\n__Double Jeopardy__", value="\u200b", inline=False) # Separator
            for category in self.board_data["double_jeopardy"]:
                category_name = category["category"]
                questions_display = []
                for q in category["questions"]:
                    if q["guessed"]:
                        questions_display.append(f"~~${q['value']}~~")
                    else:
                        questions_display.append(f"${q['value']}")
                embed.add_field(name=category_name, value="\n".join(questions_display), inline=True)
        
        if len(embed.fields) % 3 == 1:
            embed.add_field(name="\u200b", value="\u200b", inline=True)
            embed.add_field(name="\u200b", value="\u200b", inline=True)
        elif len(embed.fields) % 3 == 2:
            embed.add_field(name="\u200b", value="\u200b", inline=True)

        return embed

    def _find_question(self, category_name: str, value: int):
        """Finds a question by category and value, returning its data and type."""
        # Check normal jeopardy
        if "normal_jeopardy" in self.board_data:
            for category in self.board_data["normal_jeopardy"]:
                if category["category"].lower() == category_name.lower():
                    for q in category["questions"]:
                        if q["value"] == value and not q["guessed"]:
                            return q, "normal"
        
        # Check double jeopardy
        if "double_jeopardy" in self.board_data:
            for category in self.board_data["double_jeopardy"]:
                if category["category"].lower() == category_name.lower():
                    for q in category["questions"]:
                        if q["value"] == value and not q["guessed"]:
                            return q, "double"
        return None, None

    def _normalize_answer(self, answer_text: str) -> str:
        """Normalizes an answer string for comparison."""
        # Convert to lowercase
        normalized = answer_text.lower()
        # Remove common Jeopardy prefixes
        prefixes = ["what is ", "who is ", "where is ", "when is ", "what are ", "who are ", "where are ", "when are "]
        for prefix in prefixes:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):]
                break # Only remove one prefix

        # Remove punctuation (keep spaces)
        normalized = ''.join(char for char in normalized if char.isalnum() or char.isspace())
        # Remove extra spaces and strip leading/trailing whitespace
        normalized = ' '.join(normalized.split()).strip()
        return normalized

    async def present_question(self, interaction: discord.Interaction, question_data: dict):
        """Presents the question to the user and starts the timer."""
        self.current_question = question_data
        
        # Disable all buttons on the board temporarily while a question is active
        # This is important to prevent users from clicking other questions
        # while one is being answered.
        # We need to get the message the board is on and edit it.
        if self.question_message: # This refers to the main board message
            await self.question_message.edit(view=None) # Remove buttons from board

        question_embed = discord.Embed(
            title=f"Category: {question_data['category']} for ${question_data['value']}",
            description=f"**Question:** {question_data['question']}\n\n"
                        f"You have **{question_data['seconds']}** seconds to answer. "
                        "Type your answer in the chat, starting with 'what is', 'who is', 'what are', etc.",
            color=discord.Color.blue()
        )
        self.question_message = await interaction.channel.send(embed=question_embed)

        # Start the timer
        self.timer_task = bot.loop.create_task(
            self._question_timer(interaction.channel, question_data['seconds'], question_data['value'])
        )

    async def _question_timer(self, channel: discord.TextChannel, seconds: int, value: int):
        """Countdown timer for a question."""
        try:
            await asyncio.sleep(seconds)
            
            # If the answer lock is still held, it means no answer was received in time
            async with self.answer_lock:
                if self.current_question and not self.current_question["guessed"]:
                    self.current_question["guessed"] = True
                    self.score -= value
                    await channel.send(
                        f"Time's up! The correct answer was **{self.current_question['answer']}**. "
                        f"You lost ${value}. Your score is now **${self.score}**."
                    )
                    self.current_question = None # Clear current question
                    await self.update_board_message() # Refresh board display
        except asyncio.CancelledError:
            pass # Timer was cancelled because an answer was received

    async def handle_answer(self, interaction: discord.Interaction, user_answer: str):
        """Handles a user's attempt to answer a question."""
        async with self.answer_lock: # Acquire lock to ensure only one answer is processed
            if not self.current_question:
                await interaction.response.send_message("There is no active question right now.", ephemeral=True)
                return

            # Ensure it's the player who started the game answering
            if interaction.user.id != self.player.id:
                await interaction.response.send_message("You are not the active player for this Jeopardy game.", ephemeral=True)
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
            
            self.current_question["guessed"] = True # Mark question as guessed
            self.current_question = None # Clear active question
            
            # Cancel the timer if it's still running
            if self.timer_task and not self.timer_task.done():
                self.timer_task.cancel()
            
            await interaction.response.send_message(response_content, ephemeral=False) # Send public response
            await self.update_board_message() # Refresh board display

            if self._is_game_over():
                await interaction.channel.send(
                    f"The Jeopardy game has ended! Your final score is **${self.score}**."
                )
                del active_jeopardy_games[self.channel_id]
                self.game_over = True # Set game over flag

    async def update_board_message(self):
        """Updates the main board message with the current state."""
        if self.question_message: # This is the message that holds the board
            # Re-add the view with updated button states
            # We need to create a new view instance to reflect disabled buttons
            updated_view = discord.ui.View(timeout=300)
            # Iterate through all questions and add buttons for un-guessed ones
            for category_type in ["normal_jeopardy", "double_jeopardy"]:
                if category_type in self.board_data:
                    for category in self.board_data[category_type]:
                        for q in category["questions"]:
                            if not q["guessed"]:
                                # Create a dummy button to represent the category/value for selection
                                # This button won't be interactive on its own, but will be used to show availability
                                # For actual selection, we rely on the /jeopardy_select command.
                                # Discord buttons are limited to 5 rows, so we cannot represent the full board with buttons.
                                # We will rely on the embed for display and slash commands for selection.
                                pass # No buttons for selection on the main board message

            # The board is primarily displayed via embed, not interactive buttons for selection.
            # So, we just update the embed.
            await self.question_message.edit(embed=self._get_board_display_embed(), view=None) # No view for board

    def _is_game_over(self) -> bool:
        """Checks if all questions have been guessed."""
        for category_type in ["normal_jeopardy", "double_jeopardy"]:
            if category_type in self.board_data:
                for category in self.board_data[category_type]:
                    for q in category["questions"]:
                        if not q["guessed"]:
                            return False # Found an unguessed question
        # If all normal and double jeopardy questions are guessed, then it's Final Jeopardy or game over
        if "final_jeopardy" in self.board_data and not self.board_data["final_jeopardy"]["guessed"]:
            # Game is not over, it's time for Final Jeopardy
            return False
        return True # All questions (including Final Jeopardy if present) are guessed

    async def start_final_jeopardy(self, interaction: discord.Interaction):
        """Initiates the Final Jeopardy round."""
        final_jeopardy_data = self.board_data.get("final_jeopardy")
        if not final_jeopardy_data or final_jeopardy_data["guessed"]:
            await interaction.channel.send("Final Jeopardy is not available or already played.")
            return

        self.current_question = final_jeopardy_data
        
        # Prompt for wager
        wager_message = await interaction.channel.send(
            f"It's **Final Jeopardy!** Your current score is **${self.score}**. "
            "Please enter your wager using `/jeopardy_wager amount:VALUE`."
            f"You can wager up to ${max(self.score, 0)}."
        )
        # Store wager message to edit/delete later if needed
        self.wager_message = wager_message

    async def handle_wager(self, interaction: discord.Interaction, wager: int):
        """Handles the user's wager for Final Jeopardy."""
        if not self.current_question or self.current_question.get("category") != "RIVERS": # Assuming "RIVERS" is Final Jeopardy category
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

        # Now present the Final Jeopardy question
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
                    # No score change on timeout for Final Jeopardy, just reveal answer
                    await channel.send(
                        f"Time's up for Final Jeopardy! The correct answer was **{self.current_question['answer']}**."
                    )
                    self.current_question = None
                    self.game_over = True
                    del active_jeopardy_games[self.channel_id]
        except asyncio.CancelledError:
            pass # Timer was cancelled because an answer was received

    async def handle_final_jeopardy_answer(self, interaction: discord.Interaction, user_answer: str):
        """Handles user's answer for Final Jeopardy."""
        async with self.answer_lock:
            if not self.current_question or self.current_question.get("category") != "RIVERS":
                await interaction.response.send_message("It's not time to answer Final Jeopardy.", ephemeral=True)
                return

            if interaction.user.id != self.player.id:
                await interaction.response.send_message("You are not the active player for this Jeopardy game.", ephemeral=True)
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
            
            await interaction.response.send_message(response_content, ephemeral=False)
            self.game_over = True
            del active_jeopardy_games[self.channel_id]


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
            del active_tictactoe_games[interaction.channel_id] # End the game
        elif view._check_draw():
            await interaction.edit_original_response(
                content="It's a **draw!** 🤝",
                embed=view._start_game_message(),
                view=view._end_game()
            )
            del active_tictactoe_games[interaction.channel_id] # End the game
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
                del active_tictactoe_games[interaction.channel_id]
            elif self._check_draw():
                await interaction.edit_original_response(
                    content="It's a **draw!** 🤝",
                    embed=self._start_game_message(),
                    view=self._end_game()
                )
                del active_tictactoe_games[interaction.channel_id]
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
            if content_lower.startswith(("what is", "who is", "what are", "who are", "where is", "where are", "when is", "when are")):
                await game.handle_answer(message, message.content) # Pass the message object directly
            elif game.current_question.get("category") == "RIVERS" and content_lower.startswith(("what is", "who is", "what are", "who are")): # Final Jeopardy
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


# --- NEW /serene_roast command ---
@bot.tree.command(name="serene_roast", description="Get roasted by Serene!")
async def serene_roast_command(interaction: discord.Interaction):
    """
    Handles the /serene_roast slash command.
    Sends a "roast" message to the backend and displays the response.
    """
    await interaction.response.defer() # Acknowledge the interaction

    php_backend_url = "https://serenekeks.com/serene_bot.php"
    player_name = interaction.user.display_name

    text_to_send = "roast"  # Predefined text for this command
    param_name = "roast" # Parameter name for the PHP backend

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
        "sand-blast": "sand-blasted", # "sand-blasted out a power-shart"
        "slip": "slipped", # "slipped off the roof"
        "sand-blasted out a power-shart so strong, that they [verb_past_tense]"

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

        if jeopardy_game.board_data:
            active_jeopardy_games[interaction.channel.id] = jeopardy_game
            # Send the initial Jeopardy board
            game_message = await interaction.channel.send(embed=jeopardy_game._get_board_display_embed())
            jeopardy_game.question_message = game_message # Store the message for updates
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

# --- NEW /jeopardy_select command ---
@bot.tree.command(name="jeopardy_select", description="Select a Jeopardy question.")
@app_commands.describe(
    category="The category name (e.g., 'PRESIDENTIAL INAUGURATIONS')",
    value="The dollar value of the question (e.g., 200, 400)"
)
async def jeopardy_select_command(interaction: discord.Interaction, category: str, value: int):
    """
    Allows a user to select a Jeopardy question from the board.
    """
    await interaction.response.defer(ephemeral=True)

    if interaction.channel.id not in active_jeopardy_games:
        await interaction.followup.send("No Jeopardy game is active in this channel.", ephemeral=True)
        return

    game = active_jeopardy_games[interaction.channel.id]

    if interaction.user.id != game.player.id:
        await interaction.followup.send("You are not the active player for this Jeopardy game.", ephemeral=True)
        return
    
    if game.current_question:
        await interaction.followup.send("A question is already active. Please answer it first!", ephemeral=True)
        return

    question_data, q_type = game._find_question(category, value)

    if question_data and not question_data["guessed"]:
        question_data["category"] = category # Add category to question data for display
        await interaction.followup.send(f"You selected: **{category} for ${value}**", ephemeral=True)
        await game.present_question(interaction, question_data)
    else:
        await interaction.followup.send(
            f"Question '{category}' for ${value} not found or already guessed. Please check the board.",
            ephemeral=True
        )

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
        await interaction.followup.send("You are not the active player for this Jeopardy game.", ephemeral=True)
        return
    
    # Check if it's actually Final Jeopardy time
    if not game._is_game_over() or not game.board_data.get("final_jeopardy") or game.board_data["final_jeopardy"]["guessed"]:
        await interaction.followup.send("It's not time for Final Jeopardy or it has already been played.", ephemeral=True)
        return

    # Assuming Final Jeopardy is the only remaining step if _is_game_over() returns True without all questions being guessed
    # This logic needs refinement to explicitly transition to Final Jeopardy.
    # For now, we'll assume this command is called when it's the right time.
    await game.handle_wager(interaction, amount)


# Load environment variables for the token
BOT_TOKEN = os.getenv('BOT_TOKEN')

if BOT_TOKEN is None:
    print("Error: BOT_TOKEN environment variable not set.")
else:
    bot.run(BOT_TOKEN)
