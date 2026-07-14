# SPEC_<feature>

> Produced by the Coordinator (Opus) from PLAN_FEATURE.md + Architect notes.
> Must be self-contained: an Implementer (Sonnet) should need nothing else.

## 1. Goal
One paragraph: what this feature does and why (link back to the plan).

## 2. Acceptance criteria
- [ ] AC1 …
- [ ] AC2 …

## 3. Files to touch (real paths)
| File | Change |
|------|--------|
| `src/...` | … |
| `tests/...` | … |

## 4. Design detail
Exact function/class signatures, data shapes, config keys (in `config/schema.py`),
control flow. Note which existing helpers to reuse (e.g. `utils/frame_store.py`,
`config.loader.get_config`).

## 5. Quality gates that apply (from the skill)
List the specific gates in scope for this change (Zero-Disk-Write, keep-alive,
FFmpeg compression, degraded-mode, async safety, config discipline).

## 6. Tests
Concrete cases the Implementer must add/keep green, e.g.:
- unit: `tests/.../test_x.py::test_...`
- behavior on the failure path (disconnected camera / no cloud / missing frame)

## 7. Dependencies
Any new `pyproject.toml` entries (name + version constraint) and why.

## 8. Docker verification
```bash
docker build -t gti-router:test .
docker run --rm --shm-size=128m gti-router:test pytest -q
```

## 9. Out of scope
Explicitly what NOT to change.
