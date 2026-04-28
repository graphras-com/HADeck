from __future__ import annotations

import logging

from deckui import DuiCard, load_package
from haclient import HAClient

from helpers import PACKAGES_DIR

log = logging.getLogger(__name__)


class LightCardController:
    """Manages the LightCard DUI widget and its HA light bindings."""

    def __init__(self, ha: HAClient, deck, light):
        log.debug("LightCardController.__init__")
        self._ha = ha
        self._deck = deck
        self._light = light
        lightcard_spec = load_package(PACKAGES_DIR / "LightCard.dui")
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
        self._card.set_range("brightness", brightness, min_val=0, max_val=255)
        brightness_pct = self._card.get_range("brightness", min_val=0, max_val=100)
        self._card.set("brightness_value_text", f"{int(brightness_pct)}%")

        kelvin = light.kelvin or light.min_kelvin
        min_k = light.min_kelvin
        max_k = light.max_kelvin
        self._card.set_range("kelvin", kelvin, min_val=min_k, max_val=max_k)
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
            log.debug("LightCardController._on_brightness: %s -> %s", old, new)
            self._update_card_from_state()
            await self._deck.refresh()

        @light.on_color_change
        async def _on_color(old, new):
            log.debug("LightCardController._on_color: %s -> %s", old, new)
            self._update_card_from_state()
            await self._deck.refresh()

        @light.on_kelvin_change
        async def _on_kelvin(old, new):
            log.debug("LightCardController._on_kelvin: %s -> %s", old, new)
            self._update_card_from_state()
            await self._deck.refresh()

    def bind_card_events(self, encoder):
        log.debug("LightCardController.bind_card_events")

        @self._card.on("toggle")
        async def _toggle():
            log.debug("LightCardController: toggle")
            await self._light.toggle()

        @self._card.on("brightness_up")
        async def _brightness_up(steps: int):
            step = 0.05 * 255
            new_val = self._card.adjust_range("brightness", steps * step, min_val=0, max_val=255)
            log.info("Brightness: +%d steps -> %d%%", steps, int(new_val / 255 * 100))
            await self._light.set_brightness(int(new_val))

        @self._card.on("brightness_down")
        async def _brightness_down(steps: int):
            step = 0.05 * 255
            new_val = self._card.adjust_range("brightness", -abs(steps) * step, min_val=0, max_val=255)
            log.info("Brightness: -%d steps -> %d%%", abs(steps), int(new_val / 255 * 100))
            await self._light.set_brightness(int(new_val))

        @self._card.on("kelvin_up")
        async def _kelvin_up(steps: int):
            step = 250
            min_k = self._light.min_kelvin
            max_k = self._light.max_kelvin
            new_val = self._card.adjust_range("kelvin", steps * step, min_val=min_k, max_val=max_k)
            log.info("Kelvin: +%d steps -> %dK", steps, int(new_val))
            await self._light.set_kelvin(int(new_val))

        @self._card.on("kelvin_down")
        async def _kelvin_down(steps: int):
            step = 250
            min_k = self._light.min_kelvin
            max_k = self._light.max_kelvin
            new_val = self._card.adjust_range("kelvin", -abs(steps) * step, min_val=min_k, max_val=max_k)
            log.info("Kelvin: -%d steps -> %dK", abs(steps), int(new_val))
            await self._light.set_kelvin(int(new_val))
