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

from deckui import DeckManager, DeviceInfo, DuiCard, DuiKey, load_package
from haclient import HAClient, NowPlaying, Timer

import os

load_dotenv()

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")

log = logging.getLogger(__name__)

PACKAGES_DIR = Path(__file__).parent
STREAMDECK_SERIAL = os.environ.get("STREAMDECK_SERIAL")
MEDIA_PLAYER = os.environ.get("MEDIA_PLAYER")
UPSTAIRS_LIGHTS = os.environ.get("UPSTAIRS_LIGHTS", "light.upstairs")
TIMER_ENTITY = os.environ.get("TIMER_ENTITY", "timer.timer")

async def _fetch_image(url: str) -> Image.Image | None:
    """Download an image over HTTP without blocking the event loop."""
    log.debug("_fetch_image: %s", url)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return Image.open(BytesIO(await resp.read()))
    except Exception:
        log.exception("Failed to fetch image: %s", url)
    return None

SCENES = [
    { "position": 2, "label": "Normal", "icon": "fa-regular:smile-beam" },
    { "position": 3, "label": "Tired", "icon": "fa-regular:tired" },
    { "position": 6, "label": "Cinema", "icon": "mdi:cinema" },
    { "position": 7, "label": "Bedtime", "icon": "icon-park-outline:sleep-two" }
]

async def setup_scenes(screen, iconkey_spec):
    """Populate scenes keys on the screen."""
    log.debug("setup_scenes: populating %d scenes", len(SCENES))
    for scene in SCENES:
        key = DuiKey(iconkey_spec)
        key.set("icon", scene["icon"])
        key.set("label", scene["label"])

        @key.on_event("click")
        async def _click(item=scene):
            log.info("Activate: %s", item["label"])

        screen.set_key(scene["position"], key)

FAVORITE_KEY_SLOTS = [0, 1, 4, 5]
CATEGORY_ORDER = {"Radio": 0, "Playlists": 1, "Albums": 2}

async def setup_favorites(screen, player, picturekey_spec) -> list[DuiKey]:
    """Populate favorite-media keys on the screen. Returns the created keys."""
    log.debug("setup_favorites: fetching favorites")
    favs = await player.favorites()
    favs = sorted(
        favs,
        key=lambda f: (CATEGORY_ORDER.get(f.category or "", 99), f.title or ""),
    )

    keys: list[DuiKey] = []
    for idx, fav in enumerate(favs):
        if idx >= len(FAVORITE_KEY_SLOTS):
            break
        key = DuiKey(picturekey_spec)
        if fav.thumbnail is not None:
            thumb = await _fetch_image(fav.thumbnail)
            if thumb is not None:
                key.set("picture", thumb)

        @key.on_event("click")
        async def _click(item=fav, emitter=key):
            log.info("Play: %s", item.title)
            await emitter.start_busy()
            await item.play()

        screen.set_key(FAVORITE_KEY_SLOTS[idx], key)
        keys.append(key)
    return keys

