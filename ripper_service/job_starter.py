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
import re
import shutil
import threading
from pathlib import Path

from sqlalchemy import select

from common.config import get_store_path
from common.encode_invalidation import invalidate_encode_jobs_for_rerip
from common.encode_queue import create_encode_jobs
from common.models import CDTrack, Disc, DiscStatus, DiscType, JobStatus, RipJob, RipQuality, Setting, naive_utcnow
from ripper_service import active_jobs
from ripper_service.job_rollback import rollback_excess_jobs
from ripper_service.log_writer import write_log_event
from ripper_service.region_patcher import patch_region_if_needed
from ripper_service.makemkv_worker import run_makemkv
from ripper_service.rip_worker import run_cdparanoia, run_dvdbackup, run_mkisofs, scan_for_copy_protection

logger = logging.getLogger(__name__)

# MakeMKV log parsing for error extraction
_MKV_MSG_TEXT_RE = re.compile(r'^MSG:\d+,\d+,\d+,"([^"]*)"')
_MKV_ERR_KW_RE   = re.compile(r'(fail|error|cannot|unable)', re.IGNORECASE)
_MKV_HARMLESS_RE = re.compile(r'(AUTHENTICATION|work.{0,5}around|scrambled)', re.IGNORECASE)
_MKV_BACKUP_FAILED_RE = re.compile(r'Backup failed', re.IGNORECASE)

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

    if os.environ.get("DISCRIPPER_ENV") == "prod":
        if fake_rip_mode:
            logger.warning("fake_rip_mode=true is set but ignored in prod environment")
            fake_rip_mode = False
        if fake_dirty_mode:
            logger.warning("fake_dirty_mode=true is set but ignored in prod environment")
            fake_dirty_mode = False

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
        session.commit()
        logger.warning("DVD disc #%s appears copy protected: %s", disc.id if disc else "?", reason)
    finally:
        session.close()


def _get_disc_id(rip_job_id: int, session_factory) -> int | None:
    session = session_factory()
    try:
        rip_job = session.get(RipJob, rip_job_id)
        return rip_job.disc_id if rip_job else None
    finally:
        session.close()


def _set_rip_reason(rip_job_id: int, reason: str, session_factory) -> None:
    """Set disc.rip_reason and update progress_stage to signal MakeMKV re-rip in UI."""
    session = session_factory()
    try:
        rip_job = session.get(RipJob, rip_job_id)
        if rip_job is None:
            return
        disc = rip_job.disc
        if disc is not None:
            disc.rip_reason = reason
        rip_job.progress_stage = "MakeMKV"
        session.commit()
    finally:
        session.close()


def _append_to_log(rip_job_id: int, text: str, session_factory) -> None:
    session = session_factory()
    try:
        rip_job = session.get(RipJob, rip_job_id)
        if rip_job is not None:
            rip_job.log = (rip_job.log + "\n\n" if rip_job.log else "") + text
            session.commit()
    finally:
        session.close()


