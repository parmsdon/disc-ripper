# Deployment / Setup Guide

This covers setting up the **dev** environment on both machines. The same
steps apply to **prod**, substituting `/projects/ripper`, `prod.yaml`, the
prod Postgres DB, and `/mnt/datastore`.

---

## 0. Prerequisites (both machines)

```bash
sudo dnf install -y git
```

---

## 1. App/DB machine

### System packages

```bash
sudo dnf install -y python3 python3-pip python3-psycopg2 postgresql postgresql-server nodejs npm
sudo postgresql-setup --initdb
sudo systemctl enable --now postgresql
```

### Create the dev database/user

```bash
sudo -u postgres psql -c "CREATE USER discripper WITH PASSWORD 'changeme';"
sudo -u postgres psql -c "CREATE DATABASE discripper_dev OWNER discripper;"
```

(For prod: `discripper_prod`, and use a different password — update
`config/prod.yaml` accordingly.)

### Get the code

```bash
cd /projects
git clone <your-github-url> ripperdev
cd ripperdev
```

### Python environment

> **Note:** `psycopg2-binary` fails to build on newer Fedora/Python. Install
> `python3-psycopg2` via dnf (above) and create the venv with
> `--system-site-packages` so pip-installed packages can use it.

```bash
python3 -m venv venv --system-site-packages
source venv/bin/activate
pip install -r requirements.txt
```

### Config

```bash
cp config/dev.yaml.example config/dev.yaml
# Edit config/dev.yaml:
#   - database.host/user/password to match what you created above
#   - storage.datastore_root: /mnt/datastoredev
#   - drives: leave as-is for now (only relevant on ripper machine)
```

### Run migrations

```bash
export DISCRIPPER_ENV=dev
alembic upgrade head
```

### Seed reference data (encode profiles)

```bash
python3 scripts/seed.py
```

### Run the Flask API

```bash
export DISCRIPPER_ENV=dev
python3 api/app.py
```

API listens on `http://0.0.0.0:5000` (configurable in `config/dev.yaml`).
Check `http://localhost:5000/api/ping`.

### Run the frontend (separate terminal/tmux pane)

```bash
cd /projects/ripperdev/frontend
npm install
npm run dev
```

Frontend dev server runs on port 5173 and proxies `/api` to `localhost:5000`.
Open `http://<app-db-host>:5173`.

### My Movies sync (app/db machine only)

The sync connects to the My Movies 5 SQL Server database over FreeTDS, so
it runs on the app/db machine (it needs Postgres access, not the optical
drives).

```bash
sudo dnf install -y freetds python3-pyodbc
```

> **Note:** `pyodbc` must come from the system package (`python3-pyodbc`
> via dnf), not pip — same `--system-site-packages` venv constraint as
> `psycopg2`.

Create `/etc/freetds.conf` (encryption must be off — this SQL Server 2014
instance running on Windows Server 2008 doesn't support FreeTDS's default
encryption negotiation):

```ini
[global]
    tds version = 7.3
    client charset = UTF-8
    encryption = off

[mymovies]
    host = 192.168.2.4
    port = 11598
    tds version = 7.3
    client charset = UTF-8
    encryption = off
```

The `[mymovies]` section name is **load-bearing** — `mymovies_sync/connector.py`
connects via `SERVERNAME=mymovies`, not an inline host/port, because this
FreeTDS ODBC driver build ignores the connection string's `PORT` keyword
and otherwise falls back to the default port 1433. Do not rename this
section without updating `connector.py` to match.

Fill in `mymovies:` in `config/dev.yaml` (server/port/database/username/
password, plus `sync_interval_hours`) — see `config/dev.yaml.example`.
`server`/`port` are for documentation/diagnostics only; the live
connection's actual host/port comes from the `[mymovies]` freetds.conf
stanza above.

Run a one-off sync to confirm connectivity:

```bash
export DISCRIPPER_ENV=dev
python3 -m mymovies_sync.sync
```

