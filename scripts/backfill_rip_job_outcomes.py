"""
Backfill rip_jobs.outcome / dvdbackup_outcome / makemkv_outcome /
dvdbackup_seconds / makemkv_seconds / iso_size_bytes from existing
rip_log_events rows and ISO files on disk.

Usage:
    DISCRIPPER_ENV=prod python3 scripts/backfill_rip_job_outcomes.py [--dry-run]

Rows that already have outcome set are skipped.  Pass --force to overwrite them.

After running, use the query printed at the end to check how many jobs ended
up with outcome=unknown (the caller decides whether to investigate further).
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from common.config import load_config as get_config, get_db_url
from common.models import DiscType, JobStatus, RipJob, RipLogEvent


# ── Log-event matching ────────────────────────────────────────────────────────

def _events_for_job(session, job):
    """Return log events that fall within this job's time window."""
    if job.started_at is None:
        return []
    # Give a 10-minute buffer after completed_at to catch late-written events.
    end = job.completed_at
    if end is None:
        return []
    from datetime import timedelta
    buf_end = end + timedelta(minutes=10)
    return session.scalars(
        select(RipLogEvent)
        .where(
            RipLogEvent.disc_id == job.disc_id,
            RipLogEvent.occurred_at >= job.started_at,
            RipLogEvent.occurred_at <= buf_end,
        )
        .order_by(RipLogEvent.occurred_at)
    ).all()


# ── Outcome derivation ────────────────────────────────────────────────────────

