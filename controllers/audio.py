from __future__ import annotations

import logging
from pathlib import Path

from deckui import DuiCard, load_package
from haclient import HAClient, NowPlaying

from helpers import PACKAGES_DIR, fetch_image

log = logging.getLogger(__name__)


class AudioCardController:
    """Manages the AudioCard DUI widget and its HA media-player bindings."""

    def __init__(self, ha: HAClient, deck, player):
        log.debug("AudioCardController.__init__")
        self._ha = ha
        self._deck = deck
        self._player = player
        audiocard_spec = load_package(PACKAGES_DIR / "AudioCard.dui")
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

        volume_pct = (player.volume_level or 0.0) * 100
        self._card.set_range("volume", volume_pct, min_val=0, max_val=100)
        if player.is_muted:
            self._card.set("value_text", "Muted")
        else:
            self._card.set("value_text", f"{int(volume_pct)}%")

        await self._deck.refresh()

    async def _update_now_playing(self, media: NowPlaying):
        log.debug("AudioCardController._update_now_playing: %s - %s", media.artist, media.title)
        picture = None
        if media.entity_picture is not None:
            picture = await fetch_image(media.entity_picture)
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
            log.debug("AudioCardController._on_volume: %s -> %s", old, new)
            vol_pct = (player.volume_level or 0.0) * 100
            self._card.set_range("volume", vol_pct, min_val=0, max_val=100)
            self._card.set("value_text", f"{int(vol_pct)}%")
            await self._fire_state_callbacks()
            await self._deck.refresh()

        @player.on_mute_change
        async def _on_mute(old, new):
            log.debug("AudioCardController._on_mute: %s -> %s", old, new)
            if new:
                self._card.set("value_text", "Muted")
            else:
                vol_pct = (player.volume_level or 0.0) * 100
                self._card.set("value_text", f"{int(vol_pct)}%")
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

        @self._card.on("volume_up")
        async def _volume_up(steps: int):
            new_vol = self._card.adjust_range("volume", steps, min_val=0, max_val=100)
            log.info("Volume: +%d steps -> %.0f%%", steps, new_vol)
            await player.set_volume(new_vol / 100.0)

        @self._card.on("volume_down")
        async def _volume_down(steps: int):
            new_vol = self._card.adjust_range("volume", -abs(steps), min_val=0, max_val=100)
            log.info("Volume: -%d steps -> %.0f%%", abs(steps), new_vol)
            await player.set_volume(new_vol / 100.0)

        @self._card.on("mute_toggle")
        async def _mute():
            log.debug("AudioCardController: mute_toggle")
            await player.mute(not player.is_muted)

        @self._card.on("next")
        async def _next(steps: int):
            log.info("Skip: next")
            await player.next()

        @self._card.on("previous")
        async def _previous(steps: int):
            log.info("Skip: previous")
            await player.previous()