class AudioCardController:
    """Manages the AudioCard DUI widget and its HA media-player bindings."""

    def __init__(self, ha: HAClient, deck, player, audiocard_spec):
        log.debug("AudioCardController.__init__")
        self._ha = ha
        self._deck = deck
        self._player = player
        self._card = DuiCard(audiocard_spec)
        self._on_state_callbacks: list = []
        self._bind_events()

    def on_any_state(self, callback):
        """Register a callback to be invoked on any player state change."""
        log.debug("AudioCardController.on_any_state: registering callback")
        self._on_state_callbacks.append(callback)

    async def _fire_state_callbacks(self):
        log.debug("AudioCardController._fire_state_callbacks: %d callbacks", len(self._on_state_callbacks))
        for cb in self._on_state_callbacks:
            await cb()

    @property
    def card(self) -> DuiCard:
        return self._card

    async def sync_state(self):
        """Read current player state and push it to the card."""
        log.debug("AudioCardController.sync_state")
        player = self._player
        await player.async_refresh()

        await self._update_now_playing(player.now_playing)

        self._card.set("state", "Playing" if player.is_playing else "Paused")

        volume = player.volume_level or 0.0
        self._card.set("volume", volume)
        if player.is_muted:
            self._card.set("value_text", "Muted")
        else:
            self._card.set("value_text", f"{int(volume * 100)}%")

        await self._deck.refresh()

    async def _update_now_playing(self, media: NowPlaying):
        log.debug("AudioCardController._update_now_playing: %s - %s", media.artist, media.title)
        picture = None
        if media.entity_picture is not None:
            picture = await _fetch_image(self._ha.base_url + media.entity_picture)
        self._card.set_many(
            artist=media.artist,
            title=media.title,
            album=media.album,
            cover=picture,
        )

    def _bind_events(self):
        player = self._player
        log.debug("AudioCardController._bind_events")

        @player.on_volume_change
        async def _on_volume(old, new):
            log.debug("AudioCardController._on_volume: %s → %s", old, new)
            vol = player.volume_level or 0.0
            self._card.set("volume", vol)
            self._card.set("value_text", f"{int(vol * 100)}%")
            await self._fire_state_callbacks()
            await self._deck.refresh()

        @player.on_mute_change
        async def _on_mute(old, new):
            log.debug("AudioCardController._on_mute: %s → %s", old, new)
            if new:
                self._card.set("value_text", "Muted")
            else:
                vol = player.volume_level or 0.0
                self._card.set("value_text", f"{int(vol * 100)}%")
            await self._fire_state_callbacks()
            await self._deck.refresh()

        @player.on_play
        async def _on_play(old, new):
            log.debug("AudioCardController._on_play")
            self._card.set("state", "Playing")
            await self._fire_state_callbacks()
            await self._deck.refresh()

        @player.on_pause
        async def _on_pause(old, new):
            log.debug("AudioCardController._on_pause")
            self._card.set("state", "Paused")
            await self._fire_state_callbacks()
            await self._deck.refresh()

        @player.on_media_change
        async def _on_media(old, new):
            log.debug("AudioCardController._on_media: %s", new)
            await self._update_now_playing(new)
            await self._fire_state_callbacks()
            await self._deck.refresh()

    def bind_card_events(self, encoder):
        player = self._player
        log.debug("AudioCardController.bind_card_events")

        @self._card.on("toggle_play_pause")
        async def _toggle():
            log.debug("AudioCardController: toggle_play_pause")
            await player.play_pause()

        @encoder.on_turn_accumulated
        async def _volume(steps: int):
            step = 0.01
            current = player.volume_level or 0.0
            target = max(0.0, min(1.0, current + steps * step))
            log.info("Volume: %+d steps → %.0f%%", steps, target * 100)
            await player.set_volume(target)

        @self._card.on("mute_toggle")
        async def _mute():
            log.debug("AudioCardController: mute_toggle")
            await player.mute(not player.is_muted)

        @encoder.on_press_turn_accumulated(max_steps=1)
        async def _skip(steps: int):
            if steps > 0:
                log.info("Skip: next")
                await player.next()
            else:
                log.info("Skip: previous")
                await player.previous()

class LightCardController:
    """Manages the LightCard DUI widget and its HA light bindings."""

    def __init__(self, ha: HAClient, deck, light, lightcard_spec):
        log.debug("LightCardController.__init__")
        self._ha = ha
        self._deck = deck
        self._light = light
        self._card = DuiCard(lightcard_spec)
        self._bind_events()

    @property
    def card(self) -> DuiCard:
        return self._card

    async def sync_state(self):
        log.debug("LightCardController.sync_state")
        await self._light.async_refresh()
        self._update_card_from_state()
        await self._deck.refresh()

    def _update_card_from_state(self):
        log.debug("LightCardController._update_card_from_state")
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

    def _bind_events(self):
        light = self._light
        log.debug("LightCardController._bind_events")

        @light.on_turn_on
        async def _on_turn_on(old, new):
            log.debug("LightCardController._on_turn_on")
            self._update_card_from_state()
            await self._deck.refresh()

        @light.on_turn_off
        async def _on_turn_off(old, new):
            log.debug("LightCardController._on_turn_off")
            self._update_card_from_state()
            await self._deck.refresh()

        @light.on_brightness_change
        async def _on_brightness(old, new):
            log.debug("LightCardController._on_brightness: %s → %s", old, new)
            self._update_card_from_state()
            await self._deck.refresh()

        @light.on_color_change
        async def _on_color(old, new):
            log.debug("LightCardController._on_color: %s → %s", old, new)
            self._update_card_from_state()
            await self._deck.refresh()

        @light.on_kelvin_change
        async def _on_kelvin(old, new):
            log.debug("LightCardController._on_kelvin: %s → %s", old, new)
            self._update_card_from_state()
            await self._deck.refresh()

    def bind_card_events(self, encoder):
        log.debug("LightCardController.bind_card_events")

        @self._card.on("toggle")
        async def _toggle():
            log.debug("LightCardController: toggle")
            await self._light.toggle()

        @encoder.on_turn_accumulated
        async def _brightness(steps: int):
            step = 0.05
            current = (self._light.brightness or 0) / 255.0
            target = max(0.0, min(1.0, current + steps * step))
            brightness = int(target * 255)
            log.info("Brightness: %+d steps → %d%%", steps, int(target * 100))
            await self._light.set_brightness(brightness)

        @encoder.on_press_turn_accumulated(max_steps=1)
        async def _kelvin(steps: int):
            step = 250
            current = self._light.kelvin or self._light.min_kelvin
            min_k = self._light.min_kelvin
            max_k = self._light.max_kelvin
            target = max(min_k, min(max_k, current + steps * step))
            log.info("Kelvin: %+d steps → %dK", steps, target)
            await self._light.set_kelvin(int(target))
    # endregion


