#!/usr/bin/env python3
"""StreamDeck+ client for Home Assistant."""

from __future__ import annotations

import asyncio
import logging
from io import BytesIO
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from PIL import Image

from deckboard import DeckManager, DeviceInfo, DsuiCard, DsuiKey, load_package
from ha_client import HAClient, NowPlaying

import os

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

log = logging.getLogger(__name__)

PACKAGES_DIR = Path(__file__).parent
STREAMDECK_SERIAL = os.environ.get("STREAMDECK_SERIAL")
MEDIA_PLAYER = os.environ.get("MEDIA_PLAYER")
UPSTAIRS_LIGHTS = os.environ.get("UPSTAIRS_LIGHTS", "light.upstairs")

# region Helpers
async def _fetch_image(url: str) -> Image.Image | None:
    """Download an image over HTTP without blocking the event loop."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return Image.open(BytesIO(await resp.read()))
    except Exception:
        log.exception("Failed to fetch image: %s", url)
    return None

def _load_dsui(name: str):
    spec = load_package(PACKAGES_DIR / name)
    log.info("Loaded: %s (v%s)", spec.name, spec.version)
    return spec
# endregion

# region Favorites (keys)
FAVORITE_KEY_SLOTS = [0, 1, 2, 4, 5, 6]
CATEGORY_ORDER = {"Radio": 0, "Playlists": 1, "Albums": 2}

async def setup_favorites(screen, player, picturekey_spec):
    """Populate favorite-media keys on the screen."""
    favs = await player.favorites()
    favs = sorted(
        favs,
        key=lambda f: (CATEGORY_ORDER.get(f.category or "", 99), f.title or ""),
    )

    for idx, fav in enumerate(favs):
        if idx >= len(FAVORITE_KEY_SLOTS):
            break
        key = DsuiKey(picturekey_spec)
        if fav.thumbnail is not None:
            thumb = await _fetch_image(fav.thumbnail)
            if thumb is not None:
                key.set("picture", thumb)

        @key.on_event("click")
        async def _click(item=fav):
            log.info("Play: %s", item.title)
            await item.play()

        screen.set_key(FAVORITE_KEY_SLOTS[idx], key)
# endregion

# region Audio card
class DialAccumulator:
    """Debounce rapid dial/encoder ticks and flush them with a single callback.

    *callback*   – ``async def callback(steps: int)`` called once per flush
                   with the net accumulated tick count (signed).
    *delay*      – seconds to wait after the last tick before flushing.
    *max_steps*  – cap on how many ticks can accumulate (positive number).
                   Use ``max_steps=1`` to collapse any number of ticks into
                   a single +1 / -1 event (useful for next/previous).
    """

    def __init__(self, callback, *, delay: float = 0.25, max_steps: int = 10):
        self._callback = callback
        self._delay = delay
        self._max_steps = max_steps
        self._pending: int = 0
        self._flush_task: asyncio.Task | None = None

    def tick(self, direction: int):
        """Add +1 or -1. Clamps to ±max_steps."""
        self._pending = max(-self._max_steps, min(self._max_steps, self._pending + direction))
        if self._flush_task is not None:
            self._flush_task.cancel()
        self._flush_task = asyncio.create_task(self._schedule_flush())

    async def _schedule_flush(self):
        try:
            await asyncio.sleep(self._delay)
            await self._flush()
        except asyncio.CancelledError:
            pass

    async def _flush(self):
        steps = self._pending
        self._pending = 0
        if steps == 0:
            return
        await self._callback(steps)


class AudioCardController:
    """Manages the AudioCard DSUI widget and its HA media-player bindings."""

    def __init__(self, ha: HAClient, deck, player, audiocard_spec):
        self._ha = ha
        self._deck = deck
        self._player = player
        self._card = DsuiCard(audiocard_spec)
        self._volume_acc = DialAccumulator(self._flush_volume, max_steps=10)
        self._skip_acc = DialAccumulator(self._flush_skip, max_steps=1)
        self._bind_events()

    async def _flush_volume(self, steps: int):
        step = 0.01
        current = self._player.volume_level or 0.0
        target = max(0.0, min(1.0, current + steps * step))
        log.info("Volume flush: %+d steps → %.0f%%", steps, target * 100)
        await self._player.set_volume(target)

    async def _flush_skip(self, direction: int):
        if direction > 0:
            log.info("Skip flush: next")
            await self._player.next()
        else:
            log.info("Skip flush: previous")
            await self._player.previous()

    @property
    def card(self) -> DsuiCard:
        return self._card

    # region initial / reconnect state sync
    async def sync_state(self):
        """Read current player state and push it to the card."""
        player = self._player
        await player.async_refresh()

        # Now-playing artwork + metadata
        await self._update_now_playing(player.now_playing)

        # Play / pause label
        self._card.set("state", "Playing" if player.is_playing else "Paused")

        # Volume
        volume = player.volume_level or 0.0
        self._card.set("volume", volume)
        if player.is_muted:
            self._card.set("value_text", "Muted")
        else:
            self._card.set("value_text", f"{int(volume * 100)}%")

        await self._deck.refresh()
    # endregion

    # region internal helpers
    async def _update_now_playing(self, media: NowPlaying):
        picture = None
        if media.entity_picture is not None:
            picture = await _fetch_image(self._ha.base_url + media.entity_picture)
        self._card.set_many(
            artist=media.artist,
            title=media.title,
            album=media.album,
            cover=picture,
        )
    # endregion

    # region HA event handlers
    def _bind_events(self):
        player = self._player

        @player.on_volume_change
        async def _on_volume(old, new):
            vol = player.volume_level or 0.0
            self._card.set("volume", vol)
            self._card.set("value_text", f"{int(vol * 100)}%")
            await self._deck.refresh()

        @player.on_mute_change
        async def _on_mute(old, new):
            if new:
                self._card.set("value_text", "Muted")
            else:
                vol = player.volume_level or 0.0
                self._card.set("value_text", f"{int(vol * 100)}%")
            await self._deck.refresh()

        @player.on_play
        async def _on_play(old, new):
            self._card.set("state", "Playing")
            await self._deck.refresh()

        @player.on_pause
        async def _on_pause(old, new):
            self._card.set("state", "Paused")
            await self._deck.refresh()

        @player.on_media_change
        async def _on_media(old, new):
            await self._update_now_playing(new)
            await self._deck.refresh()
        #endregion

    # region card UI event handlers
    def bind_card_events(self):
        player = self._player

        @self._card.on("toggle_play_pause")
        async def _toggle():
            await player.play_pause()

        @self._card.on("volume_up")
        async def _up():
            self._volume_acc.tick(+1)

        @self._card.on("volume_down")
        async def _down():
            self._volume_acc.tick(-1)

        @self._card.on("mute_toggle")
        async def _mute():
            await player.mute(not player.is_muted)

        @self._card.on("next")
        async def _next():
            if player.now_playing.next:
                self._skip_acc.tick(+1)

        @self._card.on("previous")
        async def _prev():
            if player.now_playing.previous:
                self._skip_acc.tick(-1)
        # endregion
# endregion

# region Light card
class LightCardController:
    """Manages the LightCard DSUI widget and its HA light bindings."""

    def __init__(self, ha: HAClient, deck, light, lightcard_spec):
        self._ha = ha
        self._deck = deck
        self._light = light
        self._card = DsuiCard(lightcard_spec)
        self._brightness_acc = DialAccumulator(self._flush_brightness, max_steps=10)
        self._kelvin_acc = DialAccumulator(self._flush_kelvin, max_steps=10)
        self._bind_events()

    @property
    def card(self) -> DsuiCard:
        return self._card

    # region flush helpers
    async def _flush_brightness(self, steps: int):
        step = 0.05
        current = (self._light.brightness or 0) / 255.0
        target = max(0.0, min(1.0, current + steps * step))
        brightness = int(target * 255)
        log.info("Brightness flush: %+d steps → %d%%", steps, int(target * 100))
        await self._light.turn_on(brightness=brightness)

    async def _flush_kelvin(self, steps: int):
        step = 250
        current = self._light.kelvin or self._light.min_kelvin
        min_k = self._light.min_kelvin
        max_k = self._light.max_kelvin
        target = max(min_k, min(max_k, current + steps * step))
        log.info("Kelvin flush: %+d steps → %dK", steps, target)
        await self._light.turn_on(kelvin=int(target))
    # endregion

    # region state sync
    async def sync_state(self):
        await self._light.async_refresh()
        self._update_card_from_state()
        await self._deck.refresh()

    def _update_card_from_state(self):
        light = self._light
        self._card.set("lights", light.is_on)

        brightness = light.brightness or 0
        brightness_pct = brightness / 255.0
        self._card.set("brightness", brightness_pct)
        self._card.set("brightness_value_text", f"{int(brightness_pct * 100)}%")

        kelvin = light.kelvin or light.min_kelvin
        min_k = light.min_kelvin
        max_k = light.max_kelvin
        kelvin_range = max_k - min_k
        kelvin_pct = (kelvin - min_k) / kelvin_range if kelvin_range > 0 else 0.0
        self._card.set("kelvin", kelvin_pct)
        self._card.set("kelvin_value_text", f"{int(kelvin)}K")
    # endregion

    # region HA event handlers
    def _bind_events(self):
        light = self._light

        @light.on_turn_on
        async def _on_turn_on(old, new):
            self._update_card_from_state()
            await self._deck.refresh()

        @light.on_turn_off
        async def _on_turn_off(old, new):
            self._update_card_from_state()
            await self._deck.refresh()

        @light.on_brightness_change
        async def _on_brightness(old, new):
            self._update_card_from_state()
            await self._deck.refresh()

        @light.on_color_change
        async def _on_color(old, new):
            self._update_card_from_state()
            await self._deck.refresh()

        @light.on_kelvin_change
        async def _on_kelvin(old, new):
            self._update_card_from_state()
            await self._deck.refresh()
    # endregion

    # region card UI event handlers
    def bind_card_events(self):
        @self._card.on("toggle")
        async def _toggle():
            await self._light.toggle()

        @self._card.on("brightness_up")
        async def _brightness_up():
            self._brightness_acc.tick(+1)

        @self._card.on("brightness_down")
        async def _brightness_down():
            self._brightness_acc.tick(-1)

        @self._card.on("kelvin_up")
        async def _kelvin_up():
            self._kelvin_acc.tick(+1)

        @self._card.on("kelvin_down")
        async def _kelvin_down():
            self._kelvin_acc.tick(-1)
    # endregion
# endregion

# region Reconnection watcher
async def watch_reconnect(ha: HAClient, on_reconnected):
    """Wait for WS disconnect, then wait for reconnect, and call callback.

    The HAClient WS layer reconnects automatically and re-subscribes events,
    but entity *state* is stale until we explicitly refresh.
    """
    reconnected = asyncio.Event()

    @ha.ws.on_disconnect
    def _on_drop():
        log.warning("Home Assistant WebSocket disconnected")
        reconnected.clear()
        # Start polling for reconnection in a task
        asyncio.create_task(_wait_for_reconnect())

    async def _wait_for_reconnect():
        # Poll until the WS is connected again
        while not ha.ws.connected:
            await asyncio.sleep(1)
        log.info("Home Assistant WebSocket reconnected")
        await on_reconnected()

    # Keep this coroutine alive for the lifetime of the app
    await asyncio.Event().wait()
# endregion

# region Application
async def run():
    # region Load DSUI packages
    audiocard_spec = _load_dsui("AudioCard.dsui")
    picturekey_spec = _load_dsui("PictureKey.dsui")
    lightcard_spec = _load_dsui("LightCard.dsui")
    # endregion

    server = os.environ["HA_URL"]
    token = os.environ["HA_TOKEN"]

    manager = DeckManager(brightness=60, auto_reconnect=True)

    async with HAClient(server, token=token) as ha:
        player = ha.media_player(MEDIA_PLAYER)
        upstairs = ha.light(UPSTAIRS_LIGHTS)

        @manager.on_connect(serial=STREAMDECK_SERIAL)
        async def on_deck_connect(deck):
            log.info("Deck connected: %s", STREAMDECK_SERIAL)

            screen = deck.screen("main")
            if screen.touch_strip is not None:
                screen.touch_strip.background_color = "#1c1c1c"

            # region Build UI widgets
            audio_ctrl = AudioCardController(ha, deck, player, audiocard_spec)
            audio_ctrl.bind_card_events()
            screen.set_card(0, audio_ctrl.card)

            light_ctrl = LightCardController(ha, deck, upstairs, lightcard_spec)
            light_ctrl.bind_card_events()
            screen.set_card(1, light_ctrl.card)
            # endregion

            # region Load state
            async def load_state():
                """(Re)load all HA state and refresh the deck."""
                log.info("Loading Home Assistant state…")
                await ha.refresh_all()
                await setup_favorites(screen, player, picturekey_spec)
                await audio_ctrl.sync_state()
                await light_ctrl.sync_state()

            await load_state()
            # endregion

            # region HA reconnect watcher
            asyncio.create_task(watch_reconnect(ha, load_state))
            # endregion

            # region Activate screen
            await deck.set_screen("main")
            log.info("Deck ready!")
            # endregion

        @manager.on_disconnect
        async def on_deck_disconnect(info: DeviceInfo):
            log.warning("Deck disconnected: %s — waiting for reconnect…", info.serial)

        log.info("Waiting for StreamDeck %s…", STREAMDECK_SERIAL)
        async with manager:
            await manager.wait_closed()
# endregion

# region main
def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
# endregion