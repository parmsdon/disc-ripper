"""
Seed script - populates initial reference data:
  - encode_profiles (Phase 2/3 targets; safe to run now, unused until then)
  - drives (from config/<env>.yaml drive list)

Usage:
    DISCRIPPER_ENV=dev python3 scripts/seed.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from common.config import load_config, get_db_url
from common.models import Drive, EncodeProfile, DiscType, EncodeTarget, Setting


DEFAULT_ENCODE_PROFILES = [
    {
        "name": "flac",
        "target": EncodeTarget.audio,
        "format": "flac",
        "params": {"compression_level": 5},
        "output_subfolder": "flac",
    },
    {
        "name": "mp3_320",
        "target": EncodeTarget.audio,
        "format": "mp3",
        "params": {"bitrate_kbps": 320},
        "output_subfolder": "mp3_320",
    },
]

DEFAULT_SETTINGS = [
    {"key": "max_rippers", "value": "1"},
    {"key": "fake_rip_mode", "value": "false"},
    {"key": "ripping_enabled", "value": "false"},
    # Reflects reality at rest - nothing is running until a service starts.
    {"key": "service_status", "value": "stopped"},
    {"key": "service_command", "value": ""},
    {"key": "service_heartbeat", "value": ""},
]


def seed(env: str):
    cfg = load_config(env)
    engine = create_engine(get_db_url(cfg))
    Session = sessionmaker(bind=engine)
    session = Session()

    # Encode profiles
    for profile_data in DEFAULT_ENCODE_PROFILES:
        existing = session.scalar(
            select(EncodeProfile).where(EncodeProfile.name == profile_data["name"])
        )
        if existing:
            print(f"Encode profile '{profile_data['name']}' already exists, skipping")
            continue
        session.add(EncodeProfile(**profile_data))
        print(f"Added encode profile '{profile_data['name']}'")

    # Settings
    for setting_data in DEFAULT_SETTINGS:
        existing = session.scalar(
            select(Setting).where(Setting.key == setting_data["key"])
        )
        if existing:
            print(f"Setting '{setting_data['key']}' already exists, skipping")
            continue
        session.add(Setting(**setting_data))
        print(f"Added setting '{setting_data['key']}' = '{setting_data['value']}'")

    # Drives from config
    for drive_cfg in cfg.get("drives", []):
        existing = session.scalar(
            select(Drive).where(
                Drive.device_path == drive_cfg["device"],
                Drive.env == cfg["environment"],
            )
        )
        raw_type = drive_cfg.get("type")
        drive_type = DiscType(raw_type) if raw_type else None

        if existing:
            existing.label = drive_cfg.get("label")
            existing.drive_type = drive_type
            existing.active = drive_cfg.get("active", True)
            print(f"Updated drive '{drive_cfg['device']}'")
            continue

        session.add(Drive(
            device_path=drive_cfg["device"],
            env=cfg["environment"],
            drive_type=drive_type,
            label=drive_cfg.get("label"),
            active=drive_cfg.get("active", True),
        ))
        print(f"Added drive '{drive_cfg['device']}'")

    session.commit()
    print("Seed complete.")


if __name__ == "__main__":
    import os
    seed(os.environ.get("DISCRIPPER_ENV", "dev"))