def _finish_success_makemkv(
    rip_job_id: int, label: str, disc_id: int, iso_dir, cfg: dict,
    scratch_dir: str, session_factory, dirty: bool = False,
) -> None:
    """Like _finish_success but sets rip_method="makemkv" and keeps rip_reason."""
    session = session_factory()
    try:
        rip_job = session.get(RipJob, rip_job_id)
        disc = session.get(Disc, disc_id)
        if rip_job is None or disc is None:
            logger.warning("RipJob %s or disc %s vanished before MakeMKV completion", rip_job_id, disc_id)
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

        disc.rip_method = "makemkv"
        # Keep disc.rip_reason (do not clear it — UI shows "protected → MakeMKV" etc.)
        disc.rip_quality = "dirty" if dirty else "clean"
        disc.needs_rerip = dirty
        disc.error_message = None  # clear any error from a previous failed attempt
        disc.status = DiscStatus.ripped if disc.temp_name else DiscStatus.identifying

        rip_job.status = JobStatus.done
        rip_job.completed_at = naive_utcnow()
        session.commit()
        logger.info(
            "MakeMKV rip job %s (%s) completed — ISO at %s (rip_quality=%s, reason=%s)",
            rip_job_id, label, relative_path, disc.rip_quality, disc.rip_reason,
        )

        datastore_root = cfg["storage"]["datastore_root"]
        n_inv, n_running = invalidate_encode_jobs_for_rerip(session, disc_id, datastore_root)
        if n_inv:
            logger.info("Invalidated %d encode job(s) for disc #%s (%d running)", n_inv, disc_id, n_running)
            write_log_event(session_factory, "encodes_invalidated", drive_label=label,
                disc_id=disc_id, working_title=disc.temp_name,
                outcome=f"{n_inv} jobs invalidated")

        try:
            n = create_encode_jobs(session, disc_id, "dvd")
            if n:
                logger.info("Queued %d encode job(s) for disc #%s", n, disc_id)
        except Exception:
            logger.exception("Failed to create encode jobs for disc #%s — rip completion stands", disc_id)

        elapsed = (rip_job.completed_at - rip_job.started_at).total_seconds() if rip_job.started_at else None
        write_log_event(session_factory, "makemkv_completed", drive_label=label, disc_id=disc_id,
            working_title=disc.temp_name, outcome=disc.rip_quality)
        write_log_event(session_factory, "rip_completed", drive_label=label, disc_id=disc_id,
            working_title=disc.temp_name, outcome=disc.rip_quality, elapsed_seconds=elapsed)
    finally:
        session.close()
        _cleanup_scratch_dir(scratch_dir)


def _fail_job_both(
    rip_job_id: int, label: str, first_failure_summary: str,
    mkv_result: dict, scratch_dir: str, session_factory,
    disc_id: int = None,
) -> None:
    """Fail a job after both dvdbackup and MakeMKV fallback failed."""
    mkv_error = _extract_mkv_error_message(mkv_result.get("log") or "MakeMKV failed", mkv_result.get("return_code"))
    combined = f"dvdbackup: {first_failure_summary}; MakeMKV fallback: {mkv_error}"
    write_log_event(session_factory, "makemkv_failed", drive_label=label,
        disc_id=disc_id, outcome="error", detail=mkv_error[:300] if mkv_error else None)
    _fail_job(
        rip_job_id, label,
        {"log": mkv_result.get("log", ""), "return_code": mkv_result.get("return_code")},
        scratch_dir, session_factory,
        error_override=combined,
    )