def _derive(job, events, datastore_root):
    """
    Returns a dict of fields to set.  Any field that cannot be determined
    is left out (stays None / unknown).
    """
    result = {}

    # ── outcome ──────────────────────────────────────────────────────────────
    if job.status.value == "cancelled":
        result["outcome"] = "cancelled"
        result["dvdbackup_outcome"] = "unknown"
        result["makemkv_outcome"] = "unknown"
        return result

    disc = job.disc
    if disc is None:
        result["outcome"] = "unknown"
        return result

    is_cd = disc.type == DiscType.cd if disc.type else False

    if job.status.value == "error":
        result["outcome"] = "failed"
    elif job.status.value == "done":
        result["outcome"] = disc.rip_quality or "unknown"
    else:
        result["outcome"] = "unknown"

    # ── dvdbackup_outcome / makemkv_outcome ───────────────────────────────────

    if is_cd:
        result["dvdbackup_outcome"] = "not_run"
        result["makemkv_outcome"] = "not_run"
    else:
        by_type = {}
        for ev in events:
            by_type.setdefault(ev.event_type, []).append(ev)

        dvdbackup_failed_events = by_type.get("dvdbackup_failed", [])
        is_timeout = any("Timed out" in (e.detail or "") for e in dvdbackup_failed_events)
        is_dvd_failed = bool(dvdbackup_failed_events) and not is_timeout
        is_dvd_dirty = bool(by_type.get("dvdbackup_dirty"))
        is_dvd_protected = bool(by_type.get("dvdbackup_protected"))
        had_makemkv_fallback = bool(by_type.get("makemkv_fallback"))
        had_makemkv_completed = bool(by_type.get("makemkv_completed"))
        had_makemkv_failed = bool(by_type.get("makemkv_failed"))

        if is_dvd_protected:
            # HandBrake scan found protection — dvdbackup was never run as a ripper
            result["dvdbackup_outcome"] = "not_run"
        elif is_timeout:
            result["dvdbackup_outcome"] = "timed_out"
        elif is_dvd_failed:
            result["dvdbackup_outcome"] = "failed"
        elif is_dvd_dirty:
            result["dvdbackup_outcome"] = "dirty"
        elif job.status.value == "done" and not had_makemkv_fallback:
            # Clean dvdbackup (no dvdbackup-specific events = it ran cleanly)
            result["dvdbackup_outcome"] = "clean"
        elif job.status.value == "done" and had_makemkv_fallback and had_makemkv_completed and not had_makemkv_failed:
            # Fallback happened and MakeMKV succeeded — dvdbackup had some issue
            # We don't know exactly which, but it wasn't a clean run
            result["dvdbackup_outcome"] = "unknown"
        else:
            result["dvdbackup_outcome"] = "unknown"

        if had_makemkv_fallback or is_dvd_protected:
            if had_makemkv_completed and not had_makemkv_failed:
                result["makemkv_outcome"] = "clean"
            elif had_makemkv_failed:
                result["makemkv_outcome"] = "failed"
            else:
                result["makemkv_outcome"] = "unknown"
        else:
            result["makemkv_outcome"] = "not_run"

    # ── timing ────────────────────────────────────────────────────────────────

    if not is_cd and job.started_at and job.completed_at:
        by_type = {}
        for ev in events:
            by_type.setdefault(ev.event_type, []).append(ev)

        fallback_events = sorted(by_type.get("makemkv_fallback", []), key=lambda e: e.occurred_at)
        fallback_at = fallback_events[0].occurred_at if fallback_events else None

        dvo = result.get("dvdbackup_outcome", "unknown")
        if dvo not in ("not_run",):
            end_of_dvdbackup = fallback_at or job.completed_at
            result["dvdbackup_seconds"] = (end_of_dvdbackup - job.started_at).total_seconds()

        if fallback_at:
            result["makemkv_seconds"] = (job.completed_at - fallback_at).total_seconds()

    # ── iso_size_bytes ────────────────────────────────────────────────────────

    if disc and disc.raw_path and datastore_root and result.get("outcome") in ("clean", "dirty"):
        disc_label = disc.volume_label or f"disc_{disc.id}"
        iso_path = Path(datastore_root) / disc.raw_path / f"{disc_label}.iso"
        if iso_path.is_file():
            result["iso_size_bytes"] = iso_path.stat().st_size

    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Print changes but don't write to DB")
    parser.add_argument("--force", action="store_true", help="Overwrite already-populated outcome fields")
    args = parser.parse_args()

    cfg = get_config()
    datastore_root = cfg["storage"]["datastore_root"]
    engine = create_engine(get_db_url(cfg))
    Session = sessionmaker(bind=engine)
    session = Session()

    jobs = session.scalars(select(RipJob)).all()
    total = len(jobs)
    skipped = 0
    updated = 0
    unknowns = 0

    for job in jobs:
        if job.outcome is not None and not args.force:
            skipped += 1
            continue

        events = _events_for_job(session, job)
        fields = _derive(job, events, datastore_root)

        if not fields:
            skipped += 1
            continue

        changes = []
        for k, v in fields.items():
            current = getattr(job, k)
            if current != v:
                changes.append(f"  {k}: {current!r} → {v!r}")
                if not args.dry_run:
                    setattr(job, k, v)

        if changes:
            print(f"Job {job.id} (disc {job.disc_id}, status={job.status.value}):")
            for c in changes:
                print(c)
            updated += 1
        else:
            skipped += 1

        if fields.get("outcome") in ("unknown", None):
            unknowns += 1

    if not args.dry_run:
        session.commit()

    session.close()
    engine.dispose()

    print()
    print(f"Total jobs: {total}  Updated: {updated}  Skipped/unchanged: {skipped}  outcome=unknown: {unknowns}")
    if args.dry_run:
        print("(dry run — no changes written)")
    print()
    print("Read-only query to count unknowns on prod:")
    print("""
  SELECT
    COUNT(*)                                                        AS total_non_cancelled,
    COUNT(*) FILTER (WHERE outcome IS NULL OR outcome = 'unknown') AS outcome_unknown,
    COUNT(*) FILTER (WHERE dvdbackup_outcome IS NULL
                        OR dvdbackup_outcome = 'unknown')          AS dvdbackup_unknown,
    COUNT(*) FILTER (WHERE makemkv_outcome IS NULL
                        OR makemkv_outcome = 'unknown')            AS makemkv_unknown
  FROM rip_jobs
  WHERE status != 'cancelled';
""")


if __name__ == "__main__":
    main()
