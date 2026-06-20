"""
Settings API.

Generic key/value app settings. Currently only max_rippers (ripper
concurrency control) is exposed.
"""

from flask import Blueprint, jsonify, current_app, request

from common.models import Setting

settings_bp = Blueprint("settings", __name__)

_MAX_RIPPERS_KEY = "max_rippers"
_DEFAULT_MAX_RIPPERS = 1


@settings_bp.route("/max-rippers", methods=["GET"])
def get_max_rippers():
    Session = current_app.config["DB_SESSION"]
    session = Session()

    setting = session.get(Setting, _MAX_RIPPERS_KEY)
    value = int(setting.value) if setting else _DEFAULT_MAX_RIPPERS
    return jsonify({"max_rippers": value})


@settings_bp.route("/max-rippers", methods=["PUT"])
def set_max_rippers():
    Session = current_app.config["DB_SESSION"]
    session = Session()

    body = request.get_json(silent=True) or {}
    raw = body.get("max_rippers")

    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
        return jsonify({"error": "max_rippers must be a positive integer >= 1"}), 400

    setting = session.get(Setting, _MAX_RIPPERS_KEY)
    if setting:
        setting.value = str(raw)
    else:
        session.add(Setting(key=_MAX_RIPPERS_KEY, value=str(raw)))

    session.commit()
    return jsonify({"max_rippers": raw})
