# Story 11.10: Export estático servido por la FastAPI

Status: done

## Story

As a **operaciones / dev de la consola**,
I want **que la UI corra ligera dentro del Router**,
so that **no haga falta un runtime Node pesado en el Raspberry Pi**.

## Acceptance Criteria

1. La UI (Next.js) se compila con **`output: 'export'`** (estático: HTML/JS/CSS), sin SSR ni route handlers de Next.
2. La **mini-API FastAPI** (Story 11.1) **sirve** ese bundle estático (un solo proceso en el Pi, sin Node corriendo).
3. Todo el acceso a datos es **client-side fetch** a la API local.
4. La consola abre correctamente en `http://localhost:<puerto>` servida por la FastAPI.

## Tasks / Subtasks

- [ ] **Task 1: Configurar export estático** (AC: #1, #3)
  - [ ] `next.config` con `output: 'export'`; mover cualquier lógica server-side a la API local
- [ ] **Task 2: Servir estático desde FastAPI** (AC: #2, #4)
  - [ ] Montar el directorio exportado como estáticos en la FastAPI
- [ ] **Task 3: Verificación en Pi** (AC: #4)

## Dev Notes

- Evita correr `next start` (Node) en el RPi (pesado). El bundle estático + FastAPI = un proceso ligero.
- Depende de las pantallas (11.7–11.9) y de 11.1.

## References

- [Source: epic-11-consola-local-router.md#Story 11.10]

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Code)

### Completion Notes List

- `next.config.js` uses `output:'export'` (+ `images.unoptimized`,
  `trailingSlash`) → static `out/`. FastAPI mounts `cfg.console.static_dir` at
  `/` (StaticFiles, `html=True`); the API stays usable when the bundle is
  absent. Single Python process on the Pi, no Node runtime. User confirmed the
  export build works.

### File List

- `src/web/local_api.py` (static UI mount at /)
- (GTI_satelites) `next.config.js`
- `scripts/deploy-console.sh` (build + atomic deploy of out/)
