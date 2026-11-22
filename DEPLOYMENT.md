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
sudo dnf install -y python3 python3-pip python3-psycopg2 python3-pyodbc freetds postgresql postgresql-server nodejs npm
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
  - device: /dev/sr_drv01
    label: "Drive 1"
    active: true
  - device: /dev/sr_drv02
    label: "Drive 2"
    active: true
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

Runs in the foreground, polling every 3 seconds. It:
- Syncs each configured drive's hardware identity (via `udevadm`) into the
  `drives`/`physical_drives` tables, so drives are recognized across device
  path reassignment.
- Detects disc insert/removal per drive. Drives whose region is unknown are
  skipped — read the region from the Drive Status tab ("Read Region") first.
- Creates `RipJob`s and runs `dvdbackup`+`mkisofs` (DVD) or `cdparanoia`
  per track (CD) up to `max_rippers` concurrency.

`Ctrl+C` shuts it down cleanly. For production use, run as a systemd
service — see section 4.

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

## 4. Production deployment

**Machines:**
- `ripper` (192.168.0.23) — app/DB machine: PostgreSQL, Flask API (gunicorn), encoder service, nginx
- `rimmer` — ripper machine: ripper service only

Both run Fedora, code at `/projects/ripper`, venv at `/projects/ripper/venv`, services run as user `ripper`.

---

### 4.1 First-time setup (app/DB machine — ripper)

The setup script (`scripts/setup_prod.sh app`) handles venv, PostgreSQL, migrations,
frontend build, nginx, systemd units, and sudoers. The steps below must be done
manually first — they require either root privileges before the user is set up, or
configuration the script cannot generate for you.

#### Prerequisites

**HandBrakeCLI** (RPM Fusion, manual):

```bash
sudo dnf install -y \
  https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm \
  https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm
sudo dnf install -y HandBrake
```

**/etc/freetds.conf** (My Movies sync):

Create `/etc/freetds.conf` with a `[mymovies]` section — see section 1
(My Movies sync) for the full stanza. The setup script preflight checks for
this section and refuses to proceed if it is absent.

**NFS mount** (`/etc/fstab`):

```
<nfs-server>:/path/to/export  /mnt/datastore  nfs  defaults,_netdev  0  0
```

```bash
sudo mkdir -p /mnt/datastore
sudo mount -a
```

**Clone the repo and create config:**

```bash
cd /projects
git clone <your-github-url> ripper
cd ripper
cp config/prod.yaml.example config/prod.yaml
# Edit config/prod.yaml:
#   - database.password: set the real password
#   - mymovies.password, discogs.token: fill in real values
#   - drives: leave as [] (drives are on rimmer)
```

#### Run the setup script

```bash
cd /projects/ripper
./scripts/setup_prod.sh app
```

The script: checks preflight → installs dnf packages → creates venv + pip install →
initialises PostgreSQL, creates the role/database, sets `listen_addresses = '*'` and
the `pg_hba.conf` line for 192.168.0.0/24 → runs `alembic upgrade head` and
`scripts/seed.py` → builds and deploys the frontend → installs nginx conf and
systemd units → installs the sudoers rule → runs a health check and prints the UI URL.
Every step is idempotent; re-run safely at any time.

---

### 4.2 First-time setup (ripper machine — rimmer)

#### Prerequisites

**NFS mount** (`/etc/fstab`):

```
<nfs-server>:/path/to/export  /mnt/datastore  nfs  defaults,_netdev  0  0
```

```bash
sudo mkdir -p /mnt/datastore
sudo mount -a
```

**Clone the repo and create config:**

```bash
cd /projects
git clone <your-github-url> ripper
cd ripper
cp config/prod.yaml.example config/prod.yaml
# Edit config/prod.yaml:
#   - database.host: 192.168.0.23 (app/DB machine)
#   - database.password: set the real password
#   - storage.datastore_root: /mnt/datastore
#   - drives: uncomment and list /dev/sr_drv02 ... /dev/sr_drv09
```

**Seed drives** (run once Postgres is reachable from this machine):

```bash
export DISCRIPPER_ENV=prod
source venv/bin/activate 2>/dev/null || true
python3 scripts/seed.py
```

#### Run the setup script

```bash
cd /projects/ripper
./scripts/setup_prod.sh ripper
```

The script: checks preflight (verifies each configured drive device exists) →
creates venv + pip install → installs the systemd unit → installs the sudoers rule →
runs a health check and prints recent journal lines.

---

### 4.3 Deploying updates

From the relevant host, run:

```bash
./scripts/deploy_prod.sh app     # on ripper  — API + encoder
./scripts/deploy_prod.sh ripper  # on rimmer  — ripper service
```

The script does: `git pull` → `pip install` → (app only: `alembic upgrade head`, `npm ci && npm run build`, copy to `/var/www/discripper`) → `systemctl restart` (api + encoder + mymovies for app; ripper for ripper).

---

### 4.4 Database backups

A nightly backup runs via systemd timer at 02:00 on the app/DB machine.

