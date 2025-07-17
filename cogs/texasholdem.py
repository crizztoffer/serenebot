# cogs/texasholdem.py
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

# Import active game states from bot.py
from bot import active_texasholdem_games

class TexasHoldEm(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # --- New Texas Hold 'em Game UI Components ---

    class TexasHoldEmGameView(discord.ui.View):
        """
        The Discord UI View that holds the interactive Texas Hold 'em game buttons.
        """
        def __init__(self, game: 'TexasHoldEm.TexasHoldEmGame'): # Corrected type hint
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
        """
        Represents a single Texas Hold 'em game instance.
        Manages game state, player hands, and community cards.
        """
        def __init__(self, channel_id: int, player: discord.User):
            self.channel_id = channel_id
            self.player = player
            self.bot_player = None # Will be set by GamesMain cog via bot.user
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

                title = f"{rank_titles.get(rank_code, rank_code)} of {suit_titles.get(suit_code, suit_code)}"
                card_number = ranks.get(rank_code, 0)

                deck.append({
                    "title": title,
                    "cardNumber": card_number,
                    "code": card_code
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
                print(f"ERROR: An unexpected error occurred fetching Texas Hold 'em image: {e}. URL: {full_game_image_url}")
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

            # Set the bot_player here, as the bot instance is now available
            self.bot_player = interaction.client.user # interaction.client is the bot instance

            game_view = TexasHoldEm.TexasHoldEmGameView(game=self) # Refer to TexasHoldEmGameView
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

async def setup(bot):
    await bot.add_cog(TexasHoldEm(bot))

