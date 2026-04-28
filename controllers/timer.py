from __future__ import annotations

import asyncio
import logging

from deckui import DuiCard, load_package
from haclient import HAClient, Timer

from helpers import PACKAGES_DIR

log = logging.getLogger(__name__)


class TimerCardController:
    """Manages the TimerCard DUI widget and its HA timer bindings."""

    DURATION_STEP = 30  # seconds per encoder tick

    def __init__(self, ha: HAClient, deck, timer: Timer):
        log.debug("TimerCardController.__init__")
        self._ha = ha
        self._deck = deck
        self._timer = timer
        timercard_spec = load_package(PACKAGES_DIR / "TimerCard.dui")
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
                log.debug("TimerCardController: start (duration_str=%s duration=%d)", duration, self._duration_seconds)
                #await timer.start(duration=duration)
                await timer.start(duration="00:00:10")

        @self._card.on("reset")
        async def _reset():
            log.debug("TimerCardController: reset")
            if timer.is_active or timer.is_paused:
                await timer.cancel()

        @self._card.on("increase_duration")
        async def _increase_duration(steps: int):
            log.debug("TimerCardController: increase_duration steps=+%d", steps)
            if not timer.is_active:
                self._duration_seconds = max(
                    self.DURATION_STEP,
                    min(86400, self._duration_seconds + steps * self.DURATION_STEP),
                )
                self._card.set("timer", self._fmt(self._duration_seconds))
                await self._deck.refresh()

        @self._card.on("decrease_duration")
        async def _decrease_duration(steps: int):
            log.debug("TimerCardController: decrease_duration steps=-%d", abs(steps))
            if not timer.is_active:
                self._duration_seconds = max(
                    self.DURATION_STEP,
                    min(86400, self._duration_seconds - abs(steps) * self.DURATION_STEP),
                )
                self._card.set("timer", self._fmt(self._duration_seconds))
                await self._deck.refresh()
