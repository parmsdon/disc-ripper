# Rip Workflow Spec

This document is the source of truth for disc rip behaviour and drive card UI.
Changes to either must match what is described here.

---

## Workflow

### Disc insertion and identity check

1. Ripper detects media present. Reads `disc_fingerprint` (DVD: `ifo:<md5[:16]>` of
   VIDEO_TS.IFO with region byte zeroed; CD: CDDB-style hash) and `volume_label`
   (ISO 9660 Volume ID).

2. Fingerprint is looked up against existing `discs` rows:
   - **Unknown disc** → create new `Disc` row, queue rip job.
   - **Known disc, clean** → do nothing (eject or leave; no re-rip).
   - **Known disc, dirty or failed** → auto-queue a new rip job (re-rip from start).
   - **Known disc, cancelled** → treat as dirty/failed: auto-queue a new rip job (rip from start).

### DVD rip decision tree

```
Protected scan (HandBrakeCLI)?
├── YES (copy-protected)
│   └── MakeMKV
│       ├── success → Clean ISO  [method: MakeMKV · protected]
│       └── fail    → Failed     [method: MakeMKV · protected]
│           (no dvdbackup fallback for protected discs)
└── NO
    └── dvdbackup
        ├── clean success → build ISO → Clean   [method: dvdbackup]
        ├── dirty success → MakeMKV fallback?
        │   ├── configured → MakeMKV
        │   │   ├── success → Clean ISO          [method: MakeMKV · fallback]
        │   │   └── fail    → build ISO from
        │   │                 dirty dvdbackup content → Dirty  [method: dvdbackup]
        │   └── not configured → build ISO → Dirty             [method: dvdbackup]
        └── fail → MakeMKV fallback?
            ├── configured → MakeMKV
            │   ├── success → Clean ISO          [method: MakeMKV · fallback]
            │   └── fail    → Failed             [method: MakeMKV · fallback]
            └── not configured → Failed          [method: dvdbackup]
```

### Keep-best-result rule

When a re-rip fails (any path → `_fail_job`) **or is cancelled**:
- If `rip_quality = dirty` **and** the prior dirty ISO file still exists on disk
  → keep `status = ripped / rip_quality = dirty` (preserve the usable ISO).
- If `rip_quality = dirty` but no ISO file on disk
  → clear `rip_quality`, set `status = error`.
- If the rip was cancelled and no usable ISO exists
  → `status = cancelled` (see Cancelled outcome below).

### Cancelled outcome

A job is **cancelled** (not errored) when it is stopped for a reason unrelated to the
disc content itself:

- The disc is ejected or swapped before the job starts (Rules 1 & 2 in the safety rules).
- The user presses the **Cancel** button on the drive card while ripping.
- A rollback cancels a job whose disc is no longer in the drive (Rule 3).
- A fingerprint mismatch aborts the job at start (Rule 4).

**`rip_jobs.status = 'cancelled'`** (a distinct value, not `'error'`).

**`disc.status` after cancellation — keep-best-result applies:**
- If the disc already has a usable rip (`rip_quality IN ('clean','dirty')` and
  an ISO on disk) → keep disc status and ISO unchanged; only the job is cancelled.
- Otherwise → `disc.status = 'cancelled'`.

**Cancelled disc behaviour:**
- Shown as a grey **"Not ripped"** pill (same shade as the Clean / Queued chip, not red).
- Re-inserting a cancelled disc starts a fresh rip from the start (same as error/dirty).
- Does **not** appear in the Identification queue.
- **Excluded from all success/failure statistics** (Health, Audit, future Drive Health tab).

### Encode behaviour

Encode jobs are created at rip completion as now, but the encoder must not start a job
whose disc is not locked (`name_confirmed = false`). Imported discs are always
`name_confirmed = true` and unaffected.

At lock time (see Name lock below): the ISO in `dvd_store/raw/<disc_id>/` is renamed
to `{sanitised_temp_name}.iso` and `disc.raw_path` is updated. Only then may the encoder
start. Encode output folder names are fixed at encode-start time and never renamed.

A better re-rip (clean replaces dirty, or any re-rip succeeds): prior encode jobs are
invalidated (output files deleted), fresh encode jobs are queued.

---

## Drive card

### Region badge and read action

