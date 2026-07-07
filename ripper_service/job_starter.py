"""
Slot-aware rip job starting: gates queued RipJobs behind the global
ripping_enabled control, promotes them to running up to the configured
max_rippers concurrency limit, and spawns the actual dvdbackup/mkisofs
(DVD) or cdparanoia (CD) work in a background thread so the main poll
loop keeps running for other drives/region-reads/ejects.

RipJob.status stays "running" across both the dvdbackup and mkisofs
sub-steps (DVD) or across all tracks processed in one run (CD) - it's
Disc.status that distinguishes "ripping" (copying disc content) from
"building" (constructing the ISO, DVD only) for the UI.
"""

import logging
import os
import shutil
import threading
from pathlib import Path

from sqlalchemy import select

from common.config import get_store_path
from common.encode_queue import create_encode_jobs
from common.models import CDTrack, Disc, DiscStatus, DiscType, JobStatus, RipJob, RipQuality, Setting, naive_utcnow
from ripper_service import active_jobs
from ripper_service.job_rollback import rollback_excess_jobs
from ripper_service.log_writer import write_log_event
from ripper_service.region_patcher import patch_region_if_needed
from ripper_service.rip_worker import run_cdparanoia, run_dvdbackup, run_mkisofs, scan_for_copy_protection

logger = logging.getLogger(__name__)

_MAX_RIPPERS_KEY = "max_rippers"
_DEFAULT_MAX_RIPPERS = 1
_FAKE_RIP_MODE_KEY = "fake_rip_mode"
_FAKE_DIRTY_MODE_KEY = "fake_dirty_mode"
_RIPPING_ENABLED_KEY = "ripping_enabled"
_DEFAULT_RIPPING_ENABLED = False

# fake_dirty_mode only ever injects a simulated read error on this one
# drive, so testing dirty-rip detection doesn't make every fake rip dirty.
_FAKE_DIRTY_DRIVE_LABEL = "Drive 1"


def start_eligible_rip_jobs(session, cfg: dict, session_factory) -> None:
    now = naive_utcnow()

    ripping_enabled = _get_ripping_enabled(session)
    max_rippers = _get_max_rippers(session)
    fake_rip_mode = _get_fake_rip_mode(session)
    fake_dirty_mode = _get_fake_dirty_mode(session)

    if not ripping_enabled:
        # ripping_enabled=false means stopped, full stop - kill/roll back
        # anything already running.
        rollback_excess_jobs(session, 0, cfg)
        return  # defense in depth - don't start anything while stopped

    active_count = _count_running(session)
    if active_count > max_rippers:
        rollback_excess_jobs(session, max_rippers, cfg)
        active_count = _count_running(session)

    queued_jobs = session.scalars(
        select(RipJob)
        .where(RipJob.status == JobStatus.queued)
        .order_by(RipJob.created_at)
    ).all()

    for rip_job in queued_jobs:
        if active_count >= max_rippers:
            break

        disc = rip_job.disc
        drive = rip_job.drive

        if disc is None or drive is None:
            logger.warning("RipJob %s missing disc/drive - skipping", rip_job.id)
            continue

        if drive.tray_open is True:
            logger.info(
                "RipJob %s: drive %s tray is open - skipping until disc reseated",
                rip_job.id, drive.label or drive.device_path,
            )
            continue

        label = drive.label or drive.device_path
        inject_dirty = fake_dirty_mode and drive.label == _FAKE_DIRTY_DRIVE_LABEL

        rip_job.status = JobStatus.running
        rip_job.started_at = now
        disc.status = DiscStatus.ripping
        session.commit()

        write_log_event(session_factory, "rip_started", drive_label=label, disc_id=disc.id, working_title=disc.temp_name)

        active_count += 1

        if disc.type == DiscType.dvd:
            scratch_dir = str(Path(cfg["storage"]["scratch_dir"]) / str(disc.id))
            disc_label = disc.disc_fingerprint or f"disc_{disc.id}"
            thread = threading.Thread(
                target=_run_job,
                args=(
                    rip_job.id, drive.device_path, scratch_dir, disc_label, fake_rip_mode,
                    session_factory, label, cfg, inject_dirty,
                ),
                daemon=True,
            )
        else:
            thread = threading.Thread(
                target=_run_cd_job,
                args=(rip_job.id, drive.device_path, fake_rip_mode, label, cfg, inject_dirty, session_factory),
                daemon=True,
            )

        active_jobs.register(rip_job.id, thread)
        thread.start()

        logger.info(
            "Started rip job %s for disc %s (%s) on %s (fake_rip_mode=%s, inject_dirty=%s)",
            rip_job.id, disc.id, disc.type.value, label, fake_rip_mode, inject_dirty,
        )


