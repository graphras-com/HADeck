# AGENTS.md

## Project overview

StreamDeck+ client for Home Assistant. Single-file Python app (`main.py`) that drives a StreamDeck+ via the `deckboard` library and connects to Home Assistant via `ha_client`.

## Setup

- Python 3.11, managed with **uv**
- Install: `uv sync`
- Two git-sourced dependencies (see `[tool.uv.sources]` in `pyproject.toml`):
  - `deckboard` → github.com/graphras-com/Deckboard
  - `ha_client` → github.com/graphras-com/HAClient
- Requires a `.env` file with `HA_URL` and `HA_TOKEN` (loaded via `python-dotenv`)

## Running

```
uv run main.py
```

Needs a physical StreamDeck+ connected. No test suite exists.

## Repo structure

- `main.py` — sole entrypoint; all app logic lives here
- `*.dsui/` — UI packages for the StreamDeck touchscreen/keys. Each contains `manifest.yaml`, `layout.svg`, and optional `assets/`. Loaded at runtime by `deckboard.load_package()`.
- `IconKey.dsui/` and `LightCard.dsui/` exist but are not currently used in `main.py`

## Conventions

- No linter, formatter, or type checker is configured
- No tests
- `.env` is gitignored; never commit credentials
