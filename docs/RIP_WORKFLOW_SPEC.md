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

When a re-rip fails (any path → `_fail_job`):
- If `rip_quality = dirty` **and** the prior dirty ISO file still exists on disk
  → keep `status = ripped / rip_quality = dirty` (preserve the usable ISO).
- If `rip_quality = dirty` but no ISO file on disk
  → clear `rip_quality`, set `status = error`.

### Encode behaviour

- Encode jobs are queued immediately on rip success (no gate on name confirmation
  or My Movies match). Folder name is `{temp_name}_{disc_id}` or `disc_{disc_id}`
  if no temp_name yet, resolved at encode-start time.
- A better re-rip (clean replaces dirty, or any re-rip succeeds): prior encode
  jobs are invalidated (output files deleted), fresh encode jobs are queued.

---

## Drive card

### Status pill

| Disc state | Pill label | Pill style |
|---|---|---|
| `status = queued` | Reading | amber |
| `status = ripping` or `building` | Ripping | blue |
| `status = error` or `protected` | Failed | red |
| `rip_quality = dirty` (ripped/encoding/done) | Dirty | amber |
| clean ripped/encoding/done | Clean | teal |

### Method pill

Shown for every state except `queued` (method not yet determined).

| Condition | Pill label | Tooltip |
|---|---|---|
| `rip_method = makemkv` AND `rip_reason = protected` | MakeMKV · protected | "Disc is copy-protected; ripped with MakeMKV" |
| `rip_method = makemkv` AND `rip_reason = dirty` | MakeMKV · fallback | "dvdbackup reported read errors; re-ripped with MakeMKV" |
| `rip_method = makemkv` AND `rip_reason = dvdbackup_failed` | MakeMKV · fallback | "dvdbackup failed; re-ripped with MakeMKV" |
| otherwise | dvdbackup | "Ripped with dvdbackup" |

### Working-title input

- Pre-filled with `disc.temp_name` if set, otherwise `disc.volume_label`.
- Shown from insertion (queued) through to `done` — available until the disc is ejected.
One button with three states (always labelled **Confirm** or **Save**):

- Input text unchanged, `name_confirmed = false` → **Confirm**, active (blue).
- Input text edited (differs from saved name) → **Save**, active (same blue as Confirm).
- After Confirm or Save (`name_confirmed = true`, input unchanged) → **Confirm**,
  disabled/greyed. Stays greyed until the text is edited again.

### Action buttons

- **Cancel** (shown during `ripping` / `building` only).
- No other buttons on the drive card. Delete and Retry Rip are in the catalogue pages.

### Eject

Manual only — no auto-eject after rip completion.

---

## Identification order

Identification is a two-step process for ripped discs. Imported discs skip step 1.

### Step 1 — Name confirmation (ripped discs only)

The working title must be confirmed before a disc can be matched to My Movies.

- **On the drive card** while the disc is still inserted: Confirm or Save in the
  working-title input.
- **In the Identification tab** if the disc was ejected before confirmation:
  appears flagged "Name not confirmed"; the same input + Confirm/Save is available.

Imported ISOs are pre-confirmed (`name_confirmed = true`) because their name is
derived from the ISO filename and there is no drive card to confirm on.

### Step 2 — My Movies match

After name confirmation (or for imported discs, immediately), the disc appears in
the Identification tab with an **Identify** button that opens the DvdIdentifyPanel
to match it to a My Movies catalog entry.

Discs already matched (`catalog_id IS NOT NULL`) do not appear in the queue.

---

## What is not in the spec

The following do not exist and must not be added without updating this document:

- Additional DiscStatus values used for display logic
- Status pills beyond Reading / Ripping / Clean / Dirty / Failed
- Method pills beyond the three listed above
- Buttons on the drive card beyond Cancel
- Auto-eject after rip completion