def _count_running(session) -> int:
    return len(session.scalars(select(RipJob).where(RipJob.status == JobStatus.running)).all())


def _get_max_rippers(session) -> int:
    setting = session.get(Setting, _MAX_RIPPERS_KEY)
    return int(setting.value) if setting else _DEFAULT_MAX_RIPPERS


def _get_fake_rip_mode(session) -> bool:
    setting = session.get(Setting, _FAKE_RIP_MODE_KEY)
    return setting.value == "true" if setting else False


def _get_fake_dirty_mode(session) -> bool:
    setting = session.get(Setting, _FAKE_DIRTY_MODE_KEY)
    return setting.value == "true" if setting else False


def _get_ripping_enabled(session) -> bool:
    setting = session.get(Setting, _RIPPING_ENABLED_KEY)
    return setting.value == "true" if setting else _DEFAULT_RIPPING_ENABLED


def _mark_disc_protected(rip_job_id: int, reason: str, label: str, session_factory) -> None:
    session = session_factory()
    try:
        rip_job = session.get(RipJob, rip_job_id)
        if rip_job is None:
            logger.warning("RipJob %s vanished before protected handling", rip_job_id)
            return
        disc = rip_job.disc
        drive = rip_job.drive
        rip_job.status = JobStatus.error
        rip_job.completed_at = naive_utcnow()
        if disc is not None:
            disc.status = DiscStatus.protected
            disc.error_message = reason
        if drive is not None:
            drive.pending_action = "eject"
            drive.pending_action_requested_at = naive_utcnow()
        session.commit()
        logger.warning("DVD disc #%s appears copy protected, ejecting: %s", disc.id if disc else "?", reason)
    finally:
        session.close()


def _run_job(rip_job_id, device_path, scratch_dir, disc_label, fake_rip_mode, session_factory, label, cfg, inject_dirty) -> None:
    # Pre-rip protection scan: catches ARccOS and other schemes before dvdbackup
    # wastes time or hangs trying to read fake/corrupted sector tables.
    scan = scan_for_copy_protection(device_path, fake_rip_mode)
    if scan["protected"]:
        _mark_disc_protected(rip_job_id, scan["reason"], label, session_factory)
        active_jobs.unregister(rip_job_id)
        return

    try:
        result = run_dvdbackup(
            device_path, scratch_dir, disc_label, fake_rip_mode, rip_job_id, session_factory,
            inject_dirty=inject_dirty,
        )
    except Exception:
        logger.exception("Unexpected error running rip job %s", rip_job_id)
        result = {"success": False, "log": "Unexpected error - see service logs", "return_code": None}

    if active_jobs.was_rolled_back(rip_job_id):
        # job_rollback.py already set the authoritative final state
        # (queued) and cleaned up - don't overwrite it with error/done.
        logger.info("Rip job %s was rolled back externally - skipping normal completion handling", rip_job_id)
        active_jobs.unregister(rip_job_id)
        return

    if result.get("timed_out"):
        _mark_disc_protected(
            rip_job_id,
            "Rip timed out after 60 minutes — possible copy protection",
            label,
            session_factory,
        )
        active_jobs.unregister(rip_job_id)
        return

    if not result["success"]:
        _fail_job(rip_job_id, label, result, scratch_dir, session_factory)
        active_jobs.unregister(rip_job_id)
        return

    # dvdbackup succeeded (possibly "dirty" - padded over a recoverable
    # read error) - move on to building the ISO regardless; dirty is just
    # recorded for now and checked again at final completion below. Disc
    # shows "building" (distinct from "ripping") while this runs.
    dirty = result.get("dirty", False)
    disc_id = _mark_building(rip_job_id, label, session_factory)
    if disc_id is None:
        active_jobs.unregister(rip_job_id)
        return

    video_ts_parent = str(Path(scratch_dir) / disc_label)
    iso_dir = get_store_path(cfg, "dvd_store", "raw", str(disc_id))
    iso_path = str(iso_dir / f"{disc_label}.iso")

    try:
        mkiso_result = run_mkisofs(video_ts_parent, iso_path, fake_rip_mode, rip_job_id, session_factory)
    except Exception:
        logger.exception("Unexpected error running mkisofs for rip job %s", rip_job_id)
        mkiso_result = {"success": False, "log": "Unexpected error - see service logs", "return_code": None}

    if active_jobs.was_rolled_back(rip_job_id):
        logger.info(
            "Rip job %s was rolled back externally during mkisofs - skipping normal completion handling",
            rip_job_id,
        )
        active_jobs.unregister(rip_job_id)
        return

    if mkiso_result["success"]:
        _finish_success(rip_job_id, label, disc_id, iso_dir, cfg, scratch_dir, session_factory, dirty)
    else:
        _fail_job(rip_job_id, label, mkiso_result, scratch_dir, session_factory)

    active_jobs.unregister(rip_job_id)


