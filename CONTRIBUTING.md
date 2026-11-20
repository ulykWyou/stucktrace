# Contributing & team workflow

## Module ownership (4 members)

| Member | Branch | Owns | Tests |
|---|---|---|---|
| **A — Core / Tracker** | `feat/tracker` | `events.py`, `tracker.py` (Session, Jupyter hooks, magics, `track`) | `tests/test_tracker.py`, Jupyter part of `tests/test_jupyter_and_cli.py` |
| **B — Compressor** | `feat/compressor` | `compressor.py` (clustering, stuck signals, panic score, digest) | `tests/test_compressor.py` |
| **C — Integrations** | `feat/adapters` | `adapters.py` (Moodle, OPT_Mentor, Hermes, offline engine EN/RU) | adapters part of `tests/test_reporter_adapters.py` |
| **D — Reports, CLI, Docs, Release** | `feat/reporter-docs` | `reporter.py`, `cli.py`, `demo.py`, `README.md`, `docs/`, `examples/`, `pyproject.toml`, CI | reporter + CLI tests |

## Commit series (merge order: A → B → C → D)

Each member commits **from their own GitHub account** in small, meaningful steps (Conventional Commits).

**A — `feat/tracker`**
1. `chore: init repo with src layout, pyproject, MIT license, .gitignore`
2. `feat(events): add Event dataclass and JSONL serialization`
3. `feat(tracker): add Session with record_run, edit ratio and fail-safe wrapper`
4. `feat(tracker): add track decorator/context manager that re-raises student errors`
5. `feat(tracker): add IPython pre/post_run_cell hooks and %stucktrace/%confused magics`
6. `test(tracker): cover save/load, autosave, never-break guarantees, IPython shell`

**B — `feat/compressor`** (branch from `main` after A is merged)
1. `feat(compressor): add error signature normalization and clustering`
2. `feat(compressor): detect rapid-fail bursts and repeated errors`
3. `feat(compressor): detect long pauses, rewrites and confusion markers; panic score`
4. `feat(compressor): extract error→fix transitions and visualization effect`
5. `feat(compressor): add run-length digest and to_json/to_markdown/to_prompt views`
6. `test(compressor): add unit tests for every signal and config thresholds`

**C — `feat/adapters`**
1. `feat(adapters): add MoodleAdapter with attempt JSON and CSV export`
2. `feat(adapters): add OPT_Mentor trace import (summarized viz events)`
3. `feat(adapters): add HermesClient (OpenAI-compatible) with HermesError`
4. `feat(adapters): add offline LocalReportGenerator (student + instructor, EN)`
5. `feat(adapters): add Russian templates and error hints`
6. `test(adapters): fake Hermes HTTP server, Moodle and OPT_Mentor tests`

**D — `feat/reporter-docs`**
1. `feat(reporter): add generate_report with Hermes→offline fallback`
2. `feat(reporter): add spoiler guard and Jupyter Markdown rendering`
3. `feat(cli): add stucktrace demo/report/compress/moodle commands`
4. `docs: write README (motivation, innovation, architecture, quickstart)`
5. `docs: add demo notebook, example reports and architecture notes`
6. `ci: add test matrix and PyPI trusted publishing workflow`
7. `chore(release): v0.1.0`

## Rules

- `main` is protected: merge through Pull Requests, at least 1 review from another member.
- `pytest -q` must pass before every PR.
- Rebase on `main` before opening a PR (`git pull --rebase origin main`).
- Release: bump the version in `pyproject.toml` and `src/stucktrace/_version.py`, tag `vX.Y.Z`, push the tag, and CI publishes.
