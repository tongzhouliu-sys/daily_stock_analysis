# Repository Knowledge Graph Snapshot

This directory is reserved for **static, contributor-facing knowledge graph assets**.
Files here must stay reviewable in Git, must not change runtime behavior, and must not require
additional build, CI, or deployment steps.

## What this snapshot helps with

- Finding the main entrypoints and run modes quickly
- Understanding how `api/`, `apps/`, `bot/`, `data_provider/`, `src/`, and `strategies/` fit together
- Estimating which surfaces are affected when one module changes
- Mapping common automation paths such as local runs, GitHub Actions, and Docker

## Entrypoints and run modes

| Surface | Primary entry | What it does |
|--------|---------------|--------------|
| Daily analysis / CLI | `main.py` | Runs stock analysis, market review, scheduling, and service startup modes |
| FastAPI service | `server.py`, `api/` | Exposes HTTP APIs, auth, history, task, and asset routes |
| Core orchestration | `src/core/`, `src/services/` | Coordinates analysis flow, report assembly, notifications, and business services |
| Data adapters | `data_provider/` | Normalizes market/news/fundamental inputs from multiple providers with fallback behavior |
| Strategy layer | `strategies/`, `src/agent/` | Encapsulates strategy prompts, agent skills, and decision helpers |
| Bot integrations | `bot/` | Connects command handling and notifications to chat platforms |
| Web workspace | `apps/dsa-web/` | React/Vite frontend for analysis, history, settings, backtest, and portfolio |
| Desktop app | `apps/dsa-desktop/` | Electron shell around the web workspace plus desktop-specific bridge APIs |
| Automation / cloud runs | `.github/workflows/`, `scripts/`, `docker/` | CI, scheduled analysis, packaging, and deployment paths |

## Directory collaboration

```text
data_provider/ -> src/services/ + src/core/
src/core/ -> src/reports/ -> notification senders / persistence
api/ -> src/services/ + src/repositories/ -> frontend clients
apps/dsa-web/ -> api/ HTTP endpoints
apps/dsa-desktop/ -> apps/dsa-web/ build output + desktop bridge
bot/ -> src/services/ + strategy/agent helpers
.github/workflows/ / docker/ / scripts/ -> main.py / server.py / static assets
```

## Impact hints for contributors

| If you change... | Usually re-check... |
|------------------|---------------------|
| `data_provider/` | Provider fallback order, normalized fields, timeouts, downstream analysis assumptions |
| `src/core/` or `src/services/` | Report generation, notification flow, API responses, scheduling behavior |
| `src/schemas/` or API payloads | FastAPI endpoints, Web/Desktop compatibility, history/report rendering |
| `apps/dsa-web/` | API contracts, auth/task polling, static build output, desktop embedding |
| `apps/dsa-desktop/` | Web build artifacts, preload bridge, release packaging |
| `bot/` | Command routing, notification formatting, service contracts |
| `.github/workflows/`, `scripts/`, `docker/` | CI expectations, local scripts, release and deployment paths |

## Accepted scope for future `.understand-anything/` updates

Pull requests that touch this directory are acceptable when they:

1. Add or refresh static knowledge graph artifacts for contributor understanding only
2. Stay confined to `.understand-anything/` plus at most minimal documentation links
3. Avoid source-code, dependency, workflow, Docker, and runtime behavior changes
4. Keep content textual, reviewable, and free of secrets, binaries, or environment-specific paths
5. Document the generator or source of truth when regeneration details are relevant