class TimerCardController:
    """Manages the TimerCard DUI widget and its HA timer bindings."""

    DURATION_STEP = 30  # seconds per encoder tick

    def __init__(self, ha: HAClient, deck, timer: Timer, timercard_spec):
        log.debug("TimerCardController.__init__")
        self._ha = ha
        self._deck = deck
        self._timer = timer
        self._card = DuiCard(timercard_spec)
        self._duration_seconds: int = 300  # default 5 minutes
        self._tick_task: asyncio.Task | None = None
        self._bind_events()

    @property
    def card(self) -> DuiCard:
        return self._card

    @staticmethod
    def _fmt(seconds: int) -> str:
        """Format seconds as HH:MM:SS."""
        h, rem = divmod(max(seconds, 0), 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    @staticmethod
    def _duration_str(seconds: int) -> str:
        """Format seconds as H:MM:SS duration for HA service calls."""
        h, rem = divmod(max(seconds, 0), 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}"

    def _start_tick(self):
        """Start a background task that updates the display every second."""
        log.debug("TimerCardController._start_tick")
        self._stop_tick()
        self._tick_task = asyncio.create_task(self._tick_loop())

    def _stop_tick(self):
        log.debug("TimerCardController._stop_tick")
        if self._tick_task is not None:
            self._tick_task.cancel()
            self._tick_task = None

    async def _tick_loop(self):
        try:
            while True:
                remaining = self._timer.time_remaining
                if remaining is not None:
                    self._card.set("timer", self._fmt(int(remaining)))
                    await self._deck.refresh()
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    # region state sync
    async def sync_state(self):
        log.debug("TimerCardController.sync_state")
        await self._timer.async_refresh()
        self._update_card_from_state()
        await self._deck.refresh()

    def _update_card_from_state(self):
        timer = self._timer
        log.debug("TimerCardController._update_card_from_state: active=%s paused=%s", timer.is_active, timer.is_paused)
        if timer.is_active:
            remaining = timer.time_remaining
            self._card.set("timer", self._fmt(int(remaining or 0)))
            self._start_tick()
        elif timer.is_paused:
            remaining = timer.time_remaining
            self._card.set("timer", self._fmt(int(remaining or 0)))
            self._stop_tick()
        else:
            self._card.set("timer", self._fmt(self._duration_seconds))
            self._stop_tick()
    # endregion

    # region HA event handlers
    def _bind_events(self):
        timer = self._timer
        log.debug("TimerCardController._bind_events")

        @timer.on_start
        async def _on_start(old, new):
            log.debug("TimerCardController._on_start")
            self._start_tick()
            await self._deck.refresh()

        @timer.on_pause
        async def _on_pause(old, new):
            log.debug("TimerCardController._on_pause")
            self._stop_tick()
            remaining = timer.time_remaining
            if remaining is not None:
                self._card.set("timer", self._fmt(int(remaining)))
            await self._deck.refresh()

        @timer.on_idle
        async def _on_idle(old, new):
            log.debug("TimerCardController._on_idle")
            self._stop_tick()
            self._card.set("timer", self._fmt(self._duration_seconds))
            await self._deck.refresh()
    # endregion

    # region card UI event handlers
    def bind_card_events(self, encoder):
        timer = self._timer
        log.debug("TimerCardController.bind_card_events")

        @self._card.on("toggle")
        async def _toggle():
            log.debug("TimerCardController: toggle (active=%s paused=%s)", timer.is_active, timer.is_paused)
            if timer.is_active:
                await timer.pause()
            elif timer.is_paused:
                await timer.start()
            else:
                duration = self._duration_str(self._duration_seconds)
                await timer.start(duration=duration)

        @self._card.on("reset")
        async def _reset():
            log.debug("TimerCardController: reset")
            if timer.is_active or timer.is_paused:
                await timer.cancel()

        @encoder.on_turn_accumulated
        async def _adjust_duration(steps: int):
            log.debug("TimerCardController: adjust_duration steps=%+d", steps)
            if timer.is_idle:
                self._duration_seconds = max(
                    self.DURATION_STEP,
                    min(86400, self._duration_seconds + steps * self.DURATION_STEP),
                )
                self._card.set("timer", self._fmt(self._duration_seconds))
                await self._deck.refresh()
    # endregion


class DashboardCardController:
    """Manages the DashboardCard DUI widget."""

    def __init__(self, ha: HAClient, deck, dashboardcard_spec):
        log.debug("DashboardCardController.__init__")
        self._ha = ha
        self._deck = deck
        self._card = DuiCard(dashboardcard_spec)
        self._datetime_sensor = ha.sensor("sensor.date_time")
        self._temp_sensor = ha.sensor("sensor.livingroom_temperature")
        self._humidity_sensor = ha.sensor("sensor.livingroom_humidity")
        self._bind_events()

    @property
    def card(self) -> DuiCard:
        return self._card

    def _format_datetime(self, value: str):
        """Parse 'YYYY-MM-DD, HH:MM' from sensor.date_time and update card."""
        log.debug("DashboardCardController._format_datetime: %s", value)
        try:
            from datetime import datetime
            dt = datetime.strptime(value, "%Y-%m-%d, %H:%M")
            self._card.set("date", dt.strftime("%A, %d %b"))
            self._card.set("time", dt.strftime("%H:%M"))
        except (ValueError, TypeError):
            log.warning("Could not parse date_time: %s", value)

    def _bind_events(self):
        log.debug("DashboardCardController._bind_events")

        @self._datetime_sensor.on_value_change
        async def _on_datetime(old, new):
            log.debug("DashboardCardController._on_datetime: %s → %s", old, new)
            self._format_datetime(new)
            await self._deck.refresh()

        @self._temp_sensor.on_value_change
        async def _on_temp(old, new):
            log.debug("DashboardCardController._on_temp: %s → %s", old, new)
            self._card.set("temperature", f"{new}°")
            await self._deck.refresh()

        @self._humidity_sensor.on_value_change
        async def _on_humidity(old, new):
            log.debug("DashboardCardController._on_humidity: %s → %s", old, new)
            self._card.set("humidity", f"{new}%")
            await self._deck.refresh()

    def bind_card_events(self, encoder):
        log.debug("DashboardCardController.bind_card_events")

        @encoder.on_turn_accumulated(delay=0.05)
        async def _brightness(steps: int):
            step = 0.05
            current = self._deck.brightness / 100.0
            target = max(0.0, min(1.0, current + steps * step))
            brightness = int(target * 100)
            log.info("Deck brightness: %+d steps → %d%%", steps, brightness)
            await self._deck.set_brightness(brightness)
            self._card.set("deck_brightness", target)
            await self._deck.refresh()

    async def sync_state(self):
        log.debug("DashboardCardController.sync_state")
        await self._datetime_sensor.async_refresh()
        await self._temp_sensor.async_refresh()
        await self._humidity_sensor.async_refresh()

        self._format_datetime(self._datetime_sensor.state)
        self._card.set("temperature", f"{self._temp_sensor.state}°")
        self._card.set("humidity", f"{self._humidity_sensor.state}%")
        self._card.set("deck_brightness", self._deck.brightness / 100.0)
        await self._deck.refresh()


async def watch_reconnect(ha: HAClient, on_reconnected):
    """Wait for WS disconnect, then wait for reconnect, and call callback.

    The HAClient WS layer reconnects automatically and re-subscribes events,
    but entity *state* is stale until we explicitly refresh.
    """
    log.debug("watch_reconnect: starting")
    reconnected = asyncio.Event()

    @ha.ws.on_disconnect
    def _on_drop():
        log.warning("Home Assistant WebSocket disconnected")
        reconnected.clear()
        # Start polling for reconnection in a task
        asyncio.create_task(_wait_for_reconnect())

    async def _wait_for_reconnect():
        log.debug("watch_reconnect._wait_for_reconnect: polling")
        while not ha.ws.connected:
            await asyncio.sleep(1)
        log.info("Home Assistant WebSocket reconnected")
        await on_reconnected()

    # Keep this coroutine alive for the lifetime of the app
    await asyncio.Event().wait()


async def run():
    log.debug("run: starting")
    
    audiocard_spec = load_package(PACKAGES_DIR / "AudioCard.dui")
    picturekey_spec = load_package(PACKAGES_DIR / "PictureKey.dui")
    iconkey_spec = load_package(PACKAGES_DIR / "IconKey.dui")
    lightcard_spec = load_package(PACKAGES_DIR / "LightCard.dui")
    dashboardcard_spec = load_package(PACKAGES_DIR / "DashboardCard.dui")
    timercard_spec = load_package(PACKAGES_DIR / "TimerCard.dui")

    server = os.environ["HA_URL"]
    token = os.environ["HA_TOKEN"]

    manager = DeckManager(brightness=60, auto_reconnect=True)

    async with HAClient(server, token=token) as ha:
        player = ha.media_player(MEDIA_PLAYER)
        upstairs = ha.light(UPSTAIRS_LIGHTS)
        timer = ha.timer(TIMER_ENTITY)

        @manager.on_connect(serial=STREAMDECK_SERIAL)
        async def on_deck_connect(deck):
            log.info("Deck connected: %s", STREAMDECK_SERIAL)

            screen = deck.screen("main")
            if screen.touch_strip is not None:
                screen.touch_strip.background_color = "#1c1c1c"

            audio_ctrl = AudioCardController(ha, deck, player, audiocard_spec)
            audio_ctrl.bind_card_events(screen.encoder(0))
            screen.set_card(0, audio_ctrl.card)

            light_ctrl = LightCardController(ha, deck, upstairs, lightcard_spec)
            light_ctrl.bind_card_events(screen.encoder(1))
            screen.set_card(1, light_ctrl.card)

            timer_ctrl = TimerCardController(ha, deck, timer, timercard_spec)
            timer_ctrl.bind_card_events(screen.encoder(2))
            screen.set_card(2, timer_ctrl.card)

            dash_ctrl = DashboardCardController(ha, deck, dashboardcard_spec)
            dash_ctrl.bind_card_events(screen.encoder(3))
            screen.set_card(3, dash_ctrl.card)

            favorite_keys: list[DuiKey] = []

            async def _stop_favorites_busy():
                for k in favorite_keys:
                    await k.finish_busy()

            audio_ctrl.on_any_state(_stop_favorites_busy)

            async def load_state():
                """(Re)load all HA state and refresh the deck."""
                nonlocal favorite_keys
                log.info("Loading Home Assistant state…")
                await ha.refresh_all()
                favorite_keys = await setup_favorites(screen, player, picturekey_spec)
                await setup_scenes(screen, iconkey_spec)
                await audio_ctrl.sync_state()
                await light_ctrl.sync_state()
                await timer_ctrl.sync_state()
                await dash_ctrl.sync_state()

            await load_state()

            asyncio.create_task(watch_reconnect(ha, load_state))

            await deck.set_screen("main")
            log.info("Deck ready!")

        @manager.on_disconnect
        async def on_deck_disconnect(info: DeviceInfo):
            log.warning("Deck disconnected: %s — waiting for reconnect…", info.serial)

        log.info("Waiting for StreamDeck %s…", STREAMDECK_SERIAL)
        async with manager:
            await manager.wait_closed()


def main():
    log.debug("main: entry")
    asyncio.run(run())

if __name__ == "__main__":
    main()
