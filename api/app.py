"""
Disc Ripper Flask API.

Run with:
    DISCRIPPER_ENV=dev python3 api/app.py

Serves a JSON API consumed by the React frontend. CORS is enabled for
dev (frontend dev server runs on a different port).
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, jsonify
from flask_cors import CORS
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session

from common.config import load_config, get_db_url
from api.routes.discs import discs_bp
from api.routes.drives import drives_bp
from api.routes.health import health_bp
from api.routes.encode_profiles import encode_profiles_bp
from api.routes.settings import settings_bp
from api.routes.catalog import catalog_bp


def create_app(env: str | None = None) -> Flask:
    app = Flask(__name__)

    cfg = load_config(env)
    app.config["DISCRIPPER_CFG"] = cfg

    CORS(app)

    engine = create_engine(get_db_url(cfg))
    session_factory = sessionmaker(bind=engine)
    Session = scoped_session(session_factory)

    app.config["DB_ENGINE"] = engine
    app.config["DB_SESSION"] = Session

    @app.teardown_appcontext
    def remove_session(exception=None):
        Session.remove()

    app.register_blueprint(health_bp, url_prefix="/api/health")
    app.register_blueprint(discs_bp, url_prefix="/api/discs")
    app.register_blueprint(drives_bp, url_prefix="/api/drives")
    app.register_blueprint(encode_profiles_bp, url_prefix="/api/encode-profiles")
    app.register_blueprint(settings_bp, url_prefix="/api/settings")
    app.register_blueprint(catalog_bp, url_prefix="/api/catalog")

    @app.route("/api/ping")
    def ping():
        return jsonify({"status": "ok", "environment": cfg["environment"]})

    return app


if __name__ == "__main__":
    app = create_app()
    cfg = app.config["DISCRIPPER_CFG"]
    app.run(host=cfg["api"]["host"], port=cfg["api"]["port"], debug=cfg["api"]["debug"])