Run the scheduler as a long-running background process (like
`ripper_service.main` — under `tmux`/`screen` or a process supervisor,
`Ctrl+C` to stop):

```bash
export DISCRIPPER_ENV=dev
python3 -m mymovies_sync.scheduler
```

---

## 2. Ripper machine

### System packages

```bash
sudo dnf install -y python3 python3-pip python3-psycopg2 dvdbackup genisoimage cdparanoia cd-discid libdiscid regionset eject

# libdvdcss via RPM Fusion (needed for dvdbackup to read commercial DVDs)
sudo dnf install -y https://download1.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm
sudo dnf install -y libdvdcss
```

### Get the code

```bash
cd /projects
git clone <your-github-url> ripperdev
cd ripperdev
```

### Python environment

> **Note:** `psycopg2-binary` fails to build on newer Fedora/Python. Install
> `python3-psycopg2` via dnf (above) and create the venv with
> `--system-site-packages` so pip-installed packages can use it.

```bash
python3 -m venv venv --system-site-packages
source venv/bin/activate
pip install -r requirements-ripper.txt
# discid and musicbrainzngs are installed via pip (libdiscid system package above
# provides the shared library that discid wraps):
pip install discid musicbrainzngs
```

### Config

```bash
cp config/dev.yaml.example config/dev.yaml
# Edit config/dev.yaml:
#   - database.host: <app-db-machine-hostname-or-ip>
#   - database.user/password: discripper / changeme (same as created above)
#   - storage.datastore_root: /mnt/datastoredev (NFS mount, must exist)
#   - storage.scratch_dir: /tmp/discripper_scratch (local disk)
#   - drives: list the actual /dev/srX devices for this machine, marked
#     for this environment only
```

### Verify NFS mount

```bash
mount | grep datastoredev
# If not mounted, mount it per your NFS server config before continuing
```

### Seed drives into the DB

Run this once Postgres is reachable from this machine:

```bash
export DISCRIPPER_ENV=dev
python3 scripts/seed.py
```

### Running the ripper service

```bash
cd /projects/ripperdev   # must run from the project root so `common`/`ripper_service` resolve
export DISCRIPPER_ENV=dev
python3 -m ripper_service.main
```

Runs in the foreground, polling every 3 seconds. For now it only:
- Syncs each configured drive's hardware identity (via `udevadm`) into the
  `drives`/`physical_drives` tables, so drives are recognized across device
  path reassignment.
- Detects disc insert/removal per drive and logs it. Drives whose region is
  unknown are logged but otherwise skipped — read the region from the
  Drive Status tab ("Read Region") before ripping will activate for that
  drive in a later phase.
- Does **not** yet create `RipJob`s or run `dvdbackup`/`cdparanoia` — that's
  the next phase.

Run it under `tmux`/`screen` or a process supervisor for now (no systemd
unit yet). `Ctrl+C` shuts it down cleanly.

---

## 3. Claude Code (both machines)

```bash
curl -fsSL https://claude.ai/install.sh | bash
cd /projects/ripperdev
claude
```

First run opens a browser to authenticate. Run `/init` once inside Claude
Code to generate a `CLAUDE.md` for that machine's role.

---

## 4. Setting up prod (later)

Once dev is working end-to-end:

```bash
cd /projects
git clone <your-github-url> ripper
cd ripper
git checkout main
```

Then repeat the steps above on each machine using:
- `config/prod.yaml` (copied from `prod.yaml.example`)
- `discripper_prod` database
- `/mnt/datastore` (prod NFS mount)
- `DISCRIPPER_ENV=prod`
- Different drives assigned to prod in `config/prod.yaml`

---

## Notes

- `config/*.yaml` (real configs) are gitignored — never commit credentials.
- DB schema changes go through Alembic (`alembic revision --autogenerate -m "..."`,
  then `alembic upgrade head` on each environment).
- `git pull` + `alembic upgrade head` + restart services is the rollout
  process for picking up changes on an existing environment.
