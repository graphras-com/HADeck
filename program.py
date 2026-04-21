#!/usr/bin/env python3

from io import BytesIO
from PIL import Image
import asyncio
import logging
from pathlib import Path
from ha_client import HAClient,NowPlaying
from deckboard import Deck, DsuiCard, DsuiKey, load_package
import os
from dotenv import load_dotenv
import requests

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

def _load_package(package_name):
    spec = load_package(Path(__file__).parent / package_name)
    logging.info(f"Loaded: {spec.name} (v{spec.version})")
    logging.info(f"  Bindings: {sorted(spec.bindings)}")
    logging.info(f"  Events:   {[e.name for e in spec.events]}")
    logging.info(f"  Assets:   {[a for a in spec.assets]}")
    return spec

async def main():
    
    # region Load Packages
    audiocard_spec = _load_package("AudioCard.dsui")
    picturekey_spec = _load_package("PictureKey.dsui")
    # endregion

    server = os.environ["HA_URL"]
    token = os.environ["HA_TOKEN"]

    async with HAClient(server, token=token) as ha, Deck(brightness=60) as deck:
        screen = deck.screen("main")

        if screen.touch_strip is not None:
            screen.touch_strip.background_color = "#1c1c1c"

        player = ha.media_player("study")
        await player.async_refresh()

        favs = await player.favorites()
        favkeys = [0, 1, 2, 4, 5, 6]

        category_order = {"Radio": 0, "Playlists": 1, "Albums": 2}
        favs = sorted(
            favs,
            key=lambda f: (category_order.get(f.category or "", 99), f.title or ""),
        )

        for key_index, fav in enumerate(favs):
            if key_index >= len(favkeys):
                break
            f = DsuiKey(picturekey_spec)
            # Leave Label Empty
            f.set("label", "")
            if fav.thumbnail is not None:
                thumbnail = Image.open(BytesIO(requests.get(fav.thumbnail).content))
                f.set("picture", thumbnail)

            @f.on_event("click")
            async def f_click(media_item=fav.media_content_id):
                logging.info(f"Play: {media_item}")

            screen.set_key(favkeys[key_index], f)

        # region AudioCard
        audio = DsuiCard(audiocard_spec)

        async def update_playing_media(media:NowPlaying):
            picture = None
            if media.entity_picture is not None:
                picture_url = ha.base_url + media.entity_picture
                picture = Image.open(BytesIO(requests.get(picture_url).content))
            audio.set_many(artist=media.artist, title=media.title, album=media.album, cover=picture)
            await deck.refresh()

        await update_playing_media(player.now_playing)

        audio.set("state", "Playing" if player.is_playing else "Paused")

        # Volume
        volume = player.volume_level or 0.0
        audio.set("volume", volume)

        if player.is_muted:
            audio.set("value_text", "Muted")
        else:
            audio.set("value_text", f"{int(volume * 100)}%")
        
        screen.set_card(0, audio)

        @player.on_volume_change
        async def on_volume_change(old, new):
            print(old);
            print(new)
            volume = player.volume_level or 0.0
            audio.set("volume", volume)
            audio.set("value_text", f"{int(volume * 100)}%")
            await deck.refresh()

        @player.on_mute_change
        async def on_mute_change(old, new):
            if new:
                audio.set("value_text", "Muted")
            else:
                audio.set("value_text", f"{int(volume * 100)}%")
            await deck.refresh()

        @player.on_play
        async def on_play(old, new):
            audio.set("state", "Playing")
            await deck.refresh()

        @player.on_pause
        async def on_pause(old, new):
            audio.set("state", "Paused")
            await deck.refresh()

        @player.on_media_change
        async def on_media_change(old, new):
            await update_playing_media(new)
                      
        @audio.on("toggle_play_pause")
        async def on_toggle():
            await player.play_pause()

        @audio.on("volume_up")
        async def on_up():
            await deck.refresh()

        @audio.on("volume_down")
        async def on_down():
            await deck.refresh()

        @audio.on("mute_toggle")
        async def on_toggle_mute():
            new_state = not player.is_muted
            await player.mute(new_state)

        @audio.on("next")
        async def on_next():
            pass

        @audio.on("previous")
        async def on_prev():
            pass
        # endregion

        await deck.set_screen("main")
        print("Deck ready!")
        await deck.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())
