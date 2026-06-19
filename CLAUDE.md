# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Flask API
```bash
export DISCRIPPER_ENV=dev
python3 api/app.py
# Listens on 0.0.0.0:5000; check http://localhost:5000/api/ping
```

### React frontend (separate terminal)
```bash
cd frontend
npm install   # first time only
npm run dev   # dev server on port 5173, proxies /api to localhost:5000
```

### Database migrations
```bash
export DISCRIPPER_ENV=dev
alembic upgrade head          # apply all pending migrations
alembic downgrade -1          # roll back one migration
alembic revision --autogenerate -m "description"   # generate new migration
```

### Seed reference data
```bash
export DISCRIPPER_ENV=dev
python3 scripts/seed.py
# Idempotent — safe to re-run. Seeds encode_profiles and drives from config.
```

## Architecture

### Two-machine deployment
- **Ripper machine**: runs `ripper_service` (not yet implemented). Optical drives are physically attached here. Drives are configured per-environment in `config/<env>.yaml` (`drives:` list).
- **App/DB machine**: runs Postgres, the Flask API, React frontend, `encoder_service`, and `mymovies_sync`. The `drives:` list in its config should be empty (`drives: []`).
- Shared NFS storage: `/mnt/datastoredev` (dev) and `/mnt/datastore` (prod).

### Environment / config
- `DISCRIPPER_ENV` env var (`dev` or `prod`) selects `config/<env>.yaml`. Defaults to `dev`.
- Config files are gitignored — copy from `config/<env>.yaml.example`. The loader validates that the `environment:` field inside the YAML matches the requested env, guarding against copy-paste misconfiguration.
- `common/config.py` is the single config loader used by all services. `get_db_url()` and `get_store_path()` build derived values from the loaded config dict.
- `psycopg2` is provided by the system package `python3-psycopg2` (not pip). Venvs must be created with `--system-site-packages` so pip packages can see it. `psycopg2-binary` fails to build on newer Fedora/Python.

### Flask API (`api/`)
- `api/app.py` wires up Flask, CORS, a scoped SQLAlchemy session (`DB_SESSION`), and registers blueprints. The session is stored on `current_app.config["DB_SESSION"]` and cleaned up via `teardown_appcontext`.
- Route modules (`api/routes/`) each define a Blueprint. Routes access the DB session with `current_app.config["DB_SESSION"]()`.
- All blueprints are prefixed `/api/<resource>/`.
- No auth — trusted LAN only.

### SQLAlchemy models (`common/models.py`)
- All models in one file. Postgres enums are defined as Python `str` enums and used as SQLAlchemy `Enum` columns.
- **Disc** is the central model: links to `Drive`, `Catalog`, `CDTrack`, `LookupCandidate`, `RipJob`, and `EncodeJob` via relationships.
- `disc_fingerprint`: CDDB/MusicBrainz disc ID for CDs; Volume ID/Volume Set ID for DVDs. Used for dedup and re-rip detection.
- `needs_rerip` on `Disc`: set true if any CD track came back imperfect/failed, so a re-rip can be offered on next disc insertion.

### Alembic migrations (`api/migrations/`)
- `alembic.ini` points `script_location = api/migrations`.
- `api/migrations/env.py` loads the DB URL from `common/config.py` at migration time, so `DISCRIPPER_ENV` drives which database is targeted.
- Migrations use `PGEnum(..., create_type=False)` for all enum columns and call `.create(bind, checkfirst=True)` / `.drop(bind, checkfirst=True)` explicitly in `upgrade`/`downgrade`. This is required for Postgres — SQLAlchemy won't auto-create `CREATE TYPE` inside `op.create_table`.

### React frontend (`frontend/`)
- Vite + React 18 + react-router-dom v6. No state management library.
- All API calls go through `frontend/src/api/client.js`. Vite proxies `/api` to `localhost:5000` in dev; in prod the Flask server serves both.
- Tab layout in `App.jsx`; each tab is a page component in `frontend/src/pages/`.
- `package-lock.json` is gitignored — `npm install` regenerates it.

### Services (not yet implemented)
- `ripper_service/`: will poll drives, detect disc insertions, create `RipJob`s and execute `dvdbackup` (DVD → ISO via local scratch) or `cdparanoia` (CD → WAV per track).
- `encoder_service/`: will pick up completed rip jobs and run encode jobs per `EncodeProfile`.
- `mymovies_sync/`: will sync the My Movies SQL Server catalog (ODBC) into the `catalog` table.

### Storage layout
```
/mnt/datastore(dev)/
├── dvd_store/
│   ├── raw/{disc_id}/             # ISO files
│   └── {encode_profile}/{disc_id}/
└── cd_store/
    ├── raw/{disc_id}/track01.wav ...
    └── {encode_profile}/{disc_id}/
```
`encode_profiles.output_subfolder` is the directory name under each store root, so new encoding targets don't require code changes.

## Development phases

- **Phase 1 (done)**: schema, Flask API, React skeleton.
- **Phase 2 (next)**: DVD ripper (`dvdbackup` → ISO), CD ripper (`cdparanoia` → WAV), CDDB/MusicBrainz lookup, candidate selection UI.
- **Phase 3**: audio encoder (WAV → MP3/FLAC), DVD content extraction (main title heuristic → MP4/MKV).
- **Future**: symlink trees from metadata for library browsing.
