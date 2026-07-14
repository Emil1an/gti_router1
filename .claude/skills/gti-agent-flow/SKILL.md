---
name: gti-agent-flow
description: >-
  Multi-agent feature workflow for the GTI Router v2.0 edge project. Use when the
  user provides a PLAN_FEATURE.md, types "GTI Agent Flow", or asks to
  design→spec→implement→test a gti-router feature through the Fable/Opus/Sonnet
  agent pipeline with Docker-based unit tests and the Zero-Disk-Write / low-latency
  quality gates. Do NOT use for unrelated repos or one-off edits.
---

# GTI Agent Flow

A staged, subagent-driven pipeline for shipping a feature on the GTI Router
(Raspberry Pi 4/5 edge node, Dockerized, Zero-Disk-Write). Invoking this skill is
the user's standing authorization to spawn subagents for the task.

## Roles

| Stage | Who | Model override | Output |
|-------|-----|----------------|--------|
| **Architect** | You (orchestrator) | — (current model) | Design + coordination, final review |
| **Coordinator** | Subagent | `opus` | `SPEC_<feature>.md` — detailed implementation instructions |
| **Implementer** | Subagent | `sonnet` | Code + passing unit tests inside Docker |

Spawn subagents with the `Agent` tool: `subagent_type: general-purpose` +
`model: opus` (Coordinator) or `model: sonnet` (Implementer). Relay only what
matters from each subagent's final message.

## Pipeline

1. **Intake (Architect).** Read `PLAN_FEATURE.md`. Restate the goal, constraints,
   and acceptance criteria in your own words. Identify the real files in `src/`
   the change touches (search first — never assume). If the plan is ambiguous on
   something that changes the design, ask ONE round of questions; otherwise pick
   the obvious option and proceed.
2. **Spec (Coordinator, opus).** Spawn an Opus subagent to produce
   `SPEC_<feature>.md` from the plan + the architect's notes. The spec must be
   self-contained (file paths, exact function signatures, test cases, the quality
   gates below). Use `templates/SPEC_TEMPLATE.md`.
3. **Implement (Implementer, sonnet).** Spawn a Sonnet subagent with the spec.
   It writes the code, adds/updates unit tests, and runs them **inside the
   container** (`docker build` + tests), reporting pass/fail with output.
4. **Loop.** If tests fail or a quality gate is unmet, the Architect (you)
   triages: either send corrective notes back to a fresh Sonnet spawn, or have
   Opus revise the spec if the gap is design-level. Repeat until green — cap at
   3 implementer iterations, then escalate to the user with the blocker.
5. **Final review (Architect).** You review the diff for correctness, the quality
   gates, layering, and test coverage. Run the full suite once more. Summarize
   what shipped with file links. Never mark done on unverified work.

## Non-negotiable quality gates (enforce every iteration)

These are already the project baseline — new code must not regress them:

- **Zero-Disk-Write.** Snapshots (`last_frame.jpg`) are written to and read from
  tmpfs (`/dev/shm`) in production; the SD card is never touched for frames.
  Location is resolved through `src/utils/frame_store.py` (RAM-first, disk
  fallback for Windows/dev) — writer and reader MUST share it. Never reintroduce
  a disk copy in the snapshot path.
- **FFmpeg edge compression.** Frame extraction uses `-vf scale=-2:480 -q:v 7`
  (target <50 KB) — see `pipeline/snapshot.py::_build_extract_command`.
- **AWS keep-alive + freshness.** S3 uploads reuse ONE persistent aioboto3 client
  (aioboto3 wraps aiobotocore → keep-alive) configured with
  `tcp_keepalive=True`; snapshot PUTs carry a stable key +
  `Cache-Control: max-age=0, no-cache`. See `upload/s3_client.py`.
- **Local API from RAM.** `web/local_api.py` serves `last_frame.jpg` via
  `frame_store.resolve_last_frame(...)` (tmpfs first). No extra SD reads.
- **Degraded-mode contract.** Cloud (S3/Supabase) failures are contained — capture
  and the local console stay alive (`aws.enabled=false` / try-contain in
  `upload/service.py`).
- **Config discipline.** All config through `config.loader.get_config()`; secrets
  only via `${ENV}` expansion (NFR9). New knobs go in `config/schema.py`.
- **Async safety.** Never block the event loop; wrap blocking I/O in
  `asyncio.to_thread`. Subsystems expose `async start()` / `async stop()`.
- **Tests green in Docker.** `pytest` passes inside the container image; if a lib
  is missing from `pyproject.toml`, add it (with a version constraint) before
  building.

## Docker test command (Implementer runs this)

```bash
docker build -t gti-router:test .
docker run --rm --shm-size=128m gti-router:test pytest -q
```
`--shm-size=128m` gives `/dev/shm` headroom so the Zero-Disk-Write path exercises
tmpfs the same way it will in production.

## Guardrails

- Search the real codebase before designing; ground every spec in actual files.
- No commits/pushes unless the user asks (default: keep the working tree).
- Report failures faithfully with the actual test output — never claim green on
  unverified work.
- Templates live in `templates/` next to this file.
