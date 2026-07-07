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

# Audio/video encoding tools (required by encoder_service)
sudo dnf install -y ffmpeg flac

# HandBrakeCLI — requires RPM Fusion (free + nonfree) for the HandBrake package
sudo dnf install -y \
  https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm \
  https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm
sudo dnf install -y HandBrake
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
# genisoimage provides mkisofs (ISO build) and isoinfo (DVD region detection/patching)

# RPM Fusion (free + nonfree) — needed for libdvdcss and HandBrake
sudo dnf install -y \
  https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm \
  https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm
sudo dnf install -y libdvdcss

# HandBrakeCLI — required for copy protection pre-scan (scan_for_copy_protection()
# runs HandBrakeCLI --scan before dvdbackup to detect ARccOS and similar schemes)
sudo dnf install -y HandBrake
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

### Stable optical drive device names (udev)

Linux assigns `/dev/sr0`, `/dev/sr1`, … non-deterministically at boot: the
order depends on which drive the kernel initialises first and can change
after a reboot or kernel update. udev rules create persistent symlinks
(`/dev/sr_drv01` etc.) tied to physical port (`ID_PATH`) so each drive
always gets the same name regardless of boot order.

**Discover your drive paths:**

```bash
for dev in /dev/sr*; do
  echo "=== $dev ==="
  udevadm info --query=all --name=$dev | grep -E "ID_PATH=|ID_MODEL=|ID_SERIAL="
done
```

Note the `ID_PATH` value for each drive. Assign numbers top-to-bottom (or
left-to-right) to match the physical layout of your enclosure.

**Create the rules file:**

```bash
sudo nano /etc/udev/rules.d/99-optical-drives.rules
```

One line per drive — substitute the `ID_PATH` value for that physical
position:

```
SUBSYSTEM=="block", ENV{ID_CDROM}=="1", ENV{ID_PATH}=="<path>", SYMLINK+="sr_drv01"
```

**Example — this installation (9-drive enclosure):**

```
SUBSYSTEM=="block", ENV{ID_CDROM}=="1", ENV{ID_PATH}=="pci-0000:01:00.0-ata-2.3.0", SYMLINK+="sr_drv01"
SUBSYSTEM=="block", ENV{ID_CDROM}=="1", ENV{ID_PATH}=="pci-0000:01:00.0-ata-2.1.0", SYMLINK+="sr_drv02"
SUBSYSTEM=="block", ENV{ID_CDROM}=="1", ENV{ID_PATH}=="pci-0000:01:00.0-ata-2.4.0", SYMLINK+="sr_drv03"
SUBSYSTEM=="block", ENV{ID_CDROM}=="1", ENV{ID_PATH}=="pci-0000:01:00.0-ata-2.0",   SYMLINK+="sr_drv04"
SUBSYSTEM=="block", ENV{ID_CDROM}=="1", ENV{ID_PATH}=="pci-0000:01:00.0-ata-2.2.0", SYMLINK+="sr_drv05"
SUBSYSTEM=="block", ENV{ID_CDROM}=="1", ENV{ID_PATH}=="pci-0000:02:00.0-ata-2.1.0", SYMLINK+="sr_drv06"
SUBSYSTEM=="block", ENV{ID_CDROM}=="1", ENV{ID_PATH}=="pci-0000:02:00.0-ata-2.2.0", SYMLINK+="sr_drv07"
SUBSYSTEM=="block", ENV{ID_CDROM}=="1", ENV{ID_PATH}=="pci-0000:02:00.0-ata-2.0",   SYMLINK+="sr_drv08"
SUBSYSTEM=="block", ENV{ID_CDROM}=="1", ENV{ID_PATH}=="pci-0000:02:00.0-ata-2.3.0", SYMLINK+="sr_drv09"
```

**Reload and verify:**

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
ls -la /dev/sr_drv*
```

You should see symlinks like `/dev/sr_drv01 -> sr2` (the `srN` target will
vary; that's fine — the symlink is what matters).

**Update config** to use the stable names in the `drives:` list:

```yaml
drives:
  - device_path: /dev/sr_drv01
  - device_path: /dev/sr_drv02
  # … through sr_drv09
```

> **Note:** rules are tied to physical port layout and must be recreated
> if drives are moved to different SATA ports or a different HBA.

### Config

```bash
cp config/dev.yaml.example config/dev.yaml
# Edit config/dev.yaml:
#   - database.host: <app-db-machine-hostname-or-ip>
#   - database.user/password: discripper / changeme (same as created above)
#   - storage.datastore_root: /mnt/datastoredev (NFS mount, must exist)
#   - storage.scratch_dir: /tmp/discripper_scratch (local disk)
#   - drives: list /dev/sr_drv01 … /dev/sr_drv09 (stable symlinks, see above)
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

- **Discogs API token**: A personal access token is required for Discogs
  fallback lookups (used when MusicBrainz returns a fuzzy multi-disc match with
  unknown disc position). Generate one at
  https://www.discogs.com/settings/developers and add it to your config:
  ```yaml
  discogs:
    token: "your_token_here"
  ```
  Without a token, Discogs lookup is silently skipped — MB-only identification
  still works.

- `config/*.yaml` (real configs) are gitignored — never commit credentials.
- DB schema changes go through Alembic (`alembic revision --autogenerate -m "..."`,
  then `alembic upgrade head` on each environment).
- `git pull` + `alembic upgrade head` + restart services is the rollout
  process for picking up changes on an existing environment.
