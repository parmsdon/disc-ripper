# Design Notes

Captures key decisions from initial planning, for context when picking up
development (especially useful for Claude Code's `/init`).

## Scope / phases

1. **Phase 1 (current)**: repo skeleton, DB schema, Flask API, React tab
   layout — done. Next: DVD ripper (dvdbackup → ISO), CD ripper
   (cdparanoia → WAV).
2. **Phase 2**: configurable audio encoder (WAV → MP3/FLAC), CDDB/MusicBrainz
   metadata lookup with candidate selection UI.
3. **Phase 3**: DVD content extraction — identify main movie title (longest
   VTS by duration heuristic) or series episodes, transcode to configurable
   video formats (MP4/MKV), subtitle/audio track selection. Initial focus on
   movies (one disc per catalog entry); series/multi-disc later.
4. **Future**: build symlink trees from metadata (human-readable names) for
   other frontends to consume the library.

## Environments

- `dev` and `prod` are fully separate: separate Postgres DBs, separate NFS
  mounts (`/mnt/datastoredev` vs `/mnt/datastore`), separate config files,
  separate ripper service instances (each only manages drives assigned to
  it in config).
- Same git repo deployed as two copies of `main`
  (`/projects/ripperdev` and `/projects/ripper`).
- Same machine type (Fedora) for ripper and app/db machines; trusted LAN, no
  encryption between services.

## Media type detection

At rip time, the ripper service detects what kind of disc is inserted using:

```bash
udevadm info --query=all --name=/dev/srX | grep ID_CDROM
```

Key udev properties: `ID_CDROM_DVD=1` indicates a DVD, `ID_CDROM_CD=1` indicates a CD.
`drives.drive_type` is only a capability hint (nullable) — it can be used to exclude a drive
from DVD or CD jobs if it's known to be CD-only, but is not required. Omitting `type` in config
leaves it NULL and the drive is eligible for both media types.

## DVD region detection

- `regionset <device>` (piped "n" to stdin - we only ever read, never set)
  reports the region(s) a drive is *capable* of playing, not a single
  fixed value. Region-locked drives report one digit (e.g. "2");
  region-free or multi-region drives report several, e.g. "Drive plays
  discs from this region(s): 1 2 3 4 5 6 7 8".
- Because of this, `PhysicalDrive.region` and `Disc.ripped_in_region` are
  stored as a space-separated digit string, not a single integer.
  `ripped_in_region` is a snapshot of the drive's supported regions at
  rip time, since the drive's region could theoretically be changed later
  even though we discourage it.
- Future region mismatch detection should check whether the disc's region
  is a *member* of the drive's supported region list (substring/token
  membership on the space-separated digits), not an exact match.

## DVD ripping

- Use `dvdbackup` (handles CSS decryption via libdvdcss, more resilient to
  bad sectors than raw `dd`) to copy VIDEO_TS/AUDIO_TS to local scratch
  (`/tmp`), then `mkisofs -dvd-video` to build the ISO, written to
  `dvd_store/raw/{disc_id}/`.
- Capture the disc's Volume ID / Volume Set ID as `disc_fingerprint` for
  dedup/matching.

## CD ripping

- `cdparanoia` for bit-accurate WAV extraction with error correction.
- Capture CDDB/MusicBrainz disc ID as `disc_fingerprint`.
- Per-track `rip_quality` (good/imperfect/failed) from cdparanoia output. If
  any track is imperfect/failed, set `discs.needs_rerip = true`. When a disc
  with a matching fingerprint and `needs_rerip = true` is reinserted, offer
  to re-rip (ideally just the affected tracks).

## Metadata

- CDDB/MusicBrainz lookups often return multiple candidates — store all of
  them in `lookup_candidates`, let the user pick via the Data Editing tab.
  Selection populates `discs.album_title`/`album_artist` and `cd_tracks`.
- Compilations: `album_artist = "Various"`, per-track `artist` set
  individually.
- DVDs: My Movies (SQL Server via ODBC) is the source of truth for the movie
  catalog. `mymovies_sync` periodically pulls into the `catalog` table.
  When a DVD is ripped, the user maps the `disc` to a `catalog` entry via the
  Data Editing tab. One catalog entry can map to multiple discs (re-rips,
  special editions).

## Storage layout

```
/mnt/datastore(dev)/
├── dvd_store/
│   ├── raw/{disc_id}/             # .iso
│   └── <encode_profile>/{disc_id}/  # later phases
└── cd_store/
    ├── raw/{disc_id}/track01.wav...
    └── <encode_profile>/{disc_id}/  # later phases (flac, mp3_320, ...)
```

`<encode_profile>` subfolder names come from `encode_profiles.output_subfolder`
so adding a new encoding target doesn't require code changes.

## UI tabs

- **Drive Status**: one section per configured drive, current job/progress.
- **DVD Encoders** / **CD Encoders**: active/queued encode jobs (Phase 2/3).
- **DB Health**: library counts (DVDs, CDs, tracks), unmatched DVDs, discs
  needing re-rip, etc. — expand over time.
- **Data Editing**: match ripped DVDs to My Movies catalog entries, resolve
  CDDB/MusicBrainz candidates for CDs, edit track/album metadata.

## Open items for later

- Ripper service implementation (drive detection, job queue worker).
- Encoder service implementation.
- MusicBrainz/CDDB client integration.
- ODBC connector setup for My Movies sync (SQL Server).
- Symlink tree generation from metadata.
