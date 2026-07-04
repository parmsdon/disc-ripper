"""
Settings API.

Generic key/value app settings: max_rippers (ripper concurrency control),
fake_rip_mode, and fake_dirty_mode (dev-only debug toggles - see
set_fake_rip_mode/set_fake_dirty_mode below for why prod is hard-blocked
from ever enabling them).
"""

from flask import Blueprint, jsonify, current_app, request

from common.models import Setting

settings_bp = Blueprint("settings", __name__)

_MAX_RIPPERS_KEY = "max_rippers"
_DEFAULT_MAX_RIPPERS = 1

_FAKE_RIP_MODE_KEY = "fake_rip_mode"
_DEFAULT_FAKE_RIP_MODE = False

_FAKE_DIRTY_MODE_KEY = "fake_dirty_mode"
_DEFAULT_FAKE_DIRTY_MODE = False

_RIPPING_ENABLED_KEY = "ripping_enabled"
_DEFAULT_RIPPING_ENABLED = False

_SERVICE_STATUS_KEY = "service_status"
_DEFAULT_SERVICE_STATUS = "stopped"
_SERVICE_HEARTBEAT_KEY = "service_heartbeat"
_SERVICE_COMMAND_KEY = "service_command"

_DVD_ENCODING_ENABLED_KEY = "dvd_encoding_enabled"
_CD_ENCODING_ENABLED_KEY = "cd_encoding_enabled"
_MAX_DVD_ENCODERS_KEY = "max_dvd_encoders"
_MAX_CD_ENCODERS_KEY = "max_cd_encoders"
_DEFAULT_MAX_DVD_ENCODERS = 1
_DEFAULT_MAX_CD_ENCODERS = 2


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


@settings_bp.route("/fake-dirty-mode", methods=["GET"])
def get_fake_dirty_mode():
    Session = current_app.config["DB_SESSION"]
    session = Session()

    setting = session.get(Setting, _FAKE_DIRTY_MODE_KEY)
    value = (setting.value == "true") if setting else _DEFAULT_FAKE_DIRTY_MODE
    return jsonify({"fake_dirty_mode": value})


@settings_bp.route("/fake-dirty-mode", methods=["PUT"])
def set_fake_dirty_mode():
    cfg = current_app.config["DISCRIPPER_CFG"]

    # Same hard safety backstop as fake_rip_mode - never settable in prod.
    if cfg["environment"] == "prod":
        return jsonify({"error": "fake_dirty_mode cannot be changed in prod"}), 403

    Session = current_app.config["DB_SESSION"]
    session = Session()

    body = request.get_json(silent=True) or {}
    raw = body.get("fake_dirty_mode")

    if not isinstance(raw, bool):
        return jsonify({"error": "fake_dirty_mode must be a boolean"}), 400

    value_str = "true" if raw else "false"
    setting = session.get(Setting, _FAKE_DIRTY_MODE_KEY)
    if setting:
        setting.value = value_str
    else:
        session.add(Setting(key=_FAKE_DIRTY_MODE_KEY, value=value_str))

    session.commit()
    return jsonify({"fake_dirty_mode": raw})


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


@settings_bp.route("/service-status", methods=["GET"])
def get_service_status():
    # Written only by the ripper service itself (running at startup,
    # stopped on clean shutdown) - read-only from the API's side.
    Session = current_app.config["DB_SESSION"]
    session = Session()

    setting = session.get(Setting, _SERVICE_STATUS_KEY)
    value = setting.value if setting and setting.value else _DEFAULT_SERVICE_STATUS
    return jsonify({"service_status": value})


@settings_bp.route("/service-heartbeat", methods=["GET"])
def get_service_heartbeat():
    # Written only by the ripper service itself, every poll iteration
    # (plus a final stamp on clean shutdown) - read-only from the API's side.
    Session = current_app.config["DB_SESSION"]
    session = Session()

    setting = session.get(Setting, _SERVICE_HEARTBEAT_KEY)
    value = setting.value if setting and setting.value else None
    return jsonify({"service_heartbeat": value})


@settings_bp.route("/dvd-encoding-enabled", methods=["GET"])
def get_dvd_encoding_enabled():
    Session = current_app.config["DB_SESSION"]
    session = Session()
    setting = session.get(Setting, _DVD_ENCODING_ENABLED_KEY)
    return jsonify({"dvd_encoding_enabled": setting.value == "true" if setting else False})


