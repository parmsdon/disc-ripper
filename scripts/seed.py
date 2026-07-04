"""
Seed script - populates initial reference data:
  - encode_profiles (Phase 2/3 targets; safe to run now, unused until then)
  - drives (from config/<env>.yaml drive list)
  - settings (default values; skipped if already set)

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
from common.models import Drive, EncodeProfile, DiscType, Setting

# depends_on_name is resolved at seed time to depends_on_profile_id;
# profiles must appear before any profile that depends on them.
ENCODE_PROFILES = [
    {
        "name": "DVD Extract",
        "media_type": "dvd",
        "output_folder": "dvd_store/extract",
        "tool": "handbrake",
        "tool_params": '{"quality": 18, "encoder": "x264", "extra_args": ["--main-feature", "--no-dvdnav", "-f", "av_mkv", "--all-audio", "--aencoder", "copy", "--audio-copy-mask", "aac,ac3,eac3,truehd,dts,dtshd,mp3,flac", "--audio-fallback", "ac3", "--all-subtitles"]}',
        "depends_on_name": None,
        "enabled": True,
        "display_order": 1,
    },
    {
        "name": "DVD Plex",
        "media_type": "dvd",
        "output_folder": "dvd_store/plex",
        "tool": "handbrake",
        "tool_params": '{"preset": "Xbox 1080p30 Surround"}',
        "depends_on_name": "DVD Extract",
        "enabled": True,
        "display_order": 2,
    },
    {
        "name": "DVD iPhone",
        "media_type": "dvd",
        "output_folder": "dvd_store/iphone",
        "tool": "handbrake",
        "tool_params": '{"preset": "Apple 1080p30 Surround"}',
        "depends_on_name": "DVD Extract",
        "enabled": True,
        "display_order": 3,
    },
    {
        "name": "CD FLAC",
        "media_type": "cd",
        "output_folder": "cd_store/flac",
        "tool": "flac",
        "tool_params": '{"compression": 8}',
        "depends_on_name": None,
        "enabled": True,
        "display_order": 4,
    },
    {
        "name": "CD MP3 320",
        "media_type": "cd",
        "output_folder": "cd_store/mp3_320",
        "tool": "ffmpeg",
        "tool_params": '{"bitrate": "320k", "codec": "libmp3lame"}',
        "depends_on_name": None,
        "enabled": True,
        "display_order": 5,
    },
]

DEFAULT_SETTINGS = [
    {"key": "max_rippers", "value": "1"},
    {"key": "fake_rip_mode", "value": "false"},
    {"key": "fake_dirty_mode", "value": "false"},
    {"key": "ripping_enabled", "value": "false"},
    # Reflects reality at rest - nothing is running until a service starts.
    {"key": "service_status", "value": "stopped"},
    {"key": "service_command", "value": ""},
    {"key": "service_heartbeat", "value": ""},
    {"key": "dvd_encoding_enabled", "value": "false"},
    {"key": "cd_encoding_enabled", "value": "false"},
    {"key": "max_dvd_encoders", "value": "1"},
    {"key": "max_cd_encoders", "value": "2"},
    {"key": "encoder_service_status", "value": "stopped"},
    {"key": "encoder_service_command", "value": ""},
    {"key": "encoder_service_heartbeat", "value": ""},
]


def seed(env: str):
    cfg = load_config(env)
    engine = create_engine(get_db_url(cfg))
    Session = sessionmaker(bind=engine)
    session = Session()

    # Encode profiles — flush after each insert/update so depends_on_profile_id
    # can reference IDs that were just created in this run.
    # Profiles are always updated (not skipped) so tool_params changes land.
    name_to_id: dict[str, int] = {}
    for profile_data in ENCODE_PROFILES:
        depends_on_name = profile_data.get("depends_on_name")
        depends_on_id = name_to_id.get(depends_on_name) if depends_on_name else None

        existing = session.scalar(
            select(EncodeProfile).where(EncodeProfile.name == profile_data["name"])
        )
        if existing:
            existing.media_type           = profile_data["media_type"]
            existing.output_folder        = profile_data["output_folder"]
            existing.tool                 = profile_data["tool"]
            existing.tool_params          = profile_data["tool_params"]
            existing.depends_on_profile_id = depends_on_id
            existing.enabled              = profile_data["enabled"]
            existing.display_order        = profile_data["display_order"]
            session.flush()
            name_to_id[existing.name] = existing.id
            print(f"Updated encode profile '{existing.name}' (id={existing.id})")
            continue

        data = {k: v for k, v in profile_data.items() if k != "depends_on_name"}
        data["depends_on_profile_id"] = depends_on_id
        profile = EncodeProfile(**data)
        session.add(profile)
        session.flush()
        name_to_id[profile.name] = profile.id
        print(f"Added encode profile '{profile.name}' (id={profile.id})")

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