def _mark_building(rip_job_id, label, session_factory):
    session = session_factory()
    try:
        rip_job = session.get(RipJob, rip_job_id)
        if rip_job is None:
            logger.warning("RipJob %s vanished before mkisofs step", rip_job_id)
            return None
        disc = rip_job.disc
        if disc is None:
            logger.warning("RipJob %s has no disc - skipping mkisofs step", rip_job_id)
            return None
        disc.status = DiscStatus.building
        session.commit()
        logger.info("Rip job %s (%s) entering building phase", rip_job_id, label)
        return disc.id
    finally:
        session.close()


def _finish_success(rip_job_id, label, disc_id, iso_dir, cfg, scratch_dir, session_factory, dirty: bool) -> None:
    session = session_factory()
    try:
        rip_job = session.get(RipJob, rip_job_id)
        disc = session.get(Disc, disc_id)
        if rip_job is None or disc is None:
            logger.warning("RipJob %s or disc %s vanished before final completion", rip_job_id, disc_id)
            return

        relative_path = str(iso_dir.relative_to(Path(cfg["storage"]["datastore_root"])))

        disc.raw_path = relative_path
        disc.ripped_at = naive_utcnow()
        if disc.drive is not None and disc.drive.physical_drive is not None:
            disc.ripped_in_region = disc.drive.physical_drive.region

        disc_label = disc.disc_fingerprint or f"disc_{disc.id}"
        iso_path = str(iso_dir / f"{disc_label}.iso")
        original_region = patch_region_if_needed(iso_path, disc.id)
        if original_region is not None:
            disc.ripped_in_region = f"0x{original_region:02X}"

        # dirty -> flag for a re-rip next time this disc is reinserted
        # (main.py's detection dedup checks needs_rerip). clean -> clear
        # any prior dirty flag, e.g. this is a re-rip that's now succeeded.
        disc.rip_quality = "dirty" if dirty else "clean"
        disc.needs_rerip = dirty

        # A working title set before the rip finished (e.g. typed in while
        # ripping/building) goes straight to "ripped" - otherwise there's
        # nothing to identify it by yet, so it needs a stop in
        # "identifying" until temp-name is set via the API.
        disc.status = DiscStatus.ripped if disc.temp_name else DiscStatus.identifying

        rip_job.status = JobStatus.done
        rip_job.completed_at = naive_utcnow()

        session.commit()
        logger.info(
            "Rip job %s (%s) completed successfully - ISO at %s (rip_quality=%s)",
            rip_job_id, label, relative_path, disc.rip_quality,
        )

        try:
            n = create_encode_jobs(session, disc_id, "dvd")
            if n:
                logger.info("Queued %d encode job(s) for disc #%s", n, disc_id)
        except Exception:
            logger.exception("Failed to create encode jobs for disc #%s - rip completion stands", disc_id)

        elapsed = (rip_job.completed_at - rip_job.started_at).total_seconds() if rip_job.started_at else None
        write_log_event(session_factory, "rip_completed", drive_label=label, disc_id=disc_id,
            working_title=disc.temp_name, outcome=disc.rip_quality, elapsed_seconds=elapsed)
    finally:
        session.close()
        _cleanup_scratch_dir(scratch_dir)