**Backup location:** `/mnt/datastore/backup/discripper_YYYY-MM-DD.dump`
(custom pg_dump format, `-Fc`). Three most recent dumps are retained;
older ones are deleted automatically after each successful run.

**Run a backup by hand:**

```bash
cd /projects/ripper
DISCRIPPER_ENV=prod ./scripts/backup_db.sh
```

**Check timer status and next scheduled run:**

```bash
sudo systemctl status discripper-backup.timer
sudo systemctl list-timers discripper-backup.timer
```

**Check the last backup run:**

```bash
sudo journalctl -u discripper-backup.service -n 20 --no-pager
```

**Restore from a dump:**

```bash
# Stop services that hold connections first
sudo systemctl stop discripper-api discripper-encoder discripper-mymovies

PGPASSWORD=<password> pg_restore \
    --host=localhost \
    --username=discripper \
    --dbname=discripper \
    --clean \
    --if-exists \
    /mnt/datastore/backup/discripper_YYYY-MM-DD.dump

sudo systemctl start discripper-api discripper-encoder discripper-mymovies
```

---

### 4.6 Service management

```bash
# Status
sudo systemctl status discripper-api discripper-encoder discripper-mymovies   # on ripper
sudo systemctl status discripper-ripper                                        # on rimmer

# Logs
sudo journalctl -u discripper-api -f
sudo journalctl -u discripper-encoder -f
sudo journalctl -u discripper-mymovies -f
sudo journalctl -u discripper-ripper -f
```

---

## 5. Holly (Samba + Plex)

Holly is a separate Fedora machine with Docker installed. It hosts two containers
per environment — a Samba file server and a Plex Media Server — serving the
read-only generated library that Discripper writes to its NFS datastore.

**Machines and repos:**
- `holly` — this machine; `/projects/ripper` (prod), `/projects/ripperdev` (dev)
- Library paths: prod `/data2/datastore/library`, dev `/data1/datastoredev/library`
- Plex state on SSD: prod `/data3/projects/ripper`, dev `/data3/projects/ripperdev`

All containers use the existing `staticnet` Docker network with static IPs:

| Env  | Container             | IP              |
|------|-----------------------|-----------------|
| prod | discripper-samba-prod | 192.168.0.230   |
| prod | discripper-plex-prod  | 192.168.0.231   |
| dev  | discripper-samba-dev  | 192.168.0.240   |
| dev  | discripper-plex-dev   | 192.168.0.241   |

**Container images:**
- **Samba:** `ghcr.io/servercontainers/samba:a3.24.4-s4.23.8-r0` (Alpine
  3.24.4, Samba 4.23.8; last changelog entry 2026-07-11). Configuration is
  entirely via environment variables — no separate smb.conf needed. The Samba
  password is passed via `--env-file` so it does not appear in `ps` or in
  Docker's command/args fields. `up` runs `testparm -s` after start and
  removes the container immediately if the config fails to load.
- **Plex:** `plexinc/pms-docker` — the official Plex image.

---

### 5.1 Prerequisites

Verify the `staticnet` network exists before starting containers:

```bash
docker network inspect staticnet
# If absent: docker network create --driver=bridge --subnet=192.168.0.0/24 staticnet
# (Confirm the subnet with whoever manages holly's Docker setup first.)
```

Create the Plex config directory on holly's SSD (do this for each environment
you plan to run). The transcode directory is created automatically by holly.sh.

```bash
# Prod
mkdir -p /data3/projects/ripper/config
# Dev
mkdir -p /data3/projects/ripperdev/config
```

---

### 5.2 holly.env and holly.secrets

Each checkout needs two gitignored files. The env file holds all configuration;
the secrets file holds only the Samba password, with restricted permissions.

**holly.env:**

```bash
# Prod (in /projects/ripper)
cp deploy/holly/holly.env.example deploy/holly/holly.env
# Edit:
#   ENV=prod
#   LIBRARY_PATH=/data2/datastore/library
#   SAMBA_IP=192.168.0.230
#   PLEX_IP=192.168.0.231
#   PLEX_CONFIG_PATH=/data3/projects/ripper/config
#   PLEX_TRANSCODE_PATH=/data3/projects/ripper/transcode
#   PLEX_NAME=Discripper

# Dev (in /projects/ripperdev)
cp deploy/holly/holly.env.example deploy/holly/holly.env
# Defaults in the example are already set for dev — adjust as needed.
```

**holly.secrets** (Samba password — never committed):

```bash
cp deploy/holly/holly.secrets.example deploy/holly/holly.secrets
# Edit: set the real Samba password on the ACCOUNT_discripper line.
# Then restrict permissions:
chmod 600 deploy/holly/holly.secrets
# If Docker runs as root, also: sudo chown root deploy/holly/holly.secrets
```

The `ACCOUNT_<username>` variable name must match `SAMBA_USER` in holly.env.
If you change the Samba username, update both files. The secrets file is passed
to the Samba container via `--env-file` so the password is never part of the
container's command line.

---

### 5.3 First start and Plex claim token