def _run_job(rip_job_id, device_path, scratch_dir, disc_label, fake_rip_mode, session_factory, label, cfg, inject_dirty) -> None:
    # Get disc_id upfront so we can compute iso_path before any sub-step.
    disc_id = _get_disc_id(rip_job_id, session_factory)
    if disc_id is None:
        logger.warning("RipJob %s has no disc — aborting DVD job", rip_job_id)
        active_jobs.unregister(rip_job_id)
        return

    dvd_cfg = cfg.get("dvd", {})
    rip_method = dvd_cfg.get("rip_method", "dvdbackup")
    # Never attempt real MakeMKV in fake mode — fake dvdbackup handles the test path.
    makemkv_fallback = dvd_cfg.get("makemkv_fallback", False) and not fake_rip_mode

    iso_dir = get_store_path(cfg, "dvd_store", "raw", str(disc_id))
    iso_path = str(iso_dir / f"{disc_label}.iso")

    # ── MakeMKV primary mode ──────────────────────────────────────────────────
    if rip_method == "makemkv":
        _update_rip_job_progress(rip_job_id, 0, "Starting MakeMKV rip", session_factory)
        result = run_makemkv(device_path, iso_path, rip_job_id, session_factory)
        if active_jobs.was_rolled_back(rip_job_id):
            logger.info("Rip job %s rolled back during MakeMKV rip", rip_job_id)
            active_jobs.unregister(rip_job_id)
            return
        if result["success"]:
            _finish_success_makemkv(rip_job_id, label, disc_id, iso_dir, cfg, scratch_dir, session_factory, dirty=result.get("dirty", False))
        else:
            mkv_err = _extract_mkv_error_message(result.get("log") or "", result.get("return_code"))
            _fail_job(rip_job_id, label, result, scratch_dir, session_factory, error_override=mkv_err)
        active_jobs.unregister(rip_job_id)
        return

    # ── dvdbackup path ────────────────────────────────────────────────────────
    scan = scan_for_copy_protection(device_path, fake_rip_mode)
    if scan["protected"]:
        write_log_event(session_factory, "dvdbackup_protected", drive_label=label,
            disc_id=disc_id, outcome="protected", detail=scan["reason"])
        if makemkv_fallback:
            _append_to_log(
                rip_job_id,
                f"[dvdbackup] Protection detected: {scan['reason']}\n[MakeMKV] Falling back to MakeMKV",
                session_factory,
            )
            _set_rip_reason(rip_job_id, "protected", session_factory)
            write_log_event(session_factory, "makemkv_fallback", drive_label=label,
                disc_id=disc_id, detail="copy protection detected")
            result = run_makemkv(device_path, iso_path, rip_job_id, session_factory)
            if active_jobs.was_rolled_back(rip_job_id):
                logger.info("Rip job %s rolled back during MakeMKV protected fallback", rip_job_id)
                active_jobs.unregister(rip_job_id)
                return
            if result["success"]:
                _finish_success_makemkv(rip_job_id, label, disc_id, iso_dir, cfg, scratch_dir, session_factory, dirty=result.get("dirty", False))
            else:
                # MakeMKV failed on a protection-flagged disc — try dvdbackup as last resort.
                # HandBrakeCLI protection scan has false positives; dvdbackup may still produce
                # a (possibly dirty) rip that is usable.
                mkv_err = _extract_mkv_error_message(result.get("log") or "MakeMKV failed", result.get("return_code"))
                write_log_event(session_factory, "makemkv_failed", drive_label=label,
                    disc_id=disc_id, outcome="error", detail=mkv_err[:300])
                _append_to_log(
                    rip_job_id,
                    f"[MakeMKV] Failed ({mkv_err})\n[dvdbackup] Trying dvdbackup as last resort",
                    session_factory,
                )
                try:
                    db_result = run_dvdbackup(
                        device_path, scratch_dir, disc_label, fake_rip_mode,
                        rip_job_id, session_factory, inject_dirty=inject_dirty,
                    )
                except Exception:
                    logger.exception("Unexpected error in dvdbackup last resort for rip job %s", rip_job_id)
                    db_result = {"success": False, "log": "Unexpected error — see service logs", "return_code": None}
                if active_jobs.was_rolled_back(rip_job_id):
                    _cleanup_scratch_dir(scratch_dir)
                    active_jobs.unregister(rip_job_id)
                    return
                if not db_result["success"]:
                    db_err = _extract_error_message(db_result.get("log") or "")
                    combined = f"Protection: {scan['reason']}; MakeMKV: {mkv_err}; dvdbackup: {db_err}"
                    _fail_job(rip_job_id, label, {}, scratch_dir, session_factory, error_override=combined)
                else:
                    dirty_db = db_result.get("dirty", False)
                    if dirty_db:
                        write_log_event(session_factory, "dvdbackup_dirty", drive_label=label,
                            disc_id=disc_id, outcome="dirty",
                            detail=_first_dirty_line(db_result.get("log") or ""))
                        _append_to_log(rip_job_id,
                            "[dvdbackup] Completed with read errors (dirty) — building dirty ISO",
                            session_factory)
                    disc_id_from_building = _mark_building(rip_job_id, label, session_factory)
                    if disc_id_from_building is None:
                        _cleanup_scratch_dir(scratch_dir)
                    else:
                        video_ts_parent = str(Path(scratch_dir) / disc_label)
                        try:
                            mkiso_result = run_mkisofs(video_ts_parent, iso_path, fake_rip_mode, rip_job_id, session_factory)
                        except Exception:
                            logger.exception("Unexpected error in mkisofs last resort for rip job %s", rip_job_id)
                            mkiso_result = {"success": False, "log": "Unexpected error — see service logs", "return_code": None}
                        if active_jobs.was_rolled_back(rip_job_id):
                            _cleanup_scratch_dir(scratch_dir)
                            active_jobs.unregister(rip_job_id)
                            return
                        elif mkiso_result["success"]:
                            _finish_success(rip_job_id, label, disc_id, iso_dir, cfg, scratch_dir, session_factory, dirty=dirty_db)
                        else:
                            _fail_job(rip_job_id, label, mkiso_result, scratch_dir, session_factory)
        else:
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
        result = {"success": False, "log": "Unexpected error — see service logs", "return_code": None}

    if active_jobs.was_rolled_back(rip_job_id):
        logger.info("Rip job %s was rolled back externally — skipping normal completion handling", rip_job_id)
        active_jobs.unregister(rip_job_id)
        return

    if result.get("timed_out"):
        write_log_event(session_factory, "dvdbackup_failed", drive_label=label,
            disc_id=disc_id, outcome="error", detail="Timed out after 60 minutes")
        if makemkv_fallback:
            _append_to_log(
                rip_job_id,
                "[dvdbackup] Timed out after 60 minutes\n[MakeMKV] Falling back to MakeMKV",
                session_factory,
            )
            _set_rip_reason(rip_job_id, "dvdbackup_failed", session_factory)
            _cleanup_scratch_dir(scratch_dir)
            write_log_event(session_factory, "makemkv_fallback", drive_label=label,
                disc_id=disc_id, detail="dvdbackup timed out after 60 minutes")
            result_mkv = run_makemkv(device_path, iso_path, rip_job_id, session_factory)
            if active_jobs.was_rolled_back(rip_job_id):
                active_jobs.unregister(rip_job_id)
                return
            if result_mkv["success"]:
                _finish_success_makemkv(rip_job_id, label, disc_id, iso_dir, cfg, scratch_dir, session_factory, dirty=result_mkv.get("dirty", False))
            else:
                _fail_job_both(rip_job_id, label, "dvdbackup timed out after 60 minutes", result_mkv, scratch_dir, session_factory, disc_id=disc_id)
        else:
            _mark_disc_protected(
                rip_job_id,
                "Rip timed out after 60 minutes — possible copy protection",
                label, session_factory,
            )
        active_jobs.unregister(rip_job_id)
        return

    if not result["success"]:
        dvdback_err = _extract_error_message(result.get("log") or "")
        write_log_event(session_factory, "dvdbackup_failed", drive_label=label,
            disc_id=disc_id, outcome="error",
            detail=f"rc={result.get('return_code')}: {dvdback_err}"[:300])
        if makemkv_fallback:
            _append_to_log(
                rip_job_id,
                f"[dvdbackup] Failed (rc={result.get('return_code')})\n[MakeMKV] Falling back to MakeMKV",
                session_factory,
            )
            _set_rip_reason(rip_job_id, "dvdbackup_failed", session_factory)
            _cleanup_scratch_dir(scratch_dir)
            write_log_event(session_factory, "makemkv_fallback", drive_label=label,
                disc_id=disc_id, detail=f"dvdbackup failed (rc={result.get('return_code')})")
            result_mkv = run_makemkv(device_path, iso_path, rip_job_id, session_factory)
            if active_jobs.was_rolled_back(rip_job_id):
                active_jobs.unregister(rip_job_id)
                return
            if result_mkv["success"]:
                _finish_success_makemkv(rip_job_id, label, disc_id, iso_dir, cfg, scratch_dir, session_factory, dirty=result_mkv.get("dirty", False))
            else:
                _fail_job_both(rip_job_id, label, f"dvdbackup failed (rc={result.get('return_code')})", result_mkv, scratch_dir, session_factory, disc_id=disc_id)
        else:
            _fail_job(rip_job_id, label, result, scratch_dir, session_factory)
        active_jobs.unregister(rip_job_id)
        return

    dirty = result.get("dirty", False)

    if dirty and makemkv_fallback:
        dirty_detail = _first_dirty_line(result.get("log") or "")
        write_log_event(session_factory, "dvdbackup_dirty", drive_label=label,
            disc_id=disc_id, outcome="dirty", detail=dirty_detail)
        _append_to_log(
            rip_job_id,
            "[dvdbackup] Completed with read errors (dirty)\n[MakeMKV] Falling back to MakeMKV for a clean rip",
            session_factory,
        )
        _set_rip_reason(rip_job_id, "dirty", session_factory)
        write_log_event(session_factory, "makemkv_fallback", drive_label=label,
            disc_id=disc_id, detail="dvdbackup reported read errors (dirty rip)")
        # Keep scratch_dir until we know if MakeMKV succeeds
        result_mkv = run_makemkv(device_path, iso_path, rip_job_id, session_factory)
        if active_jobs.was_rolled_back(rip_job_id):
            _cleanup_scratch_dir(scratch_dir)
            active_jobs.unregister(rip_job_id)
            return
        if result_mkv["success"]:
            _cleanup_scratch_dir(scratch_dir)  # discard dirty dvdbackup content
            _finish_success_makemkv(rip_job_id, label, disc_id, iso_dir, cfg, scratch_dir, session_factory, dirty=result_mkv.get("dirty", False))
        else:
            # MakeMKV also failed — build ISO from dirty dvdbackup content anyway
            mkv_err = _extract_mkv_error_message(result_mkv.get("log") or "MakeMKV failed", result_mkv.get("return_code"))
            write_log_event(session_factory, "makemkv_failed", drive_label=label,
                disc_id=disc_id, outcome="error", detail=mkv_err[:300] if mkv_err else None)
            _append_to_log(
                rip_job_id,
                "[MakeMKV] Also failed — building ISO from dirty dvdbackup content",
                session_factory,
            )
            disc_id_from_building = _mark_building(rip_job_id, label, session_factory)
            if disc_id_from_building is None:
                _cleanup_scratch_dir(scratch_dir)
                active_jobs.unregister(rip_job_id)
                return
            video_ts_parent = str(Path(scratch_dir) / disc_label)
            try:
                mkiso_result = run_mkisofs(video_ts_parent, iso_path, fake_rip_mode, rip_job_id, session_factory)
            except Exception:
                logger.exception("Unexpected error running mkisofs fallback for rip job %s", rip_job_id)
                mkiso_result = {"success": False, "log": "Unexpected error — see service logs", "return_code": None}
            if active_jobs.was_rolled_back(rip_job_id):
                _cleanup_scratch_dir(scratch_dir)
                active_jobs.unregister(rip_job_id)
                return
            if mkiso_result["success"]:
                _finish_success(rip_job_id, label, disc_id, iso_dir, cfg, scratch_dir, session_factory, dirty=True)
            else:
                _fail_job(rip_job_id, label, mkiso_result, scratch_dir, session_factory)
        active_jobs.unregister(rip_job_id)
        return

    # Normal (clean or dirty-without-fallback) dvdbackup path
    disc_id_from_building = _mark_building(rip_job_id, label, session_factory)
    if disc_id_from_building is None:
        active_jobs.unregister(rip_job_id)
        return

    video_ts_parent = str(Path(scratch_dir) / disc_label)

    try:
        mkiso_result = run_mkisofs(video_ts_parent, iso_path, fake_rip_mode, rip_job_id, session_factory)
    except Exception:
        logger.exception("Unexpected error running mkisofs for rip job %s", rip_job_id)
        mkiso_result = {"success": False, "log": "Unexpected error — see service logs", "return_code": None}

    if active_jobs.was_rolled_back(rip_job_id):
        logger.info("Rip job %s rolled back during mkisofs — skipping normal completion handling", rip_job_id)
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
        disc.error_message = None  # clear any error from a previous failed attempt

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

        datastore_root = cfg["storage"]["datastore_root"]
        n_inv, n_running = invalidate_encode_jobs_for_rerip(session, disc_id, datastore_root)
        if n_inv:
            logger.info(
                "Invalidated %d encode job(s) for disc #%s (%d were running)",
                n_inv, disc_id, n_running,
            )
            write_log_event(session_factory, "encodes_invalidated", drive_label=label,
                disc_id=disc_id, working_title=disc.temp_name,
                outcome=f"{n_inv} jobs invalidated")

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


