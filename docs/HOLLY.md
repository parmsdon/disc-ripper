# Holly Container Runbook

Holly runs two Docker containers — Samba and Plex — managed by `deploy/holly/holly.sh`.

## Environments

| | Dev | Prod |
|---|---|---|
| Samba IP | 192.168.0.240 | 192.168.0.230 |
| Plex IP | 192.168.0.241 | 192.168.0.231 |
| Library path | `/data1/datastoredev/library` | `/data2/datastore/library` |
| Plex config | `/data3/projects/ripperdev/config` | `/data3/projects/ripper/config` |
| Samba NetBIOS name | `discripperdev` | `discripper` |
| Plex server name | Discripper Dev | DiscRipper |
| Restart policy | `no` | `unless-stopped` |

## First-time prod setup

**1. Create config and env files on holly**

```bash
mkdir -p /data3/projects/ripper/config
cp deploy/holly/holly.env.prod.example deploy/holly/holly.env.prod
# Edit holly.env.prod — values should already be correct for prod; check LIBRARY_PATH
cp deploy/holly/holly.secrets.prod.example deploy/holly/holly.secrets.prod
chmod 600 deploy/holly/holly.secrets.prod
```

**2. Set the Samba password**

Edit `deploy/holly/holly.secrets.prod` and replace `changeme` with the real password:

```
ACCOUNT_discripper=yourpassword
```

**3. Get a Plex claim token (do this immediately before starting)**

The token expires in approximately 4 minutes and is single-use — get it at
`https://www.plex.tv/claim/`, paste it into `holly.env.prod` as `PLEX_CLAIM=claim-xxxxxxx`,
then run `./holly.sh prod up` within that window.

Leave `PLEX_CLAIM=` blank on all subsequent starts; the container ignores it once the server
is already registered.

**4. Pull images and start**

```bash
cd deploy/holly
./holly.sh prod pull
./holly.sh prod up
```

After `up`, browse to `http://192.168.0.231:32400/web` to complete Plex setup and add libraries.

## Adding Plex libraries

Paths inside the container:

| Share | Container path |
|---|---|
| DVD (Plex-ready) | `/data/plex` |

Add a new library in Plex → Libraries → Add Library → Movies (or TV Shows) → Browse for media folder → `/data/plex`.

## Common commands

```bash
./holly.sh prod status            # check container states
./holly.sh prod logs              # tail both containers (last 50 lines each)
./holly.sh prod logs plex         # follow Plex log only
./holly.sh prod logs samba        # follow Samba log only
./holly.sh prod down              # stop and remove both containers
./holly.sh prod restart           # down then up
```

Dev uses the same commands with `dev` in place of `prod`.

## Stopping dev containers if needed

If dev and prod are both running and you need to free an IP or port:

```bash
./holly.sh dev down
```

Dev containers use `restart=no` so they will not come back after a Docker or host restart unless you run `up` again.

## Samba password

The Samba password is stored in `holly.secrets.prod` (prod) or `holly.secrets` / `holly.secrets.dev`
(dev). It is passed to the container via `--env-file` and never appears in `docker inspect` output.

To change the password: edit the secrets file, then `./holly.sh prod restart`.