A neutral grey **`REGION: N`** badge shows the **drive's** region setting (which disc
regions the drive can play). It is **always visible** on every drive card regardless of
whether a disc is inserted. When the drive region has not yet been read it shows
**`REGION: ?`**. When the drive is set to all eight regions (stored as `12345678`) it
shows **`REGION: ALL`**.

A small read-region button appears next to the badge **only when ripping is stopped**
(`ripping_enabled = false`). It is hidden while ripping is enabled.

- Label is **"Read region"** when the drive region is unknown, **"Re-read region"** when
  a region is already known.
- Disabled with tooltip "Insert a disc to read the drive region" when the drive is empty,
  because reading the drive region requires a disc to be present.
- While the pending action is in progress (`pending_action = read_region`) a **"Reading…"**
  label replaces the button (shown regardless of `ripping_enabled`).
- The button queues a `read_region` pending action picked up by the ripper service on its
  next poll; once the region is known the button returns to its normal (non-pending) state.

### Disc label

The disc line shows **`#<id> · <temp_name>`**. The `volume_label` and `disc_fingerprint`
appear in the tooltip on hover.

### Status pill

| Disc state | Pill label | Colour |
|---|---|---|
| `status = queued` | Reading | light orange |
| `status = ripping` or `building` | Ripping | light orange |
| `status = error` or `protected` | Failed | light red |
| `rip_quality = dirty` (ripped/encoding/done) | Dirty | amber |
| clean ripped/encoding/done | Clean | light grey |

The **media-type badge** (DVD / CD) uses the same colour as the status pill beside it.

### Method pill

Shown for every state except `queued` (method not yet determined).
dvdbackup is **blue**; both MakeMKV variants are **green**.

`rip_method` is set to `"makemkv"` by `_set_rip_reason` as soon as a MakeMKV attempt
begins, so it persists on the disc even if MakeMKV fails. The pill condition is therefore:

| Condition | Pill label | Tooltip |
|---|---|---|
| `rip_method = makemkv` AND `rip_reason = protected` | MakeMKV · protected | "Disc is copy-protected; ripped with MakeMKV" |
| `rip_method = makemkv` AND `rip_reason = dirty` | MakeMKV · fallback | "dvdbackup reported read errors; re-ripped with MakeMKV" |
| `rip_method = makemkv` AND `rip_reason = dvdbackup_failed` | MakeMKV · fallback | "dvdbackup failed; re-ripped with MakeMKV" |
| otherwise | dvdbackup | "Ripped with dvdbackup" |

### Working-title input

The input is pre-filled with `disc.temp_name` if set, otherwise `disc.volume_label`.

**States:**
- Input text unchanged, `name_confirmed = false` → **Confirm** button, active (blue).
- Input text edited (differs from saved name) → **Save** button, active (same blue).
  Editing also immediately unconfirms on the server (`name_confirmed = false`).
- After Confirm or Save (`name_confirmed = true`, input unchanged, rip not yet complete)
  → **Confirm** disabled/greyed. Stays greyed until text is edited again.

**Name lock** — when `name_confirmed = true` **AND** `status ∈ {ripped, encoding, done}`:
- Input becomes read-only; button disabled.
- At the moment of lock: the ISO is renamed to `{sanitised_temp_name}.iso`, and the
  encoder is unblocked (see Encode behaviour above).
- If the rip completes while the name is not confirmed (never confirmed, or an unsaved
  edit is pending): the user gets one final Confirm/Save, after which it locks.

Same lock behaviour in the Identification queue for discs ejected unconfirmed.

### Action buttons

- **Cancel** (shown during `ripping` / `building` only).
- No other buttons on the drive card.

### Catalogue actions

Catalogues have no rip actions. The only action on a disc row is **Delete**.
Re-ripping happens by re-inserting the disc; the ripper service detects the
dirty/failed state and queues a new rip job automatically.

### Eject

Manual only — no auto-eject after rip completion.

---

## Identification order

Identification is a two-step process for ripped discs. Imported discs skip step 1.

### Identification queue membership

The queue contains every disc row where:
- Status is **not** one of: `queued`, `ripping`, `building`, `cancelled`.
- AND the disc is unmatched (`catalog_id IS NULL` for DVD, `album_title IS NULL` for
  CD) or its name is not yet confirmed.