@settings_bp.route("/dvd-encoding-enabled", methods=["PUT"])
def set_dvd_encoding_enabled():
    Session = current_app.config["DB_SESSION"]
    session = Session()
    body = request.get_json(silent=True) or {}
    raw = body.get("dvd_encoding_enabled")
    if not isinstance(raw, bool):
        return jsonify({"error": "dvd_encoding_enabled must be a boolean"}), 400
    value_str = "true" if raw else "false"
    setting = session.get(Setting, _DVD_ENCODING_ENABLED_KEY)
    if setting:
        setting.value = value_str
    else:
        session.add(Setting(key=_DVD_ENCODING_ENABLED_KEY, value=value_str))
    session.commit()
    return jsonify({"dvd_encoding_enabled": raw})


@settings_bp.route("/cd-encoding-enabled", methods=["GET"])
def get_cd_encoding_enabled():
    Session = current_app.config["DB_SESSION"]
    session = Session()
    setting = session.get(Setting, _CD_ENCODING_ENABLED_KEY)
    return jsonify({"cd_encoding_enabled": setting.value == "true" if setting else False})


@settings_bp.route("/cd-encoding-enabled", methods=["PUT"])
def set_cd_encoding_enabled():
    Session = current_app.config["DB_SESSION"]
    session = Session()
    body = request.get_json(silent=True) or {}
    raw = body.get("cd_encoding_enabled")
    if not isinstance(raw, bool):
        return jsonify({"error": "cd_encoding_enabled must be a boolean"}), 400
    value_str = "true" if raw else "false"
    setting = session.get(Setting, _CD_ENCODING_ENABLED_KEY)
    if setting:
        setting.value = value_str
    else:
        session.add(Setting(key=_CD_ENCODING_ENABLED_KEY, value=value_str))
    session.commit()
    return jsonify({"cd_encoding_enabled": raw})


@settings_bp.route("/max-dvd-encoders", methods=["GET"])
def get_max_dvd_encoders():
    Session = current_app.config["DB_SESSION"]
    session = Session()
    setting = session.get(Setting, _MAX_DVD_ENCODERS_KEY)
    value = int(setting.value) if setting else _DEFAULT_MAX_DVD_ENCODERS
    return jsonify({"max_dvd_encoders": value})


@settings_bp.route("/max-dvd-encoders", methods=["PUT"])
def set_max_dvd_encoders():
    Session = current_app.config["DB_SESSION"]
    session = Session()
    body = request.get_json(silent=True) or {}
    raw = body.get("max_dvd_encoders")
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
        return jsonify({"error": "max_dvd_encoders must be a positive integer >= 1"}), 400
    setting = session.get(Setting, _MAX_DVD_ENCODERS_KEY)
    if setting:
        setting.value = str(raw)
    else:
        session.add(Setting(key=_MAX_DVD_ENCODERS_KEY, value=str(raw)))
    session.commit()
    return jsonify({"max_dvd_encoders": raw})


@settings_bp.route("/max-cd-encoders", methods=["GET"])
def get_max_cd_encoders():
    Session = current_app.config["DB_SESSION"]
    session = Session()
    setting = session.get(Setting, _MAX_CD_ENCODERS_KEY)
    value = int(setting.value) if setting else _DEFAULT_MAX_CD_ENCODERS
    return jsonify({"max_cd_encoders": value})


@settings_bp.route("/max-cd-encoders", methods=["PUT"])
def set_max_cd_encoders():
    Session = current_app.config["DB_SESSION"]
    session = Session()
    body = request.get_json(silent=True) or {}
    raw = body.get("max_cd_encoders")
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
        return jsonify({"error": "max_cd_encoders must be a positive integer >= 1"}), 400
    setting = session.get(Setting, _MAX_CD_ENCODERS_KEY)
    if setting:
        setting.value = str(raw)
    else:
        session.add(Setting(key=_MAX_CD_ENCODERS_KEY, value=str(raw)))
    session.commit()
    return jsonify({"max_cd_encoders": raw})


@settings_bp.route("/service-command", methods=["PUT"])
def set_service_command():
    # Written by the API/UI (e.g. the "Stop Service" button); read and
    # cleared by the ripper service itself on its next poll.
    Session = current_app.config["DB_SESSION"]
    session = Session()

    body = request.get_json(silent=True) or {}
    raw = body.get("service_command")

    if raw not in ("exit", ""):
        return jsonify({"error": "service_command must be 'exit' or ''"}), 400

    setting = session.get(Setting, _SERVICE_COMMAND_KEY)
    if setting:
        setting.value = raw
    else:
        session.add(Setting(key=_SERVICE_COMMAND_KEY, value=raw))

    session.commit()
    return jsonify({"service_command": raw})