def _fail_job(rip_job_id, label, result, scratch_dir, session_factory) -> None:
    session = session_factory()
    try:
        rip_job = session.get(RipJob, rip_job_id)
        if rip_job is None:
            logger.warning("RipJob %s vanished before error handling", rip_job_id)
            return
        disc = rip_job.disc

        rip_job.status = JobStatus.error
        rip_job.completed_at = naive_utcnow()
        error_message = _extract_error_message(result.get("log") or "")
        rip_job.error_message = error_message
        if disc is not None:
            disc.status = DiscStatus.error

        session.commit()
        logger.warning("Rip job %s (%s) failed: %s", rip_job_id, label, error_message)

        elapsed = (rip_job.completed_at - rip_job.started_at).total_seconds() if rip_job.started_at else None
        write_log_event(session_factory, "rip_failed", drive_label=label,
            disc_id=disc.id if disc else None,
            working_title=disc.temp_name if disc else None,
            outcome="error", elapsed_seconds=elapsed)
    finally:
        session.close()
        _cleanup_scratch_dir(scratch_dir)


# ---------------------------------------------------------------------------
# CD: one job processes all tracks needing a rip, sequentially (one drive
# can only read one track at a time anyway) - no separate build step.
# ---------------------------------------------------------------------------

_NEEDS_RIP_TRACK_QUALITIES = (None, RipQuality.imperfect, RipQuality.failed)


def _disc_still_on_drive(disc_id, expected_drive_id, session_factory) -> bool:
    session = session_factory()
    try:
        disc = session.get(Disc, disc_id)
        return disc is not None and disc.drive_id == expected_drive_id
    finally:
        session.close()


