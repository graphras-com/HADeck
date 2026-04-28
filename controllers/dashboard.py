from __future__ import annotations

import logging
from datetime import datetime

from deckui import DuiCard, load_package
from haclient import HAClient

from helpers import PACKAGES_DIR

log = logging.getLogger(__name__)


class DashboardCardController:
    """Manages the DashboardCard DUI widget."""

    def __init__(self, ha: HAClient, deck):
        log.debug("DashboardCardController.__init__")
        self._ha = ha
        self._deck = deck
        dashboardcard_spec = load_package(PACKAGES_DIR / "DashboardCard.dui")
        self._card = DuiCard(dashboardcard_spec)
        self._datetime_sensor = ha.sensor("date_time")
        self._temp_sensor = ha.sensor("livingroom_temperature")
        self._humidity_sensor = ha.sensor("livingroom_humidity")
        self._bind_events()

    @property
    def card(self) -> DuiCard:
        return self._card

    def _format_datetime(self, value: str):
        """Parse 'YYYY-MM-DD, HH:MM' from sensor.date_time and update card."""
        log.debug("DashboardCardController._format_datetime: %s", value)
        try:
            dt = datetime.strptime(value, "%Y-%m-%d, %H:%M")
            self._card.set("date", dt.strftime("%A, %d %b"))
            self._card.set("time", dt.strftime("%H:%M"))
        except (ValueError, TypeError):
            log.warning("Could not parse date_time: %s", value)

    def _bind_events(self):
        log.debug("DashboardCardController._bind_events")

        @self._datetime_sensor.on_value_change
        async def _on_datetime(old, new):
            log.debug("DashboardCardController._on_datetime: %s -> %s", old, new)
            self._format_datetime(new)
            await self._deck.refresh()

        @self._temp_sensor.on_value_change
        async def _on_temp(old, new):
            log.debug("DashboardCardController._on_temp: %s -> %s", old, new)
            self._card.set("temperature", f"{new}°")
            await self._deck.refresh()

        @self._humidity_sensor.on_value_change
        async def _on_humidity(old, new):
            log.debug("DashboardCardController._on_humidity: %s -> %s", old, new)
            self._card.set("humidity", f"{new}%")
            await self._deck.refresh()

    def bind_card_events(self, encoder):
        log.debug("DashboardCardController.bind_card_events")

        @self._card.on("brightness_up")
        async def _brightness_up(steps: int):
            new_val = self._card.adjust_range("deck_brightness", steps * 5, min_val=0, max_val=100)
            log.info("Deck brightness: +%d steps -> %d%%", steps, int(new_val))
            await self._deck.set_brightness(int(new_val))
            await self._deck.refresh()

        @self._card.on("brightness_down")
        async def _brightness_down(steps: int):
            new_val = self._card.adjust_range("deck_brightness", -abs(steps) * 5, min_val=0, max_val=100)
            log.info("Deck brightness: -%d steps -> %d%%", abs(steps), int(new_val))
            await self._deck.set_brightness(int(new_val))
            await self._deck.refresh()

    async def sync_state(self):
        log.debug("DashboardCardController.sync_state")
        await self._datetime_sensor.async_refresh()
        await self._temp_sensor.async_refresh()
        await self._humidity_sensor.async_refresh()

        self._format_datetime(self._datetime_sensor.state)
        self._card.set("temperature", f"{self._temp_sensor.state}°")
        self._card.set("humidity", f"{self._humidity_sensor.state}%")
        self._card.set_range("deck_brightness", self._deck.brightness, min_val=0, max_val=100)
        await self._deck.refresh()
