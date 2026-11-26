"""
Ripper service entry point.

Run with (from the project root, venv active):
    DISCRIPPER_ENV=dev python3 -m ripper_service.main

Polls configured drives for hardware identity and disc insert/removal,
creates rip jobs for newly-detected discs (DVDs gated on region_known,
CDs are not region-locked so always proceed), and starts queued jobs
(dvdbackup+mkisofs for DVD, cdparanoia per track for CD) slot-aware up
to max_rippers.

Exposes a simple remote start/stop protocol via the settings table:
    service_status:    "running" | "stopped" - written by this process
    service_heartbeat:  ISO timestamp, refreshed every poll iteration
                        and stamped one final time on clean shutdown
    service_command:    "" | "exit" - written by the API/UI, read and
                        cleared by this process on its next poll
A stale heartbeat while service_status="running" means the process died
without a clean shutdown (crash/kill -9), distinguishable in the UI from
a genuine clean stop.
"""

import logging
import os
import shutil
import sys
import time
from datetime import datetime, timezone

from sqlalchemy import select, update

from common.config import load_config
from common.models import CDTrack, Disc, DiscStatus, DiscType, Drive, JobStatus, RipJob, Setting
from ripper_service.cd_toc import read_table_of_contents
from ripper_service.lookup_queue import submit_lookup
from ripper_service.mb_lookup import compute_mb_disc_id, init_useragent, lookup_musicbrainz
from ripper_service.db import get_session_factory
from ripper_service.disc_label import read_volume_info, GENERIC_VOLUME_LABELS
from ripper_service.disc_id import compute_dvd_id
from ripper_service.drive_registry import sync_physical_drives
from ripper_service.job_rollback import rollback_excess_jobs, rollback_job
from ripper_service.job_starter import start_eligible_rip_jobs
from ripper_service.log_writer import write_log_event
from ripper_service.pending_actions import process_pending_actions
from ripper_service.tray_status import get_tray_status, tray_open_from_status
from ripper_service.udev_helper import get_drive_info

POLL_INTERVAL_SECONDS = 3


def _check_datastore_mount(datastore_root: str) -> None:
    if not os.path.isdir(datastore_root):
        logger.critical(
            "FATAL: datastore path does not exist or is not a directory: %s — "
            "check your NFS mount before starting",
            datastore_root,
        )
        sys.exit(1)
    if not os.path.ismount(datastore_root):
        logger.critical(
            "FATAL: datastore path is not a mount point: %s — "
            "NFS may not be mounted",
            datastore_root,
        )
        sys.exit(1)
    logger.info("Datastore mount OK: %s", datastore_root)

# Statuses with an outstanding RipJob that job_starter can still act on.
_ACTIVE_DISC_STATUSES = (DiscStatus.queued, DiscStatus.ripping, DiscStatus.building)

# All non-active rip-phase statuses. Includes error and done so that a
# re-inserted disc with a prior error/dirty record is found and held for
# user-initiated retry rather than auto-creating a new row.
_COMPLETED_DISC_STATUSES = (
    DiscStatus.ripped, DiscStatus.identifying, DiscStatus.encoding,
    DiscStatus.done, DiscStatus.error, DiscStatus.protected,
)

_SERVICE_STATUS_KEY = "service_status"
_SERVICE_HEARTBEAT_KEY = "service_heartbeat"
_SERVICE_COMMAND_KEY = "service_command"


# Shared with disc_label.py — see GENERIC_VOLUME_LABELS for the canonical set.
# Used here to block dedup on known-generic Volume IDs (the prod bug where
# disc #701 "owned" DVD_VIDEO and all subsequent discs with that label resumed it).
_GENERIC_DISC_FINGERPRINTS = GENERIC_VOLUME_LABELS


