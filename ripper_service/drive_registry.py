"""
Syncs configured drives (config/<env>.yaml) and their physical hardware
identity (PhysicalDrive, keyed by udevadm hardware_id) into the database.

Safe to call repeatedly - the main loop re-syncs periodically so drives
added/removed from config are picked up without restarting the service.
"""

from datetime import datetime

from sqlalchemy import select

from common.models import Drive, PhysicalDrive, DiscType
from ripper_service.udev_helper import get_drive_info


def sync_physical_drives(session, cfg: dict) -> dict:
    """
    Reconcile cfg["drives"] against the drives/physical_drives tables.

    For each active configured drive: look up or create its Drive row
    (by device_path + env, mirroring scripts/seed.py), look up or create
    the matching PhysicalDrive row (by hardware_id) and refresh
    last_seen_at, and link Drive.physical_drive_id if not already set.

    Returns {device_path: {"drive_id", "physical_drive_id", "region",
    "region_known"}} for every active drive in config.
    """
    env = cfg["environment"]
    result = {}

    for drive_cfg in cfg.get("drives", []):
        if not drive_cfg.get("active", True):
            continue

        device_path = drive_cfg["device"]
        raw_type = drive_cfg.get("type")
        drive_type = DiscType(raw_type) if raw_type else None

        drive = session.scalar(
            select(Drive).where(Drive.device_path == device_path, Drive.env == env)
        )
        if drive is None:
            drive = Drive(device_path=device_path, env=env)
            session.add(drive)
        drive.label = drive_cfg.get("label")
        drive.drive_type = drive_type
        drive.active = True

        hardware_id = get_drive_info(device_path)["hardware_id"]

        physical_drive = session.scalar(
            select(PhysicalDrive).where(PhysicalDrive.hardware_id == hardware_id)
        )
        if physical_drive is None:
            physical_drive = PhysicalDrive(hardware_id=hardware_id, region_known=False)
            session.add(physical_drive)
        physical_drive.last_seen_at = datetime.utcnow()

        if drive.physical_drive_id is None:
            drive.physical_drive = physical_drive

        session.flush()

        result[device_path] = {
            "drive_id": drive.id,
            "physical_drive_id": drive.physical_drive_id,
            "region": physical_drive.region,
            "region_known": physical_drive.region_known,
        }

    session.commit()
    return result