def _fail_job(rip_job_id, label, result, scratch_dir, session_factory, error_override: str | None = None) -> None:
    session = session_factory()
    try:
        rip_job = session.get(RipJob, rip_job_id)
        if rip_job is None:
            logger.warning("RipJob %s vanished before error handling", rip_job_id)
            return
        disc = rip_job.disc

        rip_job.status = JobStatus.error
        rip_job.completed_at = naive_utcnow()
        error_message = error_override or _extract_error_message(result.get("log") or "")
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

    track_timeout_seconds = int(cfg.get("cd", {}).get("track_timeout_seconds", 600))

    total = len(track_numbers)
    if total == 0:
        logger.warning("RipJob %s (disc %s) has no tracks needing a rip - nothing to do", rip_job_id, disc_id)
        _cleanup_scratch_dir(str(scratch_subdir))
        _finish_cd_job(rip_job_id, label, disc_id, session_factory, cfg=cfg)
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
                track_timeout_seconds=track_timeout_seconds,
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
    _finish_cd_job(rip_job_id, label, disc_id, session_factory, ripped_track_numbers=ripped_in_this_run, cfg=cfg)
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


def _finish_cd_job(rip_job_id, label, disc_id, session_factory, ripped_track_numbers=(), cfg=None) -> None:
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

        if cfg and ripped_track_numbers:
            datastore_root = cfg["storage"]["datastore_root"]
            ripped_set = set(ripped_track_numbers)
            ripped_ids = [t.id for t in disc.tracks if t.track_number in ripped_set]
            n_inv, n_running = invalidate_encode_jobs_for_rerip(
                session, disc_id, datastore_root, track_ids=ripped_ids,
            )
            if n_inv:
                logger.info(
                    "Invalidated %d encode job(s) for disc #%s (%d were running)",
                    n_inv, disc_id, n_running,
                )
                write_log_event(session_factory, "encodes_invalidated", drive_label=label,
                    disc_id=disc_id, working_title=disc.temp_name,
                    outcome=f"{n_inv} jobs invalidated")

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


