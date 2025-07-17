import discord
from discord.ext import commands
from discord import app_commands

class GeneralCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="talk", description="Have the bot say something.")
    @app_commands.describe(text="The text for the bot to say.")
    async def talk_command(self, interaction: discord.Interaction, text: str):
        """
        Handles the /talk slash command.
        """
        await interaction.response.send_message(f"You said: {text}")

    @app_commands.command(name="story", description="Generate a random story.")
    async def story_command(self, interaction: discord.Interaction):
        """
        Handles the /story slash command.
        """
        # Placeholder for your story generation logic
        await interaction.response.send_message("Once upon a time, in a land far, far away...")

    @app_commands.command(name="hail", description="Hail a user.")
    async def hail_command(self, interaction: discord.Interaction):
        """
        Handles the /hail slash command.
        """
        await interaction.response.send_message(f"All hail {interaction.user.display_name}!")

    @app_commands.command(name="roast", description="Roast a user.")
    async def roast_command(self, interaction: discord.Interaction):
        """
        Handles the /roast slash command.
        """
        # Placeholder for your roast logic
        await interaction.response.send_message(f"{interaction.user.display_name}, your wit is as sharp as a butter knife!")

async def setup(bot):
    await bot.add_cog(GeneralCog(bot))
