"""
Encode Profiles API.

Lets the UI list/configure the available encoding targets
(mp3/flac for CDs, mp4/mkv for DVDs in later phases).
"""

from flask import Blueprint, jsonify, current_app
from sqlalchemy import select

from common.models import EncodeProfile

encode_profiles_bp = Blueprint("encode_profiles", __name__)


@encode_profiles_bp.route("/", methods=["GET"])
def list_profiles():
    Session = current_app.config["DB_SESSION"]
    session = Session()

    profiles = session.scalars(select(EncodeProfile)).all()
    return jsonify([
        {
            "id": p.id,
            "name": p.name,
            "target": p.target.value if p.target else None,
            "format": p.format,
            "params": p.params,
            "output_subfolder": p.output_subfolder,
        }
        for p in profiles
    ])
