# cogs/tictactoe.py
import discord
from discord.ext import commands
from discord import app_commands, ui
import asyncio

# Import active game states and database functions from bot.py
from bot import active_tictactoe_games, update_user_kekchipz

class TicTacToe(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # --- Tic-Tac-Toe Game UI Components ---

    class TicTacToeButton(discord.ui.Button):
        """Represents a single square on the Tic-Tac-Toe board."""
        def __init__(self, row: int, col: int, player_mark: str = "⬜"):
            super().__init__(style=discord.ButtonStyle.secondary, label=player_mark, row=row)
            self.row = row
            self.col = col
            self.player_mark = player_mark

        async def callback(self, interaction: discord.Interaction):
            view: 'TicTacToe.TicTacToeView' = self.view # Corrected type hint
            
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

                # Use self.view.bot to access the bot instance from the button's view
                if view.players[view.current_player].id == self.view.bot.user.id:
                    await asyncio.sleep(1)
                    await view._bot_make_move(interaction)


    class TicTacToeView(discord.ui.View):
        """Manages the Tic-Tac-Toe game board and logic."""
        def __init__(self, player_x: discord.User, player_o: discord.User):
            super().__init__(timeout=300)
            self.players = {"X": player_x, "O": player_o}
            self.current_player = "X"
            self.board = [[" ", " ", " "], [" ", " ", " "], [" ", " ", " "]]
            self.message = None
            self.channel_id = None # Will be set by GamesMain cog

            self._create_board()

        def _create_board(self):
            for row in range(3):
                for col in range(3):
                    # Refer to TicTacToeButton via TicTacToe.TicTacToeButton
                    self.add_item(TicTacToe.TicTacToeButton(row, col, player_mark="⬜"))

        def _update_board_display(self):
            for item in self.children:
                if isinstance(item, TicTacToe.TicTacToeButton): # Refer to TicTacToeButton
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
                    if isinstance(item, TicTacToe.TicTacToeButton) and item.row == row and item.col == col: # Refer to TicTacToeButton
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
            
            if self.channel_id in active_tictactoe_games:
                del active_tictactoe_games[self.channel_id]
            print(f"Tic-Tac-Toe game in channel {self.channel_id} timed out.")

async def setup(bot):
    await bot.add_cog(TicTacToe(bot))