def _run_cd_job(rip_job_id, device_path, fake_rip_mode, label, cfg, inject_dirty, session_factory) -> None:
    session = session_factory()
    try:
        rip_job = session.get(RipJob, rip_job_id)
        if rip_job is None:
            logger.warning("RipJob %s vanished before CD job started", rip_job_id)
            return
        disc = rip_job.disc
        if disc is None:
            logger.warning("RipJob %s has no disc - aborting CD job", rip_job_id)
            return
        disc_id = disc.id
        working_title = disc.temp_name
        expected_drive_id = rip_job.drive_id

        # Fresh disc: every track has rip_quality=None, so all of them
        # need ripping. Re-rip of a dirty disc: only the imperfect/failed
        # ones do - already-"good" tracks are left untouched.
        pending = sorted(
            (t for t in disc.tracks if t.rip_quality in _NEEDS_RIP_TRACK_QUALITIES),
            key=lambda t: t.track_number,
        )
        track_numbers = [t.track_number for t in pending]
        track_durations = {t.track_number: t.duration_seconds for t in pending}

        cd_dir = get_store_path(cfg, "cd_store", "raw", str(disc_id))
        disc.raw_path = str(cd_dir.relative_to(Path(cfg["storage"]["datastore_root"])))
        session.commit()
    finally:
        session.close()

    # Phase 1 (ripping): cdparanoia writes WAVs to a per-disc scratch
    # subdirectory. Phase 2 (building): move them to NFS after all tracks
    # succeed, so the NFS destination is never left in a partial state.
    scratch_subdir = Path(cfg["storage"]["scratch_dir"]) / str(disc_id)
    os.makedirs(scratch_subdir, exist_ok=True)

    total = len(track_numbers)
    if total == 0:
        logger.warning("RipJob %s (disc %s) has no tracks needing a rip - nothing to do", rip_job_id, disc_id)
        _cleanup_scratch_dir(str(scratch_subdir))
        _finish_cd_job(rip_job_id, label, disc_id, session_factory)
        active_jobs.unregister(rip_job_id)
        return

    ripped_in_this_run = []   # track numbers successfully ripped this pass, to move to NFS

    for index, track_number in enumerate(track_numbers, start=1):
        scratch_path = str(scratch_subdir / f"track{track_number:02d}.cdda.wav")
        stage_label = f"Track {index}/{total}"
        # fake_dirty_mode simulates several bad tracks on a disc, not a
        # wholly unreadable one - every odd-numbered track gets it, so
        # the selective re-rip logic gets exercised against more than
        # just a single bad track.
        inject_dirty_this_track = inject_dirty and track_number % 2 == 1

        track_start = naive_utcnow()
        write_log_event(session_factory, "track_started", drive_label=label, disc_id=disc_id,
            working_title=working_title, track_number=track_number)

        try:
            result = run_cdparanoia(
                device_path, track_number, scratch_path, track_durations.get(track_number), stage_label,
                fake_rip_mode, rip_job_id, session_factory, inject_dirty=inject_dirty_this_track,
            )
        except Exception:
            logger.exception("Unexpected error ripping track %s for rip job %s", track_number, rip_job_id)
            result = {"success": False, "log": "Unexpected error - see service logs", "return_code": None}

        if active_jobs.was_rolled_back(rip_job_id):
            logger.info(
                "Rip job %s was rolled back externally during track %s - skipping normal completion handling",
                rip_job_id, track_number,
            )
            _cleanup_scratch_dir(str(scratch_subdir))
            active_jobs.unregister(rip_job_id)
            return

        # A single bad/scratched track is normal and shouldn't stop the
        # rest of the disc - only return_code=None (process never even
        # ran: rolled back, or couldn't launch) suggests a deeper
        # drive/device problem worth aborting the whole disc for.
        hard_failure = not result["success"] and result.get("return_code") is None
        _record_track_result(disc_id, track_number, result, label, session_factory)

        if result["success"]:
            ripped_in_this_run.append(track_number)

        track_outcome = ("imperfect" if result.get("dirty") else "good") if result["success"] else "failed"
        track_elapsed = (naive_utcnow() - track_start).total_seconds()
        write_log_event(session_factory, "track_completed", drive_label=label, disc_id=disc_id,
            working_title=working_title, track_number=track_number,
            outcome=track_outcome, elapsed_seconds=track_elapsed)

        if hard_failure:
            _fail_cd_job(rip_job_id, label, disc_id, result, session_factory)
            _cleanup_scratch_dir(str(scratch_subdir))
            active_jobs.unregister(rip_job_id)
            return

    # Phase 2 (building): move ripped WAVs from scratch to their final NFS
    # location. The disc shows "building" while the move is in progress.
    _mark_cd_building(rip_job_id, disc_id, label, session_factory)
    if not _disc_still_on_drive(disc_id, expected_drive_id, session_factory):
        logger.warning(
            "Disc #%s removed from drive during ripping phase - completing WAV move to NFS anyway",
            disc_id,
        )
    total_to_move = len(ripped_in_this_run)
    logger.info("Moving %d WAV file(s) to NFS for disc #%s (%s)", total_to_move, disc_id, label)
    _update_rip_job_progress(rip_job_id, 0, "Moving tracks to library", session_factory)
    moved_count = 0
    try:
        os.makedirs(str(cd_dir), exist_ok=True)
        for track_number in ripped_in_this_run:
            shutil.move(
                str(scratch_subdir / f"track{track_number:02d}.cdda.wav"),
                str(cd_dir / f"track{track_number:02d}.wav"),
            )
            moved_count += 1
            _update_rip_job_progress(
                rip_job_id,
                int(moved_count / total_to_move * 100),
                f"Moving track {moved_count} of {total_to_move}",
                session_factory,
            )
    except Exception:
        logger.exception("Failed to move WAV files to NFS for disc #%s (%s)", disc_id, label)
        _fail_cd_job(rip_job_id, label, disc_id,
            {"log": "Failed to move WAV files to NFS - see service logs", "return_code": 1},
            session_factory)
        _cleanup_scratch_dir(str(scratch_subdir))
        active_jobs.unregister(rip_job_id)
        return

    _cleanup_scratch_dir(str(scratch_subdir))
    _finish_cd_job(rip_job_id, label, disc_id, session_factory)
    active_jobs.unregister(rip_job_id)


def _update_rip_job_progress(rip_job_id, percent: int, stage: str, session_factory) -> None:
    session = session_factory()
    try:
        rip_job = session.get(RipJob, rip_job_id)
        if rip_job is not None:
            rip_job.progress_percent = percent
            rip_job.progress_stage = stage
            session.commit()
    finally:
        session.close()


def _mark_cd_building(rip_job_id, disc_id, label, session_factory) -> None:
    session = session_factory()
    try:
        disc = session.get(Disc, disc_id)
        if disc is not None:
            disc.status = DiscStatus.building
            session.commit()
        logger.info("CD rip job %s (%s) entering building phase (moving WAVs to NFS)", rip_job_id, label)
    finally:
        session.close()