def _find_existing_disc(session, disc_fingerprint):
    """
    Look up an existing Disc by fingerprint alone - shared by the DVD and
    CD detection branches below. drive_id is intentionally excluded: it
    gets cleared on removal and is unreliable for dedup; fingerprint is
    the stable, correct key. An active disc takes priority over a completed
    one. Returns (active_disc_or_None, completed_disc_or_None).
    """
    if not disc_fingerprint:
        return None, None
    # Refuse to dedup on known-generic Volume IDs; every disc with such a
    # label must be treated as new until a content-based ID replaces it.
    if disc_fingerprint in _GENERIC_DISC_FINGERPRINTS:
        logger.info(
            "Disc fingerprint %r is a generic volume label — skipping dedup lookup",
            disc_fingerprint,
        )
        return None, None

    active = session.scalars(
        select(Disc)
        .where(
            Disc.disc_fingerprint == disc_fingerprint,
            Disc.status.in_(_ACTIVE_DISC_STATUSES),
        )
        .order_by(Disc.id)
    ).first()
    if active is not None:
        return active, None

    completed = session.scalars(
        select(Disc)
        .where(
            Disc.disc_fingerprint == disc_fingerprint,
            Disc.status.in_(_COMPLETED_DISC_STATUSES),
        )
        .order_by(Disc.id)
    ).first()
    return None, completed



logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _set_setting(session, key: str, value: str) -> None:
    setting = session.get(Setting, key)
    if setting:
        setting.value = value
    else:
        session.add(Setting(key=key, value=value))


def _get_setting_value(session, key: str, default: str) -> str:
    setting = session.get(Setting, key)
    return setting.value if setting else default


def shutdown(cfg: dict) -> None:
    """
    Clean shutdown: kill/roll back any in-flight rip jobs (subprocess
    terminated, scratch dir cleaned, job/disc reset to queued), mark the
    service stopped with a final heartbeat, and clear any pending
    command. Shared by both the remote "exit" command and Ctrl-C.
    """
    Session = get_session_factory(cfg)
    with Session() as session:
        rollback_excess_jobs(session, 0, cfg)

        _set_setting(session, _SERVICE_STATUS_KEY, "stopped")
        _set_setting(session, _SERVICE_HEARTBEAT_KEY, datetime.now(timezone.utc).isoformat())
        _set_setting(session, _SERVICE_COMMAND_KEY, "")

        session.commit()

    logger.info("Clean shutdown complete")


