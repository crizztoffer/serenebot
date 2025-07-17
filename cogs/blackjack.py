# cogs/blackjack.py
import os
import random
import urllib.parse
import json
import asyncio
import time

import discord
from discord.ext import commands
from discord import app_commands, ui
import aiohttp

# Import active game states and database functions from bot.py
from bot import active_blackjack_games, update_user_kekchipz

class Blackjack(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # --- New Blackjack Game UI Components ---

    class BlackjackGameView(discord.ui.View):
        """
        The Discord UI View that holds the interactive Blackjack game buttons.
        """
        def __init__(self, game: 'Blackjack.BlackjackGame'): # Corrected type hint
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
        """
        Represents a single Blackjack game instance.
        Manages game state, player and Serene hands, and card deck.
        """
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

                title = f"{rank_titles.get(rank_code, rank_code)} of {suit_titles.get(suit_code, suit_code)}"
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
            
            game_view = Blackjack.BlackjackGameView(game=self) # Refer to BlackjackGameView
            
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

async def setup(bot):
    await bot.add_cog(Blackjack(bot))
