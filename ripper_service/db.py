"""
SQLAlchemy session factory for the ripper service.

Mirrors the engine/session setup in api/app.py, but returns a plain
sessionmaker instead of Flask's scoped_session, since the ripper service
is a single-threaded polling loop.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from common.config import get_db_url


def get_session_factory(cfg: dict) -> sessionmaker:
    """Build a sessionmaker bound to the database described by `cfg`."""
    engine = create_engine(get_db_url(cfg))
    return sessionmaker(bind=engine)
