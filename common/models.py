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
from datetime import datetime

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


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class DiscType(str, enum.Enum):
    cd = "cd"
    dvd = "dvd"


class DiscStatus(str, enum.Enum):
    queued = "queued"
    ripping = "ripping"
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

class Drive(Base):
    __tablename__ = "drives"

    id = Column(Integer, primary_key=True)
    device_path = Column(String, nullable=False)       # e.g. /dev/sr0
    env = Column(String, nullable=False)               # "dev" or "prod"
    drive_type = Column(Enum(DiscType), nullable=True)
    label = Column(String, nullable=True)
    active = Column(Boolean, default=True, nullable=False)

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

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    log = Column(Text, nullable=True)

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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    disc = relationship("Disc", back_populates="encode_jobs")
    profile = relationship("EncodeProfile", back_populates="encode_jobs")
