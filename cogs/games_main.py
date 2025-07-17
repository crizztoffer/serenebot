# cogs/games_main.py
import discord
from discord.ext import commands
from discord import app_commands

# Import active game states from bot.py
from bot import (
    active_tictactoe_games,
    active_jeopardy_games,
    active_blackjack_games,
    active_texasholdem_games,
    serene_group # Import serene_group here
)

# Import game classes from their respective cogs
import cogs.tictactoe
import cogs.jeopardy
import cogs.blackjack
import cogs.texasholdem

class GamesMain(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # Define the command that *will be added* to the serene_group.
    # This is now a regular method or app_commands.Command object.
    @app_commands.choices(game_type=[
        app_commands.Choice(name="Tic-Tac-Toe", value="tic_tac_toe"),
        app_commands.Choice(name="Jeopardy", value="jeopardy"),
        app_commands.Choice(name="Blackjack", value="blackjack"),
        app_commands.Choice(name="Texas Hold 'em", value="texas_hold_em"),
    ])
    @app_commands.describe(game_type="The type of game to play.")
    async def game_command_impl(self, interaction: discord.Interaction, game_type: str):
        """
        Handles the /serene game slash command.
        Starts the selected game by instantiating the relevant game class.
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
            player2 = self.bot.user # Bot's user object

            await interaction.followup.send(
                f"Starting Tic-Tac-Toe for {player1.display_name} vs. {player2.display_name}...",
                ephemeral=True
            )

            # Instantiate the TicTacToeView from the tictactoe cog
            game_view = cogs.tictactoe.TicTacToeView(player_x=player1, player_o=player2)
            game_view.channel_id = interaction.channel.id # Store channel_id for timeout cleanup

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
            
            # Instantiate the NewJeopardyGame from the jeopardy cog
            jeopardy_game = cogs.jeopardy.NewJeopardyGame(interaction.channel.id, interaction.user)
            
            success = await jeopardy_game.fetch_and_parse_jeopardy_data()

            if success:
                active_jeopardy_games[interaction.channel.id] = jeopardy_game
                
                # Instantiate the JeopardyGameView from the jeopardy cog
                jeopardy_view = cogs.jeopardy.JeopardyGameView(jeopardy_game)
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
            
            # Instantiate the BlackjackGame from the blackjack cog
            blackjack_game = cogs.blackjack.BlackjackGame(interaction.channel.id, interaction.user)
            
            await blackjack_game.start_game(interaction)

        elif game_type == "texas_hold_em":
            if interaction.channel.id in active_texasholdem_games:
                await interaction.followup.send(
                    "A Texas Hold 'em game is already active in this channel! Please finish it or wait.",
                    ephemeral=True
                )
                return
            
            await interaction.followup.send("Setting up Texas Hold 'em game...", ephemeral=True)
            
            # Instantiate the TexasHoldEmGame from the texasholdem cog
            holdem_game = cogs.texasholdem.TexasHoldEmGame(interaction.channel.id, interaction.user)
            
            await holdem_game.start_game(interaction)

        else:
            await interaction.followup.send(
                f"Game type '{game_type}' is not yet implemented. Stay tuned!",
                ephemeral=True
            )

async def setup(bot):
    cog = GamesMain(bot)
    await bot.add_cog(cog)
    # Explicitly add the game command to the serene_group
    serene_group.add_command(app_commands.Command(cog.game_command_impl, name="game", description="Start a fun game with Serene!"))