**Failed discs (`error`, `protected`) are included.** A matched failed disc has no ISO,
so library generation skips it, no encode jobs are created, and "never ripped" / missing
title reports still count it as missing. When the disc is later re-ripped successfully it
keeps its catalog match and proceeds to encode as normal.

Failed discs show a red **"Failed"** pill in the queue so they stand out. Discs with no
temp\_name fall back to `volume_label`, then `disc_<id>` for display.

**Cancelled discs are excluded** (shown as "Not ripped"; re-inserting the disc starts a
fresh rip with no existing catalog match).

The **DVDs Unmatched** / **CDs Unidentified** counts on the Health tab equal the number
of DVD / CD rows in the identification queue.

### Step 1 — Name confirmation (ripped discs only)

The working title must be confirmed and locked before a disc can be matched to My Movies.

- **On the drive card** while the disc is still inserted: Confirm or Save triggers
  the lock (if the rip is complete).
- **In the Identification tab** if the disc was ejected before confirmation:
  appears flagged "Name not confirmed"; the same input + Confirm/Save + lock behaviour
  is available.

Imported ISOs are pre-confirmed (`name_confirmed = true`) because their name is
derived from the ISO filename and there is no drive card to confirm on.

### Step 2 — My Movies match

After name lock (or for imported discs, immediately), the disc appears in the
Identification tab with an **Identify** button that opens the DvdIdentifyPanel
to match it to a My Movies catalog entry.

Discs already matched (`catalog_id IS NOT NULL`) do not appear in the queue.

---

## Rip job safety rules

A rip job may only run against the disc it was created for.  Four rules enforce
this invariant:

### Rule 1 — drive-association check at job start

Before `start_eligible_rip_jobs` promotes a queued job to running it verifies
`disc.drive_id == rip_job.drive_id`.  If the disc's `drive_id` is `None` (ejected)
or points to a different drive, the job is set to `cancelled`; `disc.status` follows
the keep-best-result rule (cancelled if no usable ISO).

### Rule 2 — cancel queued jobs on ejection

When the main detection loop detects that a disc has been removed from a drive it
cancels any queued (not-yet-started) jobs for that disc before committing the
`drive_id = NULL` update.  Cancelled jobs are set to `status = cancelled`.  Disc
status follows the keep-best-result rule.

### Rule 3 — cancel rather than re-queue if disc ejected during rollback

When a running job is rolled back (ripper stopped, tray opened, `max_rippers`
reduced) the rollback checks whether the disc is still physically in the drive
(`disc.drive_id IS NOT NULL`).

- **Still in drive** → job and disc reset to `queued` as before.
- **Not in drive** → job set to `cancelled`; disc follows keep-best-result rule.

### Rule 4 — fingerprint check at the moment of ripping

At the very start of `_run_job` (DVD) and `_run_cd_job` (CD), before any
filesystem I/O, the service reads the actual disc fingerprint from the hardware
(`compute_dvd_id` / `read_table_of_contents`) and compares it to
`disc.disc_fingerprint`.

A definite mismatch (both sides non-`None` and unequal) → job set to `cancelled`;
`disc.status` reset to `cancelled` (keep-best-result applies); rip does **not** proceed.

If either fingerprint is `None` (computation failed or not yet stored) the check
is skipped and the rip proceeds — an inconclusive check never blocks a rip.

---

---

## Drive Health tab

Read-only statistics tab showing per-drive rip quality and cross-drive retry analysis.
Each app deployment shows only its own environment's drives (filtered by `drives.env`
matching the server's `DISCRIPPER_ENV`). No env toggle — the app always shows itself.

### Per-job outcome columns on `rip_jobs`

The Drive Health tab is based on first-class columns added in migration 0036:

| Column | Values | Populated by |
|---|---|---|
| `outcome` | `clean / dirty / failed / cancelled / imported` | Ripper service at job completion; backfill script for historical rows |
| `dvdbackup_outcome` | `clean / dirty / timed_out / failed / not_run / unknown` | Ripper service at job completion |
| `makemkv_outcome` | `clean / failed / not_run / unknown` | Ripper service at job completion |
| `dvdbackup_seconds` | float, nullable | Time from job start to MakeMKV fallback (or job end), null if dvdbackup not run |
| `makemkv_seconds` | float, nullable | Time from MakeMKV start to job end, null if MakeMKV not run |
| `iso_size_bytes` | bigint, nullable | `os.path.getsize(iso_path)` on job completion, null for failed or CD jobs |

