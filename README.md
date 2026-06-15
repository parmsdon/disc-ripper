# Disc Ripper

Internal tool to rip a DVD/CD collection (DVDs → ISO, CDs → WAV), encode to
configurable formats later (FLAC/MP3, MP4/MKV), and track everything in a
Postgres database with a React frontend.

## Architecture

- **Ripper machine** (Fedora, multiple optical drives): runs `ripper_service`,
  one instance per environment (dev/prod), each managing the drives assigned
  to it in `config/<env>.yaml`.
- **App/DB machine** (Fedora, separate for dev and prod): runs Postgres,
  `encoder_service`, the Flask API (`api/`), the React frontend
  (`frontend/`), and `mymovies_sync`.
- Shared storage via NFS: `dvd_store/raw/{id}/` for ISOs,
  `cd_store/raw/{id}/trackNN.wav` for CD rips. Dev and prod use separate NFS
  mounts (`/mnt/datastoredev` and `/mnt/datastore`).

## Repo layout

```
config/             # gitignored real configs; *.yaml.example are templates
common/             # shared config loader + SQLAlchemy models
api/                # Flask API + Alembic migrations
ripper_service/     # runs on the drive/ripper machine
encoder_service/    # runs on the app/db machine
mymovies_sync/      # periodic ODBC sync from My Movies (SQL Server)
frontend/           # React (Vite) frontend
scripts/            # seed/maintenance scripts
docs/               # design notes
```

## Environments

Both `dev` and `prod` run as fully separate deployments:

- Same git repo, deployed as two copies of `main`:
  `/projects/ripperdev` (dev) and `/projects/ripper` (prod)
- Separate Postgres databases, separate NFS mounts, separate config files
- `DISCRIPPER_ENV` env var (`dev` or `prod`) selects `config/<env>.yaml`

See `DEPLOYMENT.md` for setup steps.
