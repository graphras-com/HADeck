#!/usr/bin/env python3
"""StreamDeck+ client for Home Assistant."""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

from deckui import DeckManager, DeviceInfo, DuiKey, load_package
from haclient import HAClient

from controllers import (
    AudioCardController,
    DashboardCardController,
    LightCardController,
    TimerCardController,
)
from helpers import PACKAGES_DIR, setup_favorites, setup_scenes

load_dotenv()

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")

log = logging.getLogger(__name__)

STREAMDECK_SERIAL = os.environ.get("STREAMDECK_SERIAL")
MEDIA_PLAYER = os.environ.get("MEDIA_PLAYER", "entertainment")
UPSTAIRS_LIGHTS = os.environ.get("UPSTAIRS_LIGHTS", "upstairs")
TIMER_NAME = os.environ.get("TIMER_ENTITY", "streamdeck")


async def run():
    log.debug("run: starting")

    picturekey_spec = load_package(PACKAGES_DIR / "PictureKey.dui")
    iconkey_spec = load_package(PACKAGES_DIR / "IconKey.dui")

    server = os.environ["HA_URL"]
    token = os.environ["HA_TOKEN"]

    manager = DeckManager(brightness=60, auto_reconnect=True)

    async with HAClient.from_url(server, token=token) as ha:
        player = ha.media_player(MEDIA_PLAYER)
        upstairs = ha.light(UPSTAIRS_LIGHTS)
        timer = ha.timer("TIMER_NAME")

        @manager.on_connect(serial=STREAMDECK_SERIAL)
        async def on_deck_connect(deck):
            log.info("Deck connected: %s", STREAMDECK_SERIAL)

            screen = deck.screen("main")
            if screen.touch_strip is not None:
                screen.touch_strip.background_color = "#1c1c1c"

            audio_ctrl = AudioCardController(ha, deck, player)
            audio_ctrl.bind_card_events(screen.encoder(0))
            screen.set_card(0, audio_ctrl.card)

            light_ctrl = LightCardController(ha, deck, upstairs)
            light_ctrl.bind_card_events(screen.encoder(1))
            screen.set_card(1, light_ctrl.card)

            timer_ctrl = TimerCardController(ha, deck, timer)
            timer_ctrl.bind_card_events(screen.encoder(2))
            screen.set_card(2, timer_ctrl.card)

            dash_ctrl = DashboardCardController(ha, deck)
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
                log.info("Loading Home Assistant state...")
                favorite_keys = await setup_favorites(screen, player, picturekey_spec)
                await setup_scenes(screen, iconkey_spec)
                await audio_ctrl.sync_state()
                await light_ctrl.sync_state()
                await timer_ctrl.sync_state()
                await dash_ctrl.sync_state()

            async def sync_deck():
                """Re-sync deck UI after HAClient reconnects (state already refreshed)."""
                nonlocal favorite_keys
                log.info("Re-syncing deck after reconnect...")
                favorite_keys = await setup_favorites(screen, player, picturekey_spec)
                await setup_scenes(screen, iconkey_spec)
                await audio_ctrl.sync_state()
                await light_ctrl.sync_state()
                await timer_ctrl.sync_state()
                await dash_ctrl.sync_state()

            await load_state()

            ha.on_reconnect(sync_deck)

            await deck.set_screen("main")
            log.info("Deck ready!")

        @manager.on_disconnect
        async def on_deck_disconnect(info: DeviceInfo):
            log.warning("Deck disconnected: %s -- waiting for reconnect...", info.serial)

        log.info("Waiting for StreamDeck %s...", STREAMDECK_SERIAL)
        async with manager:
            await manager.wait_closed()


def main():
    log.debug("main: entry")
    asyncio.run(run())


if __name__ == "__main__":
    main()
