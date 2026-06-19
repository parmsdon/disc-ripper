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

---

## 2. Ripper machine

### System packages

```bash
sudo dnf install -y python3 python3-pip python3-psycopg2 dvdbackup genisoimage cdparanoia

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

### Ripper service

Not yet implemented (next phase). Will run via:

```bash
export DISCRIPPER_ENV=dev
python3 -m ripper_service.main
```

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
