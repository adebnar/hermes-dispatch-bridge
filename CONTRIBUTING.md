# Contributing to Hermes Dispatch Bridge

Thanks for your interest! Early-stage, open-source.

## Setup
```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
uv run ruff check .
uv run pytest -q
```

## Ground rules
- **Keep logic testable.** Pure pieces (cron classification, SSE parsing, push
  mapping) live in dependency-free modules with unit tests. Anything you can
  test without a network, you should.
- **The bridge is a trust boundary** — see [SECURITY.md](SECURITY.md). Never log
  or commit secrets; never weaken auth defaults.
- **Stable mobile contract.** `/v1` is what the Android app depends on; evolve it
  additively and bump the path version for breaking changes.
- **Style:** `ruff` (lint + import order); type hints throughout; Conventional
  Commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`).

## Pull requests
Keep them focused; describe what changed and how you verified it. CI (ruff +
pytest) must pass. Contributions are licensed under [GPL-3.0](LICENSE).
