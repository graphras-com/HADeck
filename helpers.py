from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

import aiohttp
from PIL import Image

from deckui import DuiKey

log = logging.getLogger(__name__)

PACKAGES_DIR = Path(__file__).parent

SCENES = [
    {"position": 2, "label": "Normal", "icon": "fa-regular:smile-beam"},
    {"position": 3, "label": "Tired", "icon": "fa-regular:tired"},
    {"position": 6, "label": "Cinema", "icon": "mdi:cinema"},
    {"position": 7, "label": "Bedtime", "icon": "icon-park-outline:sleep-two"},
]

FAVORITE_KEY_SLOTS = [0, 1, 4, 5]
CATEGORY_ORDER = {"Radio": 0, "Playlists": 1, "Albums": 2}


async def fetch_image(url: str) -> Image.Image | None:
    """Download an image over HTTP without blocking the event loop."""
    log.debug("fetch_image: %s", url)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return Image.open(BytesIO(await resp.read()))
    except Exception:
        log.exception("Failed to fetch image: %s", url)
    return None


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
            thumb = await fetch_image(fav.thumbnail)
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
