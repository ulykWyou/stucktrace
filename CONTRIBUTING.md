# Contributing & team workflow

## Team and module ownership

| Member | Branch (v0.1.1) | Owns |
|---|---|---|
| 1 — Maintainer | `feat/more-hints` | repo, releases, `pyproject.toml`, error hints in `adapters.py` |
| 2 — Tracker | `feat/reset-magic` | `events.py`, `tracker.py` (Session, Jupyter hooks, magics, `track`) |
| 3 — Compressor | `feat/identical-rerun` | `compressor.py` (clustering, stuck signals, panic score, digest) |
| 4 — Integrations | `feat/hermes-retry` | `HermesClient`, Moodle and OPT_Mentor adapters |
| 5 — Docs & demo | `docs/team-and-screenshots` | `README.md`, `docs/`, `examples/`, `CHANGELOG.md` |

Merge order for v0.1.1: 2 → 1 → 3 → 4 → 5 → release.

## Workflow

```bash
git clone https://github.com/<owner>/stucktrace.git && cd stucktrace
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
git checkout -b <branch>
# ... change code + add tests ...
pytest -q
git commit -m "feat(scope): short description"         # Conventional Commits
git pull --rebase origin main
git push -u origin <branch>                            # then open a Pull Request
```

## Rules

- `main` is protected: changes go through Pull Requests with at least one review.
- Every PR keeps `pytest -q` green and adds tests for new behaviour.
- New stuck signal → weight in `compressor._WEIGHTS`, labels in `adapters._T[lang]["signals"]` and `["detail"]` for EN and RU.
- Release: bump version in `pyproject.toml` and `src/stucktrace/_version.py`, update `CHANGELOG.md`, tag `vX.Y.Z`, push the tag.
