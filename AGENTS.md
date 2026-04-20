# AGENTS.md

## Project

DSUI-Collection: a set of `.dsui` UI packages (keys and touchscreen cards) for a Stream Deck-like device, driven by the `deckboard` and `ha_client` (Home Assistant) libraries.

## Setup

- Python 3.11, managed via **uv** (lockfile: `uv.lock`)
- Install: `uv sync`
- Two git-sourced dependencies in `pyproject.toml` under `[tool.uv.sources]`:
  - `deckboard` → github.com/graphras-com/Deckboard
  - `ha_client` → github.com/graphras-com/HAClient

## Running

- Entry point: `uv run program.py` (async, connects to a physical deck + Home Assistant)
- Requires `.env` with `HA_URL` and `HA_TOKEN`
- `main.py` is a placeholder; `program.py` is the real entry point

## DSUI packages

Each `*.dsui/` directory is a UI component package containing:
- `manifest.yaml` — bindings, events, regions, metadata
- `layout.svg` — visual layout
- Optional asset files (e.g. images)

Types: `TouchStripCard` (AudioCard, LightCard) and keys (IconKey, PictureKey). Loaded at runtime via `deckboard.load_package()`.

## Conventions

- No tests, linter, or CI configured
- `.env` is gitignored; never commit it
