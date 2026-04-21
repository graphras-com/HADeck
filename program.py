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
    print(f"Loaded: {spec.name} (v{spec.version})")
    print(f"  Bindings: {sorted(spec.bindings)}")
    print(f"  Events:   {[e.name for e in spec.events]}")
    print(f"  Assets:   {[a for a in spec.assets]}")
    return spec

async def main():
    
    # region Load Packages
    audiocard_spec = _load_package("AudioCard.dsui")
    lightcard_spec = _load_package("LightCard.dsui")
    iconkey_spec = _load_package("IconKey.dsui")
    picturekey_spec = _load_package("PictureKey.dsui")
    # endregion

    server = os.environ["HA_URL"]
    token = os.environ["HA_TOKEN"]

    img = Image.open(BytesIO(audiocard_spec.assets["album_art.jpeg"]))

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
                print(f"Play: {media_item}")

            screen.set_key(favkeys[key_index], f)
            print(fav)


        # region AudioCard
        muted = False
        audio = DsuiCard(audiocard_spec)

        async def update_playing_media(media:NowPlaying):
            picture = None
            if media.entity_picture is not None:
                picture_url = ha.base_url + media.entity_picture
                picture = Image.open(BytesIO(requests.get(picture_url).content))
            print(f"Album: {media.title}")
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

        """
        @audio.on("next")
        async def on_next():
            nonlocal track_index
            track_index = (track_index + 1) % len(tracks)
            artist, title, album = tracks[track_index]
            audio.set_many(artist=artist, title=title, album=album, cover=img)
            print(f"⏭ {artist} — {title}")
            await deck.refresh()

        @audio.on("previous")
        async def on_prev():
            nonlocal track_index
            track_index = (track_index - 1) % len(tracks)
            artist, title, album = tracks[track_index]
            audio.set_many(artist=artist, title=title, album=album)
            print(f"⏮ {artist} — {title}")
            await deck.refresh()
        """
        # endregion

        # region LightCard
        light = DsuiCard(lightcard_spec)

        lights_on = False

        min_kelvin = 2000
        max_kelvin = 6535
        kelvin = 2706

        min_brightness = 0
        max_brightness = 255
        brightness = 201

        def kelvin_norm() -> float:
            return (kelvin - min_kelvin) / (max_kelvin - min_kelvin)

        def brightness_norm() -> float:
            return (brightness - min_brightness) / (max_brightness - min_brightness)

        light.set("lights", lights_on)
        light.set("brightness_value_text", f"{brightness}")
        light.set("brightness", brightness_norm())

        light.set("kelvin_value_text", f"{kelvin}K")
        light.set("kelvin", kelvin_norm())

        screen.set_card(1, light)

        @light.on("toggle")
        async def on_lights_toggle():
            nonlocal lights_on
            lights_on = not lights_on

            light.set("lights", lights_on)

            print(f"Lights {'ON' if lights_on else 'OFF'}")
            await deck.refresh()

        @light.on("brightness_up")
        async def on_brightness_up():
            nonlocal brightness
            brightness = min(max_brightness, brightness + 10)
            light.set("brightness", brightness_norm())
            light.set("brightness_value_text", f"{brightness}")
            print(f"Brightness: {brightness}")
            await deck.refresh()

        @light.on("brightness_down")
        async def on_brightness_down():
            nonlocal brightness
            brightness = max(min_brightness, brightness - 10)
            light.set("brightness", brightness_norm())
            light.set("brightness_value_text", f"{brightness}")
            print(f"Brightness: {brightness}")
            await deck.refresh()

        @light.on("kelvin_up")
        async def on_kelvin_up():
            nonlocal kelvin
            kelvin = min(max_kelvin, kelvin + 500)
            light.set("kelvin", kelvin_norm())
            light.set("kelvin_value_text", f"{kelvin}K")
            print(f"Kelvin: {kelvin}K")
            await deck.refresh()

        @light.on("kelvin_down")
        async def on_kelvin_down():
            nonlocal kelvin
            kelvin = max(min_kelvin, kelvin - 500)
            light.set("kelvin", kelvin_norm())
            light.set("kelvin_value_text", f"{kelvin}K")
            print(f"Kelvin: {kelvin}K")
            await deck.refresh()

        # endregion

        # region Up and Down Keys
        """
        up = DsuiKey(iconkey_spec)
        up.set("label", "Shades")
        background_color = up.get("background")
        foreground_color = up.get("foreground")
        up.set("icon", "icon-park-outline:up-square")
        screen.set_key(3, up)

        @up.on_event("press")
        async def up_press():
            up.set("background", foreground_color)
            up.set("foreground", background_color)
            print("Up pressed")

        @up.on_event("release")
        async def up_release():
            up.set("background", background_color)            
            up.set("foreground", foreground_color)
            print("Up released")

        @up.on_event("click")
        async def up_click():
            print("Up clicked")

        down = DsuiKey(iconkey_spec)
        down.set("label", "Shades")
        down.set("icon", "icon-park-outline:down-square")
        screen.set_key(7, down)

        @down.on_event("press")
        async def down_press():
            down.set("background", foreground_color)
            down.set("foreground", background_color)
            print("Down pressed")

        @down.on_event("release")
        async def down_release():
            down.set("background", background_color)            
            down.set("foreground", foreground_color)
            print("Down released")

        @down.on_event("click")
        async def down_click():
            print("Down clicked")
        """
        # endregion

        # region Favorites Keys
        """
        fav1 = DsuiKey(picturekey_spec)
        fav1.set("label", "Arthur Olsen's Favorite Music")
        screen.set_key(0, fav1)
        fav2 = DsuiKey(picturekey_spec)
        fav2.set("label", "Greatest Hits")
        screen.set_key(1, fav2)
        fav3 = DsuiKey(picturekey_spec)
        fav3.set("label", "Kringvarp Føroya")
        screen.set_key(4, fav3)
        fav4 = DsuiKey(picturekey_spec)
        fav4.set("label", "Blues Essentials")
        screen.set_key(5, fav4)
        """
        # endregion

        await deck.set_screen("main")
        print("Deck ready!")
        await deck.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())
