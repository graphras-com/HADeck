from __future__ import annotations

import logging
from pathlib import Path

from deckui import DuiKey
from deckui.render.image_fetch import ImageFetchError, fetch_image

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
            try:
                key.set("picture", fetch_image(fav.thumbnail))
            except ImageFetchError:
                log.warning("Could not load thumbnail for %s", fav.title)

        @key.on_event("click")
        async def _click(item=fav, emitter=key):
            log.info("Play: %s", item.title)
            await emitter.start_busy()
            await item.play()

        screen.set_key(FAVORITE_KEY_SLOTS[idx], key)
        keys.append(key)
    return keys
