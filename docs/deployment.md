# Deployment

A production-oriented guide for running `stanford-town-vue`. For local
development setup, see the top-level `README.md`.

## 1. Build the frontend

From `frontend/`:

```bash
pnpm install
pnpm build
```

This type-checks and produces a static bundle in `frontend/dist/` (HTML, JS, CSS).
Those files are what you serve in production.

## 2. Run the backend

From `backend/`, install the package and apply migrations, then run uvicorn
**without** `--reload` and with an explicit host/port:

```bash
pip install -e .
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

`app/main.py` also runs `alembic upgrade head` on startup, so the explicit
migration step is belt-and-suspenders.

### Single process / one worker only

**Run exactly one uvicorn worker.** The `SimulationManager` keeps in-process
asyncio state — one worker task per running simulation, with per-task pause/stop
events. Multiple workers would each have their own manager and event bus, so
WebSocket clients and run controls would only see whichever worker happened to
handle the request. Scale by running fewer, longer simulations rather than more
workers.

## 3. Serving the built frontend

`app/main.py` mounts static files **only** for `backend/static/` (at `/static`)
and `backend/assets/` (at `/assets`) when those directories exist. It does **not**
currently mount the built `frontend/dist/` SPA bundle, and there is no catch-all
route to serve `index.html`.

For production, put a reverse proxy (nginx, Caddy, etc.) in front:

- Serve `frontend/dist/` as static files at `/`, with an SPA fallback to
  `index.html` for client-side routes.
- Proxy `/api`, `/ws`, `/static`, and `/assets` to the uvicorn process on
  `:8000`. (These are the same paths the Vite dev proxy forwards.)

Note that the backend's CORS middleware is configured for `localhost` /
`127.0.0.1` origins only. If the SPA is served from a different production
hostname, the CORS allow-list in `app/main.py` must be widened — or, preferably,
the reverse proxy serves the SPA and the API from the same origin so CORS does
not apply.

Mounting `frontend/dist/` directly from FastAPI is a reasonable future
improvement but is not implemented today.

## 4. Environment variables that matter in production

All backend settings use the `STT_` env prefix (or a `backend/.env` file). See
`config/settings.py`.

- **`STT_SECRET_KEY_PATH`** (default `~/.stanford-town-vue/secret.key`) — the
  Fernet key used to encrypt LLM profile API keys. **This file must be stable and
  persisted across restarts and deploys.** If it is lost or regenerated, every
  existing encrypted LLM profile key becomes permanently unreadable and those
  profiles must be recreated. Back it up, and on ephemeral/containerized hosts
  mount it from durable storage (or supply it via a path that points at a
  persistent volume).
- **`STT_DATABASE_URL`** (default `sqlite:///./data/stanford_town.db`) — the
  SQLite database URL. The default path is relative to the process working
  directory; in production pin it to an absolute path on a persistent volume.
- **`STT_ASSETS_DIR`** (default `assets`, relative to `backend/`) — the bundled
  maze / character / persona assets directory. The relative default resolves
  against the `backend/` package root regardless of the working directory; only
  override it if assets live elsewhere.
- **`STT_LOGS_DIR`** (default `~/.stanford-town-vue/logs`) — loguru writes
  `backend.log` here with rotation (10 MB) and retention (14 days).
- **`STT_FRONTEND_DEV_ORIGIN`** (default `http://localhost:5173`) — added to the
  CORS allow-list; relevant only if a separate-origin frontend must call the API
  directly.

## 5. SQLite file location and backups

With the default `STT_DATABASE_URL`, the database lives at `backend/data/stanford_town.db`
(the `data/` directory is gitignored). It is the **single source of truth** for
all simulation state — personas, memory, steps, movements, LLM call logs, and
encrypted LLM profiles.

Back it up regularly. The safest approach is to back up while the process is idle
(no running simulations), or use SQLite's online backup / `.backup` mechanism;
copying the file mid-write can capture an inconsistent snapshot. Note that losing
the secret key (section 4) makes the encrypted profile keys in this database
unrecoverable even if the database file itself is intact.

Simulations can also be exported to the original Stanford Town JSON format via
`POST /api/sims/{id}/export`, which is a useful portable archive format
independent of the SQLite file.
