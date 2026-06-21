"""
SQLAlchemy models for Disc Ripper.

Schema covers:
- drives: physical optical drives and their environment/type assignment
- discs: a physical disc that has been (or is being) ripped
- catalog: synced from My Movies, represents known movies/series
- cd_tracks: per-track info for CDs
- lookup_candidates: CDDB/MusicBrainz candidate matches awaiting user selection
- encode_profiles: configurable encoding targets (mp3/flac/mp4/mkv etc.)
- rip_jobs: queue/status for ripping (DVD->ISO, CD->WAV)
- encode_jobs: queue/status for encoding (WAV->MP3/FLAC, ISO->MP4/MKV)
"""

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    Enum,
    Float,
    JSON,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def naive_utcnow() -> datetime:
    """
    Current UTC time as a naive datetime, for use as a default/value for
    these models' DateTime columns (all "timestamp without time zone").

    Deliberately not datetime.now(timezone.utc) directly: psycopg2
    converts aware datetimes to the Postgres session's TimeZone setting
    before storing them in a tz-naive column, silently shifting the
    wall-clock value (e.g. +1h for Europe/London in BST). Stripping
    tzinfo here keeps the stored value as true UTC, matching what the
    deprecated datetime.utcnow() this replaces always stored.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class DiscType(str, enum.Enum):
    cd = "cd"
    dvd = "dvd"


class DiscStatus(str, enum.Enum):
    queued = "queued"
    ripping = "ripping"
    building = "building"
    identifying = "identifying"
    ripped = "ripped"
    encoding = "encoding"
    done = "done"
    error = "error"


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    done = "done"
    error = "error"


class RipQuality(str, enum.Enum):
    good = "good"
    imperfect = "imperfect"
    failed = "failed"


class MediaType(str, enum.Enum):
    movie = "movie"
    series = "series"


class EncodeTarget(str, enum.Enum):
    audio = "audio"
    video = "video"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class PhysicalDrive(Base):
    """
    Physical optical drive hardware, keyed by a stable identifier (e.g.
    udevadm ID_SERIAL) rather than device path, since device paths can
    shift between reboots or be reassigned between dev/prod.
    """
    __tablename__ = "physical_drives"

    id = Column(Integer, primary_key=True)
    hardware_id = Column(String, nullable=False, unique=True)
    # Space-separated region digits the drive is capable of playing, e.g.
    # "2" for a locked drive or "1 2 3 4 5 6 7 8" for a region-free drive.
    # Not a single integer - regionset can report multiple supported
    # regions. null = unknown.
    region = Column(String, nullable=True)
    region_known = Column(Boolean, nullable=False, default=False)
    last_seen_at = Column(DateTime, nullable=True)
    notes = Column(String, nullable=True)           # e.g. "Samsung SH-224 - replaced 2026-06"

    drives = relationship("Drive", back_populates="physical_drive")


class Drive(Base):
    __tablename__ = "drives"

    id = Column(Integer, primary_key=True)
    device_path = Column(String, nullable=False)       # e.g. /dev/sr0
    env = Column(String, nullable=False)               # "dev" or "prod"
    drive_type = Column(Enum(DiscType), nullable=True)
    label = Column(String, nullable=True)
    active = Column(Boolean, default=True, nullable=False)
    physical_drive_id = Column(Integer, ForeignKey("physical_drives.id"), nullable=True)

    # Whether a disc is currently physically present, as last observed by
    # the ripper service's udev poll. Null = not yet observed (e.g. no
    # ripper service has polled this drive yet).
    media_present = Column(Boolean, nullable=True)

    # Whether the tray is physically open, as last observed by the ripper
    # service's CDROM_DRIVE_STATUS ioctl poll. Null = unknown/not yet checked.
    tray_open = Column(Boolean, nullable=True)

    # DB-based command queue: the API sets these, the ripper service picks
    # them up on its next poll and clears them once executed.
    pending_action = Column(String, nullable=True)     # "read_region" or "eject", null = none
    pending_action_requested_at = Column(DateTime, nullable=True)

    physical_drive = relationship("PhysicalDrive", back_populates="drives")
    rip_jobs = relationship("RipJob", back_populates="drive")
    discs = relationship("Disc", back_populates="drive")


class Catalog(Base):
    """Synced from My Movies (read-only source of truth)."""
    __tablename__ = "catalog"

    id = Column(Integer, primary_key=True)
    mymovies_id = Column(String, nullable=True, unique=True, index=True)
    title = Column(String, nullable=False)
    year = Column(Integer, nullable=True)
    media_type = Column(Enum(MediaType), default=MediaType.movie, nullable=False)
    raw_metadata = Column(JSON, nullable=True)
    synced_at = Column(DateTime, nullable=True)

    discs = relationship("Disc", back_populates="catalog")


class Disc(Base):
    __tablename__ = "discs"

    id = Column(Integer, primary_key=True)
    type = Column(Enum(DiscType), nullable=False)
    status = Column(Enum(DiscStatus), default=DiscStatus.queued, nullable=False)

    drive_id = Column(Integer, ForeignKey("drives.id"), nullable=True)
    catalog_id = Column(Integer, ForeignKey("catalog.id"), nullable=True)

    created_at = Column(DateTime, default=naive_utcnow, nullable=False)
    ripped_at = Column(DateTime, nullable=True)

    raw_path = Column(String, nullable=True)           # path under datastore, e.g. dvd_store/raw/123
    error_message = Column(Text, nullable=True)

    # Disc fingerprint:
    #   CD  -> CDDB/MusicBrainz disc ID (computed from track lengths/offsets)
    #   DVD -> Volume ID / Volume Set ID from the ISO9660/UDF filesystem
    disc_fingerprint = Column(String, nullable=True, index=True)

    # CD-specific album info (compilations use album_artist = "Various")
    album_title = Column(String, nullable=True)
    album_artist = Column(String, nullable=True)

    # Set true if any track came back imperfect/failed during ripping,
    # so a re-rip can be triggered next time the disc is inserted.
    needs_rerip = Column(Boolean, default=False, nullable=False)
    temp_name = Column(String, nullable=True)

    # Region(s) the drive supported at the time this disc was ripped, as a
    # space-separated digit string (same format as PhysicalDrive.region).
    # Captured as a snapshot for historical record even though the drive's
    # region could theoretically change later (discouraged, but possible).
    ripped_in_region = Column(String, nullable=True)

    drive = relationship("Drive", back_populates="discs")
    catalog = relationship("Catalog", back_populates="discs")
    tracks = relationship("CDTrack", back_populates="disc", cascade="all, delete-orphan")
    lookup_candidates = relationship("LookupCandidate", back_populates="disc", cascade="all, delete-orphan")
    rip_jobs = relationship("RipJob", back_populates="disc", cascade="all, delete-orphan")
    encode_jobs = relationship("EncodeJob", back_populates="disc", cascade="all, delete-orphan")


class CDTrack(Base):
    __tablename__ = "cd_tracks"

    id = Column(Integer, primary_key=True)
    disc_id = Column(Integer, ForeignKey("discs.id"), nullable=False)

    track_number = Column(Integer, nullable=False)
    title = Column(String, nullable=True)
    artist = Column(String, nullable=True)      # per-track artist, for compilations
    duration_seconds = Column(Float, nullable=True)
    wav_filename = Column(String, nullable=True)

    rip_quality = Column(Enum(RipQuality), nullable=True)
    rip_log = Column(Text, nullable=True)

    disc = relationship("Disc", back_populates="tracks")


class LookupCandidate(Base):
    """CDDB/MusicBrainz candidate match for a disc, awaiting user selection."""
    __tablename__ = "lookup_candidates"

    id = Column(Integer, primary_key=True)
    disc_id = Column(Integer, ForeignKey("discs.id"), nullable=False)

    source = Column(String, nullable=False)     # "musicbrainz" or "cddb"
    candidate_data = Column(JSON, nullable=False)
    selected = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=naive_utcnow, nullable=False)

    disc = relationship("Disc", back_populates="lookup_candidates")


class EncodeProfile(Base):
    __tablename__ = "encode_profiles"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    target = Column(Enum(EncodeTarget), nullable=False)
    format = Column(String, nullable=False)       # mp3, flac, mp4, mkv, etc.
    params = Column(JSON, nullable=True)          # bitrate, codec settings, etc.
    output_subfolder = Column(String, nullable=False)

    encode_jobs = relationship("EncodeJob", back_populates="profile")


class RipJob(Base):
    __tablename__ = "rip_jobs"

    id = Column(Integer, primary_key=True)
    disc_id = Column(Integer, ForeignKey("discs.id"), nullable=False)
    drive_id = Column(Integer, ForeignKey("drives.id"), nullable=True)

    status = Column(Enum(JobStatus), default=JobStatus.queued, nullable=False)
    created_at = Column(DateTime, default=naive_utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    log = Column(Text, nullable=True)

    # When the countdown ends and ripping is allowed to begin. Recomputed
    # if the job is rolled back to queued (e.g. max_rippers decreased).
    scheduled_start = Column(DateTime, nullable=True)

    # Live progress, parsed from dvdbackup/mkisofs stdout while running.
    progress_percent = Column(Integer, nullable=True)
    progress_stage = Column(String, nullable=True)     # e.g. "Copying Title, part 1/4"

    disc = relationship("Disc", back_populates="rip_jobs")
    drive = relationship("Drive", back_populates="rip_jobs")


class EncodeJob(Base):
    __tablename__ = "encode_jobs"

    id = Column(Integer, primary_key=True)
    disc_id = Column(Integer, ForeignKey("discs.id"), nullable=False)
    profile_id = Column(Integer, ForeignKey("encode_profiles.id"), nullable=False)

    status = Column(Enum(JobStatus), default=JobStatus.queued, nullable=False)
    source_file = Column(String, nullable=True)
    output_path = Column(String, nullable=True)
    created_at = Column(DateTime, default=naive_utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    disc = relationship("Disc", back_populates="encode_jobs")
    profile = relationship("EncodeProfile", back_populates="encode_jobs")


class Setting(Base):
    """Generic key/value app settings (e.g. max_rippers)."""
    __tablename__ = "settings"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)
