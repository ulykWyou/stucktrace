# Changelog

## 0.1.1 — 2026-11
- New panic signal `identical_rerun`: the same code re-run right after a failure (decorator calls excluded).
- `%stucktrace reset` magic to start a fresh session.
- Hermes: `HERMES_TIMEOUT` env var; one retry on network/5xx errors, no retry on 4xx.
- Hints for `FileNotFoundError`, `StopIteration`, `OverflowError`, `NotImplementedError`, `TabError` (EN/RU).
- Docs: JupyterHub screenshots, Team section, updated contributing guide.

## 0.1.0 — 2026-11
- Session tracker with Jupyter cell hooks (`%load_ext stucktrace`, `start_session()`), `@track` decorator / context manager, `mark_confusion`, `log_viz`.
- Trajectory Compressor: error-signature clustering, 5 stuck/panic signals, panic score, error→fix transitions, run-length digest.
- Adapters: Moodle (attempt JSON / CSV), OPT_Mentor (trace import), Hermes (OpenAI-compatible chat API) with offline fallback.
- Student Self-Reflection and Instructor Diagnostic reports (EN/RU) with spoiler guard.
- CLI: `stucktrace demo | report | compress | moodle`.