def _record_track_result(disc_id, track_number, result, label, session_factory) -> None:
    session = session_factory()
    try:
        track = session.scalars(
            select(CDTrack).where(CDTrack.disc_id == disc_id, CDTrack.track_number == track_number)
        ).first()
        if track is None:
            logger.warning("CDTrack %s for disc %s vanished - skipping result recording", track_number, disc_id)
            return

        track.rip_log = result.get("log")
        if result["success"]:
            track.rip_quality = RipQuality.imperfect if result.get("dirty") else RipQuality.good
            track.wav_filename = f"track{track_number:02d}.wav"
        else:
            track.rip_quality = RipQuality.failed

        if track.rip_quality in (RipQuality.imperfect, RipQuality.failed):
            disc = session.get(Disc, disc_id)
            if disc and disc.rip_quality != "dirty":
                disc.rip_quality = "dirty"
                disc.needs_rerip = True

        session.commit()
        logger.info(
            "Track %s for disc %s (%s) -> %s", track_number, disc_id, label, track.rip_quality.value,
        )
    finally:
        session.close()


def _finish_cd_job(rip_job_id, label, disc_id, session_factory) -> None:
    session = session_factory()
    try:
        rip_job = session.get(RipJob, rip_job_id)
        disc = session.get(Disc, disc_id)
        if rip_job is None or disc is None:
            logger.warning("RipJob %s or disc %s vanished before final CD completion", rip_job_id, disc_id)
            return

        # "Clean" only if every track, across all attempts (not just the
        # ones processed in this run), is currently "good".
        any_bad = any(t.rip_quality in (RipQuality.imperfect, RipQuality.failed) for t in disc.tracks)
        disc.rip_quality = "dirty" if any_bad else "clean"
        disc.needs_rerip = any_bad
        disc.ripped_at = naive_utcnow()
        disc.status = DiscStatus.ripped if disc.temp_name else DiscStatus.identifying

        rip_job.status = JobStatus.done
        rip_job.completed_at = naive_utcnow()

        session.commit()
        logger.info(
            "CD rip job %s (%s) completed - disc #%s rip_quality=%s",
            rip_job_id, label, disc_id, disc.rip_quality,
        )

        try:
            n = create_encode_jobs(session, disc_id, "cd")
            if n:
                logger.info("Queued %d encode job(s) for disc #%s", n, disc_id)
        except Exception:
            logger.exception("Failed to create encode jobs for disc #%s - rip completion stands", disc_id)

        elapsed = (rip_job.completed_at - rip_job.started_at).total_seconds() if rip_job.started_at else None
        write_log_event(session_factory, "rip_completed", drive_label=label, disc_id=disc_id,
            working_title=disc.temp_name, outcome=disc.rip_quality, elapsed_seconds=elapsed)
    finally:
        session.close()


def _fail_cd_job(rip_job_id, label, disc_id, result, session_factory) -> None:
    session = session_factory()
    try:
        rip_job = session.get(RipJob, rip_job_id)
        if rip_job is None:
            logger.warning("RipJob %s vanished before CD error handling", rip_job_id)
            return
        disc = rip_job.disc

        rip_job.status = JobStatus.error
        rip_job.completed_at = naive_utcnow()
        error_message = _extract_error_message(result.get("log") or "")
        rip_job.error_message = error_message
        if disc is not None:
            disc.status = DiscStatus.error

        session.commit()
        logger.warning(
            "CD rip job %s (%s) aborted - drive/device problem, not just a bad track: %s",
            rip_job_id, label, error_message,
        )

        elapsed = (rip_job.completed_at - rip_job.started_at).total_seconds() if rip_job.started_at else None
        write_log_event(session_factory, "rip_failed", drive_label=label, disc_id=disc_id,
            working_title=disc.temp_name if disc else None,
            outcome="error", elapsed_seconds=elapsed)
    finally:
        session.close()


def _extract_error_message(log_text: str) -> str:
    for line in log_text.splitlines():
        if "error" in line.lower():
            return line.strip()
    return log_text[-500:].strip()


def _cleanup_scratch_dir(scratch_dir: str) -> None:
    try:
        if os.path.isdir(scratch_dir):
            shutil.rmtree(scratch_dir)
    except OSError:
        logger.warning("Failed to clean up scratch dir %s", scratch_dir, exc_info=True)
