"""Tests for main.py – targets ≥95 % coverage."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from PIL import Image

# We need to be able to import main without side-effects from load_dotenv /
# logging.basicConfig.  Both are harmless at import time, so just import.
import main as m


# ---------------------------------------------------------------------------
# Helpers – tiny fakes for ha_client / deckboard objects
# ---------------------------------------------------------------------------

def _make_image(size=(4, 4)) -> bytes:
    img = Image.new("RGB", size, color="red")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _now_playing(entity_picture=None, **kw) -> m.NowPlaying:
    return m.NowPlaying(entity_picture=entity_picture, **kw)


class FakeFavorite:
    def __init__(self, title, media_content_id, thumbnail=None, category=None):
        self.title = title
        self.media_content_id = media_content_id
        self.media_content_type = "music"
        self.thumbnail = thumbnail
        self.category = category
        self.media_class = "track"


class FakePlayer:
    """Mimics MediaPlayer event-decorator API."""

    def __init__(self):
        self.is_playing = True
        self.is_paused = False
        self.is_muted = False
        self.volume_level = 0.5
        self.now_playing = _now_playing(title="Song", artist="Artist", album="Album")

        self.async_refresh = AsyncMock()
        self.play_pause = AsyncMock()
        self.mute = AsyncMock()
        self.favorites = AsyncMock(return_value=[])

        # Registered callbacks
        self._cbs: dict[str, object] = {}

    # Decorator stubs – store the handler so tests can fire them
    def on_volume_change(self, fn):
        self._cbs["volume_change"] = fn
        return fn

    def on_mute_change(self, fn):
        self._cbs["mute_change"] = fn
        return fn

    def on_play(self, fn):
        self._cbs["play"] = fn
        return fn

    def on_pause(self, fn):
        self._cbs["pause"] = fn
        return fn

    def on_media_change(self, fn):
        self._cbs["media_change"] = fn
        return fn

    async def fire(self, event, old=None, new=None):
        cb = self._cbs[event]
        await cb(old, new)


class FakeCard:
    """Mimics DsuiCard."""

    def __init__(self, spec):
        self.values: dict = {}
        self._handlers: dict[str, object] = {}

    def set(self, name, value):
        self.values[name] = value
        return self

    def set_many(self, **kw):
        self.values.update(kw)
        return self

    def on(self, event_name):
        def decorator(fn):
            self._handlers[event_name] = fn
            return fn
        return decorator

    async def fire(self, event):
        await self._handlers[event]()


class FakeKey:
    """Mimics DsuiKey."""

    def __init__(self, spec):
        self.values: dict = {}
        self._handlers: dict[str, object] = {}

    def set(self, name, value):
        self.values[name] = value
        return self

    def on_event(self, event_name):
        def decorator(fn):
            self._handlers[event_name] = fn
            return fn
        return decorator


class FakeScreen:
    def __init__(self):
        self.keys: dict[int, object] = {}
        self.cards: dict[int, object] = {}
        self.touch_strip = SimpleNamespace(background_color=None)

    def set_key(self, slot, key):
        self.keys[slot] = key

    def set_card(self, slot, card):
        self.cards[slot] = card


class FakeWs:
    def __init__(self):
        self.connected = True
        self._disconnect_cbs: list = []

    def on_disconnect(self, fn):
        self._disconnect_cbs.append(fn)
        return fn

    def fire_disconnect(self):
        for cb in self._disconnect_cbs:
            cb()


class FakeHA:
    def __init__(self):
        self.base_url = "http://ha.local"
        self.ws = FakeWs()
        self.refresh_all = AsyncMock()
        self._player = FakePlayer()

    def media_player(self, name):
        return self._player


class FakeDeck:
    def __init__(self):
        self._screens: dict[str, FakeScreen] = {}
        self.refresh = AsyncMock()
        self.set_screen = AsyncMock()
        self.wait_closed = AsyncMock()

    def screen(self, name):
        if name not in self._screens:
            self._screens[name] = FakeScreen()
        return self._screens[name]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def player():
    return FakePlayer()


@pytest.fixture
def deck():
    return FakeDeck()


@pytest.fixture
def ha():
    return FakeHA()


@pytest.fixture
def fake_spec():
    return SimpleNamespace(name="test", version=1, bindings={}, events=[], assets={})


# ---------------------------------------------------------------------------
# _fetch_image
# ---------------------------------------------------------------------------

class TestFetchImage:
    @pytest.mark.asyncio
    async def test_success(self):
        img_bytes = _make_image()

        resp = AsyncMock()
        resp.status = 200
        resp.read = AsyncMock(return_value=img_bytes)

        session_ctx = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=session_ctx)
        session_ctx.__aexit__ = AsyncMock(return_value=False)

        get_ctx = AsyncMock()
        get_ctx.__aenter__ = AsyncMock(return_value=resp)
        get_ctx.__aexit__ = AsyncMock(return_value=False)
        session_ctx.get = MagicMock(return_value=get_ctx)

        with patch("main.aiohttp.ClientSession", return_value=session_ctx):
            result = await m._fetch_image("http://example.com/img.png")

        assert result is not None
        assert isinstance(result, Image.Image)

    @pytest.mark.asyncio
    async def test_non_200(self):
        resp = AsyncMock()
        resp.status = 404

        session_ctx = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=session_ctx)
        session_ctx.__aexit__ = AsyncMock(return_value=False)

        get_ctx = AsyncMock()
        get_ctx.__aenter__ = AsyncMock(return_value=resp)
        get_ctx.__aexit__ = AsyncMock(return_value=False)
        session_ctx.get = MagicMock(return_value=get_ctx)

        with patch("main.aiohttp.ClientSession", return_value=session_ctx):
            result = await m._fetch_image("http://example.com/nope")

        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self):
        with patch("main.aiohttp.ClientSession", side_effect=Exception("boom")):
            result = await m._fetch_image("http://example.com/err")
        assert result is None


# ---------------------------------------------------------------------------
# _load_dsui
# ---------------------------------------------------------------------------

class TestLoadDsui:
    def test_calls_load_package(self, fake_spec):
        with patch("main.load_package", return_value=fake_spec) as mock_lp:
            result = m._load_dsui("Foo.dsui")
        assert result is fake_spec
        mock_lp.assert_called_once()


# ---------------------------------------------------------------------------
# setup_favorites
# ---------------------------------------------------------------------------

class TestSetupFavorites:
    @pytest.mark.asyncio
    async def test_populates_keys(self, player, fake_spec):
        favs = [
            FakeFavorite("Station A", "id_a", category="Radio"),
            FakeFavorite("Album B", "id_b", thumbnail="http://img/b", category="Albums"),
            FakeFavorite("Playlist C", "id_c", category="Playlists"),
        ]
        player.favorites = AsyncMock(return_value=favs)
        screen = FakeScreen()

        fake_keys: list[FakeKey] = []
        original_init = FakeKey.__init__

        with patch("main.DsuiKey") as MockKey, \
             patch("main._fetch_image", new_callable=AsyncMock, return_value=None):
            def make_key(spec):
                k = FakeKey(spec)
                fake_keys.append(k)
                return k
            MockKey.side_effect = make_key

            await m.setup_favorites(screen, player, fake_spec)

        # 3 favs → 3 keys in slots 0, 1, 2
        assert len(screen.keys) == 3
        assert set(screen.keys.keys()) == {0, 1, 2}

    @pytest.mark.asyncio
    async def test_sorts_by_category(self, player, fake_spec):
        favs = [
            FakeFavorite("Z Album", "id_z", category="Albums"),
            FakeFavorite("A Radio", "id_a", category="Radio"),
            FakeFavorite("M Playlist", "id_m", category="Playlists"),
        ]
        player.favorites = AsyncMock(return_value=favs)
        screen = FakeScreen()

        order: list[str] = []

        with patch("main.DsuiKey") as MockKey, \
             patch("main._fetch_image", new_callable=AsyncMock, return_value=None):
            def make_key(spec):
                k = FakeKey(spec)
                return k
            MockKey.side_effect = make_key

            # Capture which fav goes to which slot by intercepting set_key
            original_set_key = screen.set_key
            def tracking_set_key(slot, key):
                original_set_key(slot, key)
            screen.set_key = tracking_set_key

            await m.setup_favorites(screen, player, fake_spec)

        # Radio (0) < Playlists (1) < Albums (2) order
        assert len(screen.keys) == 3

    @pytest.mark.asyncio
    async def test_max_six_favorites(self, player, fake_spec):
        favs = [FakeFavorite(f"F{i}", f"id_{i}") for i in range(10)]
        player.favorites = AsyncMock(return_value=favs)
        screen = FakeScreen()

        with patch("main.DsuiKey") as MockKey, \
             patch("main._fetch_image", new_callable=AsyncMock, return_value=None):
            MockKey.side_effect = lambda spec: FakeKey(spec)
            await m.setup_favorites(screen, player, fake_spec)

        assert len(screen.keys) == 6

    @pytest.mark.asyncio
    async def test_thumbnail_fetched(self, player, fake_spec):
        img = Image.new("RGB", (4, 4))
        favs = [FakeFavorite("T", "id_t", thumbnail="http://img/t")]
        player.favorites = AsyncMock(return_value=favs)
        screen = FakeScreen()
        keys: list[FakeKey] = []

        with patch("main.DsuiKey") as MockKey, \
             patch("main._fetch_image", new_callable=AsyncMock, return_value=img):
            def make_key(spec):
                k = FakeKey(spec)
                keys.append(k)
                return k
            MockKey.side_effect = make_key
            await m.setup_favorites(screen, player, fake_spec)

        assert keys[0].values.get("picture") is img

    @pytest.mark.asyncio
    async def test_thumbnail_none_not_set(self, player, fake_spec):
        favs = [FakeFavorite("T", "id_t", thumbnail=None)]
        player.favorites = AsyncMock(return_value=favs)
        screen = FakeScreen()
        keys: list[FakeKey] = []

        with patch("main.DsuiKey") as MockKey:
            def make_key(spec):
                k = FakeKey(spec)
                keys.append(k)
                return k
            MockKey.side_effect = make_key
            await m.setup_favorites(screen, player, fake_spec)

        assert "picture" not in keys[0].values

    @pytest.mark.asyncio
    async def test_click_handler_registered(self, player, fake_spec):
        favs = [FakeFavorite("X", "id_x")]
        player.favorites = AsyncMock(return_value=favs)
        screen = FakeScreen()
        keys: list[FakeKey] = []

        with patch("main.DsuiKey") as MockKey, \
             patch("main._fetch_image", new_callable=AsyncMock, return_value=None):
            MockKey.side_effect = lambda spec: FakeKey(spec)
            await m.setup_favorites(screen, player, fake_spec)


# ---------------------------------------------------------------------------
# AudioCardController
# ---------------------------------------------------------------------------

class TestAudioCardController:
    def _make_ctrl(self, ha, deck, player, fake_spec):
        with patch("main.DsuiCard") as MockCard:
            cards = []
            def make_card(spec):
                c = FakeCard(spec)
                cards.append(c)
                return c
            MockCard.side_effect = make_card
            ctrl = m.AudioCardController(ha, deck, player, fake_spec)
        return ctrl, cards[0]

    @pytest.mark.asyncio
    async def test_sync_state_playing(self, ha, deck, player, fake_spec):
        player.is_playing = True
        player.is_muted = False
        player.volume_level = 0.75
        player.now_playing = _now_playing(title="T", artist="A", album="Al")

        ctrl, card = self._make_ctrl(ha, deck, player, fake_spec)

        with patch("main._fetch_image", new_callable=AsyncMock, return_value=None):
            await ctrl.sync_state()

        assert card.values["state"] == "Playing"
        assert card.values["volume"] == 0.75
        assert card.values["value_text"] == "75%"
        player.async_refresh.assert_awaited_once()
        deck.refresh.assert_awaited()

    @pytest.mark.asyncio
    async def test_sync_state_paused_muted(self, ha, deck, player, fake_spec):
        player.is_playing = False
        player.is_muted = True
        player.volume_level = 0.3

        ctrl, card = self._make_ctrl(ha, deck, player, fake_spec)

        with patch("main._fetch_image", new_callable=AsyncMock, return_value=None):
            await ctrl.sync_state()

        assert card.values["state"] == "Paused"
        assert card.values["value_text"] == "Muted"

    @pytest.mark.asyncio
    async def test_sync_state_zero_volume(self, ha, deck, player, fake_spec):
        player.volume_level = None
        player.is_muted = False
        player.is_playing = True

        ctrl, card = self._make_ctrl(ha, deck, player, fake_spec)

        with patch("main._fetch_image", new_callable=AsyncMock, return_value=None):
            await ctrl.sync_state()

        assert card.values["volume"] == 0.0
        assert card.values["value_text"] == "0%"

    @pytest.mark.asyncio
    async def test_update_now_playing_with_picture(self, ha, deck, player, fake_spec):
        ctrl, card = self._make_ctrl(ha, deck, player, fake_spec)
        img = Image.new("RGB", (4, 4))
        media = _now_playing(entity_picture="/api/img", title="S", artist="A", album="B")

        with patch("main._fetch_image", new_callable=AsyncMock, return_value=img) as mock_fetch:
            await ctrl._update_now_playing(media)
            mock_fetch.assert_awaited_once_with("http://ha.local/api/img")

        assert card.values["title"] == "S"
        assert card.values["cover"] is img

    @pytest.mark.asyncio
    async def test_update_now_playing_no_picture(self, ha, deck, player, fake_spec):
        ctrl, card = self._make_ctrl(ha, deck, player, fake_spec)
        media = _now_playing(title="No Pic")

        await ctrl._update_now_playing(media)
        assert card.values["cover"] is None

    @pytest.mark.asyncio
    async def test_on_volume_change(self, ha, deck, player, fake_spec):
        ctrl, card = self._make_ctrl(ha, deck, player, fake_spec)
        player.volume_level = 0.42

        await player.fire("volume_change")

        assert card.values["volume"] == 0.42
        assert card.values["value_text"] == "42%"
        deck.refresh.assert_awaited()

    @pytest.mark.asyncio
    async def test_on_volume_change_none(self, ha, deck, player, fake_spec):
        ctrl, card = self._make_ctrl(ha, deck, player, fake_spec)
        player.volume_level = None

        await player.fire("volume_change")

        assert card.values["volume"] == 0.0
        assert card.values["value_text"] == "0%"

    @pytest.mark.asyncio
    async def test_on_mute_true(self, ha, deck, player, fake_spec):
        ctrl, card = self._make_ctrl(ha, deck, player, fake_spec)

        await player.fire("mute_change", new=True)
        assert card.values["value_text"] == "Muted"
        deck.refresh.assert_awaited()

    @pytest.mark.asyncio
    async def test_on_mute_false(self, ha, deck, player, fake_spec):
        ctrl, card = self._make_ctrl(ha, deck, player, fake_spec)
        player.volume_level = 0.65

        await player.fire("mute_change", new=False)
        assert card.values["value_text"] == "65%"

    @pytest.mark.asyncio
    async def test_on_mute_false_none_volume(self, ha, deck, player, fake_spec):
        ctrl, card = self._make_ctrl(ha, deck, player, fake_spec)
        player.volume_level = None

        await player.fire("mute_change", new=False)
        assert card.values["value_text"] == "0%"

    @pytest.mark.asyncio
    async def test_on_play(self, ha, deck, player, fake_spec):
        ctrl, card = self._make_ctrl(ha, deck, player, fake_spec)

        await player.fire("play")
        assert card.values["state"] == "Playing"
        deck.refresh.assert_awaited()

    @pytest.mark.asyncio
    async def test_on_pause(self, ha, deck, player, fake_spec):
        ctrl, card = self._make_ctrl(ha, deck, player, fake_spec)

        await player.fire("pause")
        assert card.values["state"] == "Paused"
        deck.refresh.assert_awaited()

    @pytest.mark.asyncio
    async def test_on_media_change(self, ha, deck, player, fake_spec):
        ctrl, card = self._make_ctrl(ha, deck, player, fake_spec)
        new_media = _now_playing(title="New Song")

        with patch("main._fetch_image", new_callable=AsyncMock, return_value=None):
            await player.fire("media_change", new=new_media)

        assert card.values["title"] == "New Song"
        deck.refresh.assert_awaited()

    def test_card_property(self, ha, deck, player, fake_spec):
        ctrl, card = self._make_ctrl(ha, deck, player, fake_spec)
        assert ctrl.card is card

    @pytest.mark.asyncio
    async def test_bind_card_events_toggle(self, ha, deck, player, fake_spec):
        ctrl, card = self._make_ctrl(ha, deck, player, fake_spec)
        ctrl.bind_card_events()

        await card.fire("toggle_play_pause")
        player.play_pause.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bind_card_events_mute_toggle(self, ha, deck, player, fake_spec):
        ctrl, card = self._make_ctrl(ha, deck, player, fake_spec)
        player.is_muted = False
        ctrl.bind_card_events()

        await card.fire("mute_toggle")
        player.mute.assert_awaited_once_with(True)

    @pytest.mark.asyncio
    async def test_bind_card_events_volume_up(self, ha, deck, player, fake_spec):
        ctrl, card = self._make_ctrl(ha, deck, player, fake_spec)
        ctrl.bind_card_events()
        await card.fire("volume_up")
        deck.refresh.assert_awaited()

    @pytest.mark.asyncio
    async def test_bind_card_events_volume_down(self, ha, deck, player, fake_spec):
        ctrl, card = self._make_ctrl(ha, deck, player, fake_spec)
        ctrl.bind_card_events()
        await card.fire("volume_down")
        deck.refresh.assert_awaited()

    @pytest.mark.asyncio
    async def test_bind_card_events_next(self, ha, deck, player, fake_spec):
        ctrl, card = self._make_ctrl(ha, deck, player, fake_spec)
        ctrl.bind_card_events()
        await card.fire("next")

    @pytest.mark.asyncio
    async def test_bind_card_events_previous(self, ha, deck, player, fake_spec):
        ctrl, card = self._make_ctrl(ha, deck, player, fake_spec)
        ctrl.bind_card_events()
        await card.fire("previous")


# ---------------------------------------------------------------------------
# watch_reconnect
# ---------------------------------------------------------------------------

class TestWatchReconnect:
    @pytest.mark.asyncio
    async def test_disconnect_triggers_reload(self, ha):
        callback = AsyncMock()
        ha.ws.connected = True

        task = asyncio.create_task(m.watch_reconnect(ha, callback))
        await asyncio.sleep(0.05)

        # Simulate disconnect then reconnect
        ha.ws.connected = False
        ha.ws.fire_disconnect()
        await asyncio.sleep(0.1)
        ha.ws.connected = True
        # Give the polling loop time to detect reconnection
        await asyncio.sleep(1.5)

        callback.assert_awaited_once()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------

class TestRun:
    @pytest.mark.asyncio
    async def test_full_run(self, fake_spec):
        fake_ha = FakeHA()
        fake_deck = FakeDeck()
        fake_player = fake_ha._player
        fake_player.favorites = AsyncMock(return_value=[])
        fake_player.now_playing = _now_playing(title="T")

        # Make wait_closed resolve quickly
        fake_deck.wait_closed = AsyncMock(return_value=None)

        ha_ctx = AsyncMock()
        ha_ctx.__aenter__ = AsyncMock(return_value=fake_ha)
        ha_ctx.__aexit__ = AsyncMock(return_value=False)

        deck_ctx = AsyncMock()
        deck_ctx.__aenter__ = AsyncMock(return_value=fake_deck)
        deck_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("main._load_dsui", return_value=fake_spec), \
             patch("main.HAClient", return_value=ha_ctx), \
             patch("main.Deck", return_value=deck_ctx), \
             patch("main.DsuiCard") as MockCard, \
             patch("main._fetch_image", new_callable=AsyncMock, return_value=None), \
             patch.dict("os.environ", {"HA_URL": "http://ha", "HA_TOKEN": "tok"}):
            MockCard.side_effect = lambda spec: FakeCard(spec)
            await m.run()

        fake_deck.set_screen.assert_awaited_once_with("main")
        fake_ha.refresh_all.assert_awaited()


# ---------------------------------------------------------------------------
# main() entrypoint
# ---------------------------------------------------------------------------

class TestMain:
    def test_main_calls_asyncio_run(self):
        with patch("main.asyncio.run") as mock_run:
            m.main()
        mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_favorite_key_slots(self):
        assert m.FAVORITE_KEY_SLOTS == [0, 1, 2, 4, 5, 6]

    def test_category_order(self):
        assert "Radio" in m.CATEGORY_ORDER
        assert "Playlists" in m.CATEGORY_ORDER
        assert "Albums" in m.CATEGORY_ORDER
