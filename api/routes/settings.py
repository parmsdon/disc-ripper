"""
Settings API.

Generic key/value app settings: max_rippers (ripper concurrency control)
and fake_rip_mode (dev-only debug toggle - see set_fake_rip_mode below
for why prod is hard-blocked from ever enabling it).
"""

from flask import Blueprint, jsonify, current_app, request

from common.models import Setting

settings_bp = Blueprint("settings", __name__)

_MAX_RIPPERS_KEY = "max_rippers"
_DEFAULT_MAX_RIPPERS = 1

_FAKE_RIP_MODE_KEY = "fake_rip_mode"
_DEFAULT_FAKE_RIP_MODE = False

_RIPPING_ENABLED_KEY = "ripping_enabled"
_DEFAULT_RIPPING_ENABLED = False


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


@settings_bp.route("/fake-rip-mode", methods=["GET"])
def get_fake_rip_mode():
    Session = current_app.config["DB_SESSION"]
    session = Session()

    setting = session.get(Setting, _FAKE_RIP_MODE_KEY)
    value = (setting.value == "true") if setting else _DEFAULT_FAKE_RIP_MODE
    return jsonify({"fake_rip_mode": value})


@settings_bp.route("/fake-rip-mode", methods=["PUT"])
def set_fake_rip_mode():
    cfg = current_app.config["DISCRIPPER_CFG"]

    # Hard safety backstop, independent of what the UI shows: fake_rip_mode
    # must never be settable in prod, full stop - checked before even
    # looking at the request body.
    if cfg["environment"] == "prod":
        return jsonify({"error": "fake_rip_mode cannot be changed in prod"}), 403

    Session = current_app.config["DB_SESSION"]
    session = Session()

    body = request.get_json(silent=True) or {}
    raw = body.get("fake_rip_mode")

    if not isinstance(raw, bool):
        return jsonify({"error": "fake_rip_mode must be a boolean"}), 400

    value_str = "true" if raw else "false"
    setting = session.get(Setting, _FAKE_RIP_MODE_KEY)
    if setting:
        setting.value = value_str
    else:
        session.add(Setting(key=_FAKE_RIP_MODE_KEY, value=value_str))

    session.commit()
    return jsonify({"fake_rip_mode": raw})


@settings_bp.route("/ripping-enabled", methods=["GET"])
def get_ripping_enabled():
    Session = current_app.config["DB_SESSION"]
    session = Session()

    setting = session.get(Setting, _RIPPING_ENABLED_KEY)
    value = (setting.value == "true") if setting else _DEFAULT_RIPPING_ENABLED
    return jsonify({"ripping_enabled": value})


@settings_bp.route("/ripping-enabled", methods=["PUT"])
def set_ripping_enabled():
    Session = current_app.config["DB_SESSION"]
    session = Session()

    body = request.get_json(silent=True) or {}
    raw = body.get("ripping_enabled")

    if not isinstance(raw, bool):
        return jsonify({"error": "ripping_enabled must be a boolean"}), 400

    value_str = "true" if raw else "false"
    setting = session.get(Setting, _RIPPING_ENABLED_KEY)
    if setting:
        setting.value = value_str
    else:
        session.add(Setting(key=_RIPPING_ENABLED_KEY, value=value_str))

    session.commit()
    return jsonify({"ripping_enabled": raw})