def _first_dirty_line(log_text: str) -> str | None:
    """Return the first dvdbackup output line that looks like a read error."""
    _dirty_re = re.compile(r"error reading|padding\s+\d+\s+blocks", re.IGNORECASE)
    for line in log_text.splitlines():
        if _dirty_re.search(line):
            return line.strip()[:200]
    return None


def _extract_mkv_error_message(log_text: str, return_code) -> str:
    """Extract the meaningful error from a MakeMKV log.

    Skips harmless expected MSG lines (scrambled sector / authentication /
    work-around).  Returns: "Backup failed; <last error msg>; rc=N".
    """
    backup_failed = bool(_MKV_BACKUP_FAILED_RE.search(log_text))
    last_error_msg: str | None = None
    for line in log_text.splitlines():
        m = _MKV_MSG_TEXT_RE.match(line)
        if not m:
            continue
        text = m.group(1)
        if _MKV_ERR_KW_RE.search(text) and not _MKV_HARMLESS_RE.search(text):
            last_error_msg = text
    parts: list[str] = []
    if backup_failed:
        parts.append("Backup failed")
    # avoid duplicating "Backup failed" if the last MSG is exactly that text
    if last_error_msg and last_error_msg.lower().rstrip(". ") != "backup failed":
        parts.append(last_error_msg)
    if return_code is not None:
        parts.append(f"rc={return_code}")
    if parts:
        return "; ".join(parts)
    return log_text[-300:].strip() or "MakeMKV failed"


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
