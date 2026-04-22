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
MEDIA_PLAYER= "entertainment"

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
        async def _click(media_item=fav.media_content_id):
            #player.play_media(media_item)
            log.info("Play: %s", media_item)

        screen.set_key(FAVORITE_KEY_SLOTS[idx], key)
# endregion

# region Audio card
class VolumeAccumulator:
    """Collect rapid volume-change ticks and flush them as one HA call.

    *delay*  – seconds to wait after the last tick before flushing.
    *max_steps* – cap on how many ticks can accumulate (positive number).
                  Prevents the user from accidentally cranking the volume.
    *step*  – volume change per tick (0-1 float, default 0.01 = 1 %).
    """

    def __init__(self, player, *, delay: float = 0.25, max_steps: int = 10, step: float = 0.01):
        self._player = player
        self._delay = delay
        self._max_steps = max_steps
        self._step = step
        self._pending: int = 0          # accumulated tick count (signed)
        self._flush_task: asyncio.Task | None = None

    def tick(self, direction: int):
        """Add +1 (up) or -1 (down). Clamps to ±max_steps."""
        self._pending = max(-self._max_steps, min(self._max_steps, self._pending + direction))

        # (Re)start the flush timer
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
        delta = steps * self._step
        current = self._player.volume_level or 0.0
        target = max(0.0, min(1.0, current + delta))
        log.info("Volume flush: %+d steps → %.0f%%", steps, target * 100)
        await self._player.set_volume(target)


class AudioCardController:
    """Manages the AudioCard DSUI widget and its HA media-player bindings."""

    def __init__(self, ha: HAClient, deck, player, audiocard_spec):
        self._ha = ha
        self._deck = deck
        self._player = player
        self._card = DsuiCard(audiocard_spec)
        self._volume_acc = VolumeAccumulator(player)
        self._bind_events()

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
            pass

        @self._card.on("previous")
        async def _prev():
            pass
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
    # endregion

    server = os.environ["HA_URL"]
    token = os.environ["HA_TOKEN"]

    manager = DeckManager(brightness=60, auto_reconnect=True)

    async with HAClient(server, token=token) as ha:
        player = ha.media_player(MEDIA_PLAYER)

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
            # endregion

            # region Load state
            async def load_state():
                """(Re)load all HA state and refresh the deck."""
                log.info("Loading Home Assistant state…")
                await ha.refresh_all()
                await setup_favorites(screen, player, picturekey_spec)
                await audio_ctrl.sync_state()

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