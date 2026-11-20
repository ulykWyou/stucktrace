# Architecture notes

## Data flow

1. **Capture** (`tracker.py`). Each student action becomes an `Event`:
   - `run`: from the Jupyter `post_run_cell` hook, `@track`, or `with track()`. Stores ok/error type/message, duration, code hash/length, `edit_ratio` against the previous attempt of the same task (difflib), and an optional code preview.
   - `confusion`: `mark_confusion(text)` / `%confused text`.
   - `viz`: `log_viz(...)` or `OPTMentorAdapter.import_trace(...)`.
   - `task`: `set_task(name)` / `%stucktrace task name`.
2. **Compress** (`compressor.py`): `compress(session, CompressorConfig)` → `CompressedTrajectory`.
3. **Explain** (`reporter.py`): `generate_report(mode, backend, lang)`:
   - `backend="auto"`: Hermes if `HERMES_API_URL` is set, else offline.
   - Any Hermes failure → `LocalReportGenerator`, with `Report.warning` set.
   - `mode="student"` → `spoiler_guard()` strips multi-line code blocks.
4. **Export** (`adapters.py`): Moodle CSV/JSON, raw JSONL (`Session.save`), Markdown (`Report.save`).

## Fail-safe guarantees

| Risk | Mitigation |
|---|---|
| Bug inside stucktrace | `_safe` wrapper on every Session method; hooks catch everything; errors go to the `stucktrace` logger at DEBUG level |
| Student exception swallowed | `track.__exit__` always returns `False` |
| Hermes down / slow | 12 s timeout → offline report |
| Huge sessions | Ring buffer (`max_events=20000`); diff limited to 8 000 chars; prompt capped at `max_chars` |
| Disk full / read-only FS | `save` / autosave failures are ignored |

## Raw event format (JSONL)

```json
{"_session": {"session_id": "1a42…", "started_at": 1700000000.0, "metadata": {"student_id": "s1024"}}}
{"kind": "task", "ts": 1700000000.0, "task": "sum_evens"}
{"kind": "run", "ts": 1700000005.0, "task": "sum_evens", "ok": false, "error_type": "NameError",
 "error_msg": "name 'dat' is not defined", "duration": 0.0001, "code_hash": "9f1c…", "code_len": 92}
{"kind": "confusion", "ts": 1700000170.0, "task": "sum_evens", "text": "why does % not work on my numbers?"}
```

## Panic score

`raw = 1.0·bursts + 0.7·repeated + 0.5·pauses + 0.5·rewrites + 0.9·confusions + 0.1·max(0, failures − 2)`
`score = 1 − exp(−raw / 2)` → low < 0.33 ≤ medium < 0.66 ≤ high.

The weights are heuristics for intro-programming labs and can be changed in `compressor._WEIGHTS`.

## Extending

- **Real Hermes API**: override `HermesClient.complete()` (request shape) and `_extract_text()` (response shape).
- **Real Moodle web service**: add a method to `MoodleAdapter` that POSTs `export_attempt_json()`.
- **New stuck signal**: add it in `_analyze_task`, give it a weight in `_WEIGHTS`, and add labels in `adapters._T[lang]["signals"]` and `["detail"]`.