The Plex claim token registers the new server to your Plex account. It is
**single-use and expires in roughly 4 minutes**, so fetch it immediately
before running `up`.

1. Open https://www.plex.tv/claim/ in a browser — copy the `claim-XXXX` token.
2. Put it in holly.env: `PLEX_CLAIM=claim-XXXX`
3. Start the containers:

```bash
cd /projects/ripper          # or /projects/ripperdev for dev
./deploy/holly/holly.sh up
```

4. Open `http://192.168.0.231:32400/web` (prod) or `http://192.168.0.241:32400/web`
   (dev) in a browser on the same LAN.
5. Plex will walk you through setup. When it asks for a server name, use
   **Discripper** (prod) or **Discripper Dev** (dev) — this is what appears
   in Plex clients.
6. Add a **Movies** library, source: `/data/plex` (this is the container path
   for `dvd/plex`).
7. After setup, clear `PLEX_CLAIM=` in holly.env — it is ignored on subsequent
   starts but leaving a used token there is harmless.

---

### 5.4 Samba shares

Five read-only shares are served from the library, all requiring credentials:

| Share   | Library path          | Use                         |
|---------|-----------------------|-----------------------------|
| flac    | cd/flac               | Lossless audio (Sonos, Dune)|
| mp3     | cd/mp3                | Compressed audio (Sonos)    |
| iso     | dvd/iso               | Full DVD ISO images          |
| iphone  | dvd/iphone            | iPhone-compatible MP4s      |
| plex    | dvd/plex              | Plex-optimised MP4s/MKVs    |

Username comes from `SAMBA_USER` in holly.env; password from holly.secrets.

By default (`SAMBA_ALLOW_SMB1=false`) the server requires SMB2 or later with
modern authentication. If older Sonos hardware cannot connect, set
`SAMBA_ALLOW_SMB1=true` in holly.env and restart — this lowers the minimum to
SMB1 and permits NTLMv1. Dune players and Windows clients negotiate upward
to SMB2/3 and NTLMv2 regardless of this setting.

---

### 5.5 Connecting Sonos

Sonos discovers music libraries as SMB shares:

1. Open the Sonos app → **Settings** → **System** → **Music Library**.
2. Tap **Add Music Library** (or the `+` icon).
3. Enter the server address: `192.168.0.230` (prod) or `192.168.0.240` (dev).
4. The app should list the available shares. Add **flac** for lossless or
   **mp3** for compressed (Sonos transcodes FLAC to PCM internally — either
   works, FLAC is higher quality).
5. Enter the Samba username (`SAMBA_USER` from holly.env) and the password
   from holly.secrets when prompted.
6. Sonos will index the library. Large collections take a few minutes.

> **If Sonos cannot connect:** older Sonos S1 hardware requires SMB1 and
> NTLMv1 authentication. Set `SAMBA_ALLOW_SMB1=true` in holly.env and run
> `./deploy/holly/holly.sh restart`. Check the logs afterwards to confirm
> `server min protocol = NT1` appears in the generated smb.conf section.

---

### 5.6 Connecting the Dune HD player

Dune HD players browse SMB shares natively:

1. On the Dune remote, navigate to **Network Storage** (or **Files** →
   **Network**).
2. Select **Add network storage** → **Windows network (SMB)**.
3. Enter:
   - Server: `192.168.0.230` (or `.240` for dev)
   - Share: one of `flac`, `mp3`, `iso`, `iphone`, `plex`
   - Username: `SAMBA_USER` from holly.env / Password: from holly.secrets
4. Confirm and save. Repeat for each share you want to browse.

The Dune supports SMB2/NTLMv2, so no special settings are required beyond
what the container already provides.

---

### 5.7 Connecting the Xbox

The Xbox has an official Plex app in the Microsoft Store:

1. Install **Plex** from the Xbox store (search "Plex").
2. Sign in with your Plex account — the Discripper server should appear
   automatically if the Xbox is on the same LAN. If it does not appear, tap
   **More** → **Connect manually** and enter `192.168.0.231:32400` (prod).
3. Browse the Movies library.

---

### 5.8 Day-to-day management

```bash
cd /projects/ripper    # or /projects/ripperdev for dev

./deploy/holly/holly.sh status           # running / stopped / image
./deploy/holly/holly.sh logs             # last 50 lines from both containers
./deploy/holly/holly.sh logs samba       # tail samba logs
./deploy/holly/holly.sh logs plex        # tail plex logs
./deploy/holly/holly.sh pull             # pull latest image versions
./deploy/holly/holly.sh restart          # down then up (re-reads holly.env)
```

The script only touches `discripper-samba-<env>` and `discripper-plex-<env>`
where `<env>` comes from holly.env — it never touches containers from the
other environment.

---

### 5.9 Dev containers

Dev containers are started and stopped by hand — they carry no restart policy
and do not come up after a reboot.

```bash
cd /projects/ripperdev

# Start when you need them
./deploy/holly/holly.sh up

# Stop when done (frees the IPs and ports)
./deploy/holly/holly.sh down
```

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