**`outcome`** is the job-level result, independent of `disc.rip_quality` (the disc's
current best result). A disc that failed on drive A and later ripped clean on drive B
shows `outcome = failed` on the first job and `outcome = clean` on the second.

**Backfill**: run `python3 scripts/backfill_rip_job_outcomes.py` once per environment
to populate historical jobs as far as the `rip_log_events` record allows.  Jobs that
cannot be determined get `outcome = unknown` and are excluded from count totals (but
still increment `total_attempts`).  Imported discs get `outcome = imported` and are
excluded from all counts and rates.  Use `--fix-imports` to correct rows that were
already backfilled before this logic was added.

### Per-drive statistics

Each drive card shows:

| Metric | Source | Notes |
|---|---|---|
| Total attempts | `rip_jobs WHERE outcome NOT IN ('cancelled', 'imported')` | Cancelled and imported excluded everywhere |
| Clean | `rip_jobs.outcome = clean` | |
| Dirty | `rip_jobs.outcome = dirty` | |
| Failed | `rip_jobs.outcome = failed` | |
| Timed out | `rip_jobs.dvdbackup_outcome = timed_out` | First-class field; not double-counted against Clean |
| Success rate | `(clean + dirty) / total_attempts` | |
| Avg dvdbackup time | `AVG(dvdbackup_seconds)` WHERE `outcome = clean AND dvdbackup_outcome = clean` | |
| Avg MakeMKV time | `AVG(makemkv_seconds)` WHERE `outcome = clean AND makemkv_outcome = clean` | |
| dvdbackup read speed | `AVG(iso_size_bytes / 1 MiB / dvdbackup_seconds)` for clean dvdbackup jobs | Null until `iso_size_bytes` is populated |
| MakeMKV read speed | `AVG(iso_size_bytes / 1 MiB / makemkv_seconds)` for clean MakeMKV jobs | Null until `iso_size_bytes` is populated |
| Last 10 outcomes | Most-recent 10 jobs with `outcome IN (clean, dirty, failed)` | Colour-coded strip, newest first |
| Failed here, OK elsewhere | Discs with `outcome IN (dirty, failed)` on this drive AND a later `outcome = clean` job on another drive | Strongest signal of a hardware problem |

### Highlighting

A drive is highlighted (amber left border + ⚠ icon) when:
- It has ≥ 5 attempts, **and**
- Its success rate is ≥ 15 percentage points below the fleet average (computed
  over all drives with ≥ 5 attempts on that env).

No highlighting when there is only one drive (no comparison is possible).

### Cross-drive retries

A table of discs with `outcome IN (dirty, failed)` on one drive that later had
`outcome = clean` on a different drive. One row per disc (most recent failure shown).
Columns: disc title, failed drive, failure date, success drive, success date.

The per-drive "Failed here, OK elsewhere" count tallies the same set of discs
grouped by the failing drive.

### Data not shown

- **CDs**: the drive health tab covers DVD rip jobs only. CD track rip quality
  is tracked in `cd_tracks.rip_quality` but drive-level CD stats are not yet
  aggregated here.
- **encode jobs**: not drive-specific; use the Health tab encode counters.
- **cancelled jobs**: excluded from all counts and rates.
- **imported discs**: jobs with `outcome = imported` are excluded from all counts and
  rates — they were never ripped through a physical drive.
- **deleted discs**: deleting a disc sets `rip_jobs.disc_id = NULL` (FK is ON DELETE
  SET NULL). The rip_job rows are kept with their `drive_id`, `outcome`, and timing
  data intact, so Drive Health counts are not affected by disc deletes.

---

## What is not in the spec

The following do not exist and must not be added without updating this document:

- Additional DiscStatus values used for display logic
- Status pills beyond Reading / Ripping / Clean / Dirty / Failed
- Method pills beyond the three listed above
- Buttons on the drive card beyond Cancel
- Auto-eject after rip completion
- Additional badges on the drive card beyond the region badge
- Rip actions (Retry Rip or similar) in any catalogue view