def run(cfg: dict) -> None:
    Session = get_session_factory(cfg)
    media_present_by_device = {}
    tray_open_by_device = {}

    init_useragent(cfg.get("musicbrainz", {}).get("contact_email", "discripper@example.com"))

    _check_datastore_mount(cfg["storage"]["datastore_root"])

    # /tmp-based scratch space may not survive a reboot - make sure it
    # exists before relying on it, rather than assuming manual setup.
    scratch_dir = cfg["storage"]["scratch_dir"]
    if os.path.exists(scratch_dir):
        shutil.rmtree(scratch_dir)
    os.makedirs(scratch_dir)
    logger.info("Scratch directory cleaned and recreated: %s", scratch_dir)

    # Safety default: a restart should never silently resume mass-ripping,
    # regardless of whatever was last persisted.
    with Session() as session:
        setting = session.get(Setting, "ripping_enabled")
        if setting:
            setting.value = "false"
        else:
            session.add(Setting(key="ripping_enabled", value="false"))
        session.commit()
        logger.info("ripping_enabled reset to false on startup")

        # Any RipJob still "running" at startup belonged to a previous
        # instance that's gone - it can't actually still be running, so
        # clean it up via the same rollback mechanism used for manual stop.
        stale_running_count = len(session.scalars(
            select(RipJob).where(RipJob.status == JobStatus.running)
        ).all())
        if stale_running_count:
            logger.warning(
                "%d RipJob(s) found 'running' at startup - the process that "
                "owned them is gone, rolling them back",
                stale_running_count,
            )
            rollback_excess_jobs(session, 0, cfg)

    with Session() as session:
        _set_setting(session, _SERVICE_STATUS_KEY, "running")
        session.commit()
    logger.info("service_status set to running")

    logger.info("Ripper service started (env=%s)", cfg["environment"])

    _last_mb_error_retry: float = 0.0

    while True:
        try:
            with Session() as session:
                drive_states = sync_physical_drives(session, cfg)

            pending_mb_lookups = []   # (mb_disc_id, mb_toc, db_disc_id) for CDs detected this poll
            pending_log_events = []   # (event_type, kwargs) to write after session.commit()
            with Session() as session:
                for drive_cfg in cfg.get("drives", []):
                    if not drive_cfg.get("active", True):
                        continue

                    device_path = drive_cfg["device"]
                    label = drive_cfg.get("label") or device_path
                    state = drive_states.get(device_path, {})
                    drive_id = state.get("drive_id")

                    info = get_drive_info(device_path)
                    media_present = info["media_present"]
                    media_type = info["media_type"]
                    tray_open = tray_open_from_status(get_tray_status(device_path))

                    was_present = media_present_by_device.get(device_path, False)
                    fresh_insert = media_present and not was_present

                    # Reconciliation: if the disc is already present in memory but
                    # has no active DB row (e.g. reset_dev_data.py wiped rows while
                    # the service was running, or a service restart with a disc
                    # already loaded), treat it as a fresh insert so detection
                    # re-runs and a new Disc row is created. The DB query is cheap
                    # in steady state (there IS an active row → returns immediately).
                    if not fresh_insert and media_present and drive_id is not None:
                        has_active_row = session.scalar(
                            select(Disc.id)
                            .where(
                                Disc.drive_id == drive_id,
                                Disc.status.notin_([DiscStatus.done]),
                            )
                            .limit(1)
                        ) is not None
                        if not has_active_row:
                            logger.info(
                                "Drive %s: disc present but no active DB row - reconciling "
                                "(post-reset or restart with disc already loaded)",
                                label,
                            )
                            fresh_insert = True

                    if fresh_insert:
                        logger.info("Disc detected in %s, type=%s", label, media_type)

                        if media_type == "dvd":
                            # Only DVDs are region-locked - CDs play anywhere,
                            # so they're handled below without this gate.
                            if not state.get("region_known", False):
                                logger.info(
                                    "Drive %s has unknown region - disc inserted but will NOT "
                                    "be processed for ripping. Use Read Region in the UI.",
                                    label,
                                )
                            elif drive_id is not None:
                                volume_info = read_volume_info(device_path)
                                volume_label = volume_info["volume_id"] or volume_info["volume_set_id"]
                                # Content-based identity: hash of VIDEO_TS.IFO (region byte
                                # zeroed) + volume space size. Never collides on generic labels.
                                disc_fingerprint = compute_dvd_id(device_path)

                                # A service restart loses media_present_by_device,
                                # so a disc that was never removed looks like a
                                # fresh insert on the next poll. Without an
                                # identifier we can't safely dedup, so fall back
                                # to the old create-new behavior in that case.
                                existing_active_disc, existing_completed_disc = _find_existing_disc(
                                    session, disc_fingerprint,
                                )

                                if existing_active_disc is not None:
                                    existing_active_disc.drive_id = drive_id
                                    session.execute(
                                        update(RipJob)
                                        .where(RipJob.disc_id == existing_active_disc.id)
                                        .where(RipJob.status == JobStatus.queued)
                                        .values(drive_id=drive_id)
                                    )
                                    logger.info(
                                        "Disc %r (%s) already tracked as disc #%s on Drive %s, "
                                        "resuming existing record",
                                        volume_label, disc_fingerprint, existing_active_disc.id, label,
                                    )
                                elif existing_completed_disc is not None and (
                                    existing_completed_disc.status in (DiscStatus.error, DiscStatus.protected)
                                    or existing_completed_disc.rip_quality == "dirty"
                                ):
                                    existing_completed_disc.drive_id = drive_id
                                    if not existing_completed_disc.volume_label and volume_label:
                                        existing_completed_disc.volume_label = volume_label
                                    dvd_cfg = cfg.get("dvd", {})
                                    makemkv_active = (
                                        dvd_cfg.get("rip_method") == "makemkv"
                                        or dvd_cfg.get("makemkv_fallback", False)
                                    )
                                    # Always re-rip dirty discs — dvdbackup alone may improve
                                    # on a re-attempt, and MakeMKV fallback will fire if
                                    # configured.  For error/protected discs, only re-rip when
                                    # MakeMKV is active (dvdbackup can't overcome protection).
                                    is_dirty = existing_completed_disc.rip_quality == "dirty"
                                    if is_dirty or makemkv_active:
                                        existing_completed_disc.status = DiscStatus.queued
                                        existing_completed_disc.rip_attempt_count = (
                                            existing_completed_disc.rip_attempt_count or 1
                                        ) + 1
                                        session.add(RipJob(
                                            disc_id=existing_completed_disc.id,
                                            drive_id=drive_id,
                                            status=JobStatus.queued,
                                        ))
                                        logger.info(
                                            "Re-inserted disc #%s (%s) — queuing new rip job",
                                            existing_completed_disc.id,
                                            "dirty" if is_dirty else "error/protected, MakeMKV active",
                                        )
                                    else:
                                        logger.info(
                                            "Disc %r previously had errors - disc #%s on "
                                            "Drive %s, awaiting user action",
                                            volume_label, existing_completed_disc.id, label,
                                        )
                                elif existing_completed_disc is not None:
                                    existing_completed_disc.drive_id = drive_id
                                    logger.info(
                                        "Disc %r already processed as disc #%s on Drive %s - "
                                        "ignoring reinsertion",
                                        volume_label, existing_completed_disc.id, label,
                                    )
                                else:
                                    disc = Disc(
                                        type=DiscType.dvd,
                                        status=DiscStatus.queued,
                                        drive_id=drive_id,
                                        disc_fingerprint=disc_fingerprint,
                                        # Store the ISO 9660 / UDF volume label separately
                                        # so it can be used for ISO file naming without
                                        # the colon in "ifo:..." causing Windows/Samba issues.
                                        volume_label=volume_label,
                                        # Also use it as the initial working title.
                                        temp_name=volume_label,
                                    )
                                    session.add(disc)

                                    # When compute_dvd_id failed (fingerprint=None), check the
                                    # kernel-cached label (ID_FS_LABEL from udevadm) for a soft
                                    # match against existing disc fingerprints. If found, show
                                    # "possible duplicate of #N" on Drive Status while ripping.
                                    # A label match alone never skips the rip.
                                    if disc_fingerprint is None:
                                        fs_label = info.get("fs_label") or volume_label
                                        if fs_label:
                                            label_match = session.scalar(
                                                select(Disc).where(
                                                    Disc.disc_fingerprint == fs_label,
                                                ).limit(1)
                                            )
                                            if label_match is not None:
                                                disc.possible_duplicate_of_id = label_match.id
                                                logger.info(
                                                    "Drive %s: label %r matches disc #%s — "
                                                    "flagging as possible duplicate (rip will proceed)",
                                                    label, fs_label, label_match.id,
                                                )

                                    session.flush()
                                    session.execute(
                                        update(Disc)
                                        .where(Disc.drive_id == drive_id, Disc.id != disc.id)
                                        .values(drive_id=None)
                                    )

                                    # RipJob starts queued - job_starter promotes
                                    # it to running once ripping_enabled is true
                                    # and a slot is free.
                                    session.add(RipJob(
                                        disc_id=disc.id,
                                        drive_id=drive_id,
                                        status=JobStatus.queued,
                                    ))
                                    logger.info(
                                        "Created disc #%s for %s (fingerprint=%s, label=%s) - "
                                        "rip job queued, waiting for ripping to be enabled",
                                        disc.id, label, disc_fingerprint, volume_label,
                                    )
                                    pending_log_events.append(("disc_inserted", {
                                        "drive_label": label,
                                        "disc_id": disc.id,
                                        "working_title": disc.temp_name,
                                    }))

                        elif media_type == "cd":
                            if drive_id is not None:
                                toc = read_table_of_contents(device_path)
                                disc_fingerprint = toc["fingerprint"]

                                if not disc_fingerprint or not toc.get("track_count"):
                                    logger.warning(
                                        "cd-discid failed for Drive %s (fingerprint=%r, "
                                        "track_count=%r) - will retry next poll",
                                        label, disc_fingerprint, toc.get("track_count"),
                                    )
                                    # Skip disc creation; tray/presence tracking still runs below.
                                else:
                                    existing_active_disc, existing_completed_disc = _find_existing_disc(
                                        session, disc_fingerprint,
                                    )

                                    if existing_active_disc is not None:
                                        existing_active_disc.drive_id = drive_id
                                        session.execute(
                                            update(RipJob)
                                            .where(RipJob.disc_id == existing_active_disc.id)
                                            .where(RipJob.status == JobStatus.queued)
                                            .values(drive_id=drive_id)
                                        )
                                        logger.info(
                                            "Disc %s already tracked as disc #%s on Drive %s, "
                                            "resuming existing record",
                                            disc_fingerprint, existing_active_disc.id, label,
                                        )
                                        # Re-trigger MB lookup if status was cleared or errored.
                                        if existing_active_disc.mb_lookup_status in (None, "error"):
                                            if existing_active_disc.mb_disc_id:
                                                # Disc ID already known from previous attempt
                                                existing_active_disc.mb_lookup_status = "pending"
                                                pending_mb_lookups.append((
                                                    existing_active_disc.mb_disc_id,
                                                    existing_active_disc.mb_toc,
                                                    existing_active_disc.id,
                                                ))
                                            else:
                                                mb_result = compute_mb_disc_id(device_path)
                                                if mb_result.get("disc_id"):
                                                    existing_active_disc.mb_disc_id = mb_result["disc_id"]
                                                    existing_active_disc.mb_toc = mb_result["toc"]
                                                    existing_active_disc.mb_lookup_status = "pending"
                                                    pending_mb_lookups.append(
                                                        (mb_result["disc_id"], mb_result["toc"], existing_active_disc.id)
                                                    )
                                                else:
                                                    existing_active_disc.mb_lookup_status = "error"
                                                    logger.warning(
                                                        "Failed to compute MB disc ID for existing disc #%s: %s",
                                                        existing_active_disc.id, mb_result.get("error"),
                                                    )
                                    elif existing_completed_disc is not None and (
                                        existing_completed_disc.status == DiscStatus.error
                                        or existing_completed_disc.rip_quality == "dirty"
                                    ):
                                        existing_completed_disc.drive_id = drive_id
                                        logger.info(
                                            "Disc %s previously had errors/dirty rip - disc #%s on "
                                            "Drive %s, awaiting user action",
                                            disc_fingerprint, existing_completed_disc.id, label,
                                        )
                                        if (
                                            existing_completed_disc.mb_lookup_status in (None, "error")
                                            and existing_completed_disc.mb_disc_id
                                        ):
                                            existing_completed_disc.mb_lookup_status = "pending"
                                            pending_mb_lookups.append((
                                                existing_completed_disc.mb_disc_id,
                                                existing_completed_disc.mb_toc,
                                                existing_completed_disc.id,
                                            ))
                                    elif existing_completed_disc is not None:
                                        existing_completed_disc.drive_id = drive_id
                                        logger.info(
                                            "Disc %s already processed as disc #%s on Drive %s - "
                                            "ignoring reinsertion",
                                            disc_fingerprint, existing_completed_disc.id, label,
                                        )
                                        if (
                                            existing_completed_disc.mb_lookup_status in (None, "error")
                                            and existing_completed_disc.mb_disc_id
                                        ):
                                            existing_completed_disc.mb_lookup_status = "pending"
                                            pending_mb_lookups.append((
                                                existing_completed_disc.mb_disc_id,
                                                existing_completed_disc.mb_toc,
                                                existing_completed_disc.id,
                                            ))
                                    else:
                                        disc = Disc(
                                            type=DiscType.cd,
                                            status=DiscStatus.queued,
                                            drive_id=drive_id,
                                            disc_fingerprint=disc_fingerprint,
                                        )
                                        session.add(disc)
                                        session.flush()
                                        session.execute(
                                            update(Disc)
                                            .where(Disc.drive_id == drive_id, Disc.id != disc.id)
                                            .values(drive_id=None)
                                        )

                                        for track in toc["tracks"]:
                                            duration = track["length_sectors"] / 75.0 if track["length_sectors"] else None
                                            session.add(CDTrack(
                                                disc_id=disc.id,
                                                track_number=track["number"],
                                                duration_seconds=duration,
                                            ))

                                        session.add(RipJob(
                                            disc_id=disc.id,
                                            drive_id=drive_id,
                                            status=JobStatus.queued,
                                        ))

                                        mb_result = compute_mb_disc_id(device_path)
                                        if mb_result.get("disc_id"):
                                            disc.mb_disc_id = mb_result["disc_id"]
                                            disc.mb_toc = mb_result["toc"]
                                            disc.mb_lookup_status = "pending"
                                            pending_mb_lookups.append(
                                                (mb_result["disc_id"], mb_result["toc"], disc.id)
                                            )
                                        else:
                                            disc.mb_lookup_status = "error"
                                            logger.warning(
                                                "Failed to compute MB disc ID for disc #%s: %s",
                                                disc.id, mb_result.get("error"),
                                            )

                                        logger.info(
                                            "Created disc #%s for %s (CD, %s tracks, fingerprint=%s) - "
                                            "rip job queued, waiting for ripping to be enabled",
                                            disc.id, label, toc["track_count"], disc_fingerprint,
                                        )
                                        pending_log_events.append(("disc_inserted", {
                                            "drive_label": label,
                                            "disc_id": disc.id,
                                            "working_title": disc.temp_name,
                                        }))

                        else:
                            logger.info(
                                "Drive %s: media_type=%s - skipping job creation "
                                "(unsupported media type)",
                                label, media_type,
                            )
                    elif not media_present and was_present:
                        logger.info("Disc removed from %s", label)
                        if drive_id is not None:
                            removed_disc = session.scalars(
                                select(Disc)
                                .where(Disc.drive_id == drive_id)
                                .order_by(Disc.created_at.desc())
                                .limit(1)
                            ).first()
                            if removed_disc is not None:
                                removed_disc.drive_id = None
                                session.commit()
                                logger.info(
                                    "Dissociated disc #%s (%s) from Drive %s on removal",
                                    removed_disc.id, removed_disc.disc_fingerprint, label,
                                )

                    media_present_by_device[device_path] = media_present

                    # Detect tray-open transition and cancel any active rip job
                    # so it doesn't run against a disc that's no longer present.
                    tray_was_open = tray_open_by_device.get(device_path, False)
                    if tray_open and not tray_was_open and drive_id is not None:
                        active_rip_job = session.scalars(
                            select(RipJob)
                            .where(
                                RipJob.drive_id == drive_id,
                                RipJob.status.in_([JobStatus.queued, JobStatus.running]),
                            )
                        ).first()
                        if active_rip_job is not None:
                            if active_rip_job.status == JobStatus.running:
                                logger.warning(
                                    "Tray opened on %s while ripping — aborting rip job %s",
                                    label, active_rip_job.id,
                                )
                                rollback_job(session, active_rip_job, cfg)
                            else:
                                active_rip_job.scheduled_start = None
                                if active_rip_job.disc is not None:
                                    active_rip_job.disc.status = DiscStatus.queued
                                session.commit()
                                logger.info(
                                    "Tray opened on %s while job was queued — pausing until disc reseated",
                                    label,
                                )
                    if not tray_open and tray_was_open and not media_present and drive_id is not None:
                        session.execute(
                            update(Disc)
                            .where(Disc.drive_id == drive_id)
                            .values(drive_id=None)
                        )
                        session.commit()
                        logger.info("Drive %s tray closed empty - cleared stale disc association", label)

                    tray_open_by_device[device_path] = tray_open

                    # Persist current media presence and tray state so the
                    # API/UI (which has no direct hardware access) can
                    # reflect them.
                    if drive_id is not None:
                        drive = session.get(Drive, drive_id)
                        if drive is not None:
                            drive.media_present = media_present
                            drive.tray_open = tray_open

                session.commit()

            _discogs_token = cfg.get("discogs", {}).get("token")
            for mb_disc_id, mb_toc, db_disc_id in pending_mb_lookups:
                submitted = submit_lookup(
                    ("mb", db_disc_id),
                    lookup_musicbrainz,
                    mb_disc_id, mb_toc, db_disc_id, Session,
                    discogs_token=_discogs_token,
                )
                if submitted:
                    logger.info(
                        "MusicBrainz lookup queued for disc #%s (MB disc ID: %s)",
                        db_disc_id, mb_disc_id,
                    )

            # Pick up discs set to "pending" by the API retry endpoint
            # (not detected this poll, so absent from pending_mb_lookups).
            with Session() as session:
                api_pending = session.scalars(
                    select(Disc)
                    .where(
                        Disc.type == DiscType.cd,
                        Disc.mb_lookup_status == "pending",
                        Disc.mb_disc_id.isnot(None),
                    )
                ).all()
                for disc in api_pending:
                    submitted = submit_lookup(
                        ("mb", disc.id),
                        lookup_musicbrainz,
                        disc.mb_disc_id, disc.mb_toc, disc.id, Session,
                        discogs_token=_discogs_token,
                    )
                    if submitted:
                        logger.info(
                            "MusicBrainz lookup queued for pending disc #%s (MB disc ID: %s)",
                            disc.id, disc.mb_disc_id,
                        )

            # Periodic background retry of discs stuck in "error" state
            _now = time.monotonic()
            if _now - _last_mb_error_retry >= 300.0:
                _last_mb_error_retry = _now
                with Session() as session:
                    error_discs = session.scalars(
                        select(Disc)
                        .where(
                            Disc.type == DiscType.cd,
                            Disc.mb_lookup_status == "error",
                            Disc.mb_disc_id.isnot(None),
                        )
                    ).all()
                    if error_discs:
                        for disc in error_discs:
                            disc.mb_lookup_status = "pending"
                        session.commit()
                        logger.info(
                            "Periodic MB retry: %d disc(s) with error status re-queued",
                            len(error_discs),
                        )
                        for disc in error_discs:
                            submit_lookup(
                                ("mb", disc.id),
                                lookup_musicbrainz,
                                disc.mb_disc_id, disc.mb_toc, disc.id, Session,
                                discogs_token=_discogs_token,
                            )

            for event_type, kwargs in pending_log_events:
                write_log_event(Session, event_type, **kwargs)

            with Session() as session:
                process_pending_actions(session, cfg, Session)

            with Session() as session:
                start_eligible_rip_jobs(session, cfg, Session)

            with Session() as session:
                _set_setting(session, _SERVICE_HEARTBEAT_KEY, datetime.now(timezone.utc).isoformat())
                session.commit()
                command = _get_setting_value(session, _SERVICE_COMMAND_KEY, "")

            if command == "exit":
                logger.info("Received exit command - shutting down cleanly")
                shutdown(cfg)
                return

        except Exception:
            logger.exception("Error during poll iteration - continuing")

        time.sleep(POLL_INTERVAL_SECONDS)


def main() -> None:
    cfg = load_config(os.environ.get("DISCRIPPER_ENV"))
    try:
        run(cfg)
    except KeyboardInterrupt:
        logger.info("Ripper service stopping (KeyboardInterrupt)")
        shutdown(cfg)


if __name__ == "__main__":
    main()
