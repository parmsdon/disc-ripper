"""
Shared configuration loader for Disc Ripper services.

Usage:
    from common.config import load_config
    cfg = load_config()                # uses DISCRIPPER_ENV env var, defaults to "dev"
    cfg = load_config("prod")          # explicit environment

Looks for config/<env>.yaml relative to the project root (two levels up
from this file: common/config.py -> project_root/config/<env>.yaml).
"""

import os
import yaml
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"


class ConfigError(Exception):
    pass


def load_config(env: str | None = None) -> dict:
    """
    Load the YAML config for the given environment.

    env: "dev" or "prod". If not provided, reads DISCRIPPER_ENV env var,
         defaulting to "dev".
    """
    env = env or os.environ.get("DISCRIPPER_ENV", "dev")

    if env not in ("dev", "prod"):
        raise ConfigError(f"Unknown environment '{env}' (expected 'dev' or 'prod')")

    config_path = CONFIG_DIR / f"{env}.yaml"

    if not config_path.exists():
        example_path = CONFIG_DIR / f"{env}.yaml.example"
        raise ConfigError(
            f"Config file not found: {config_path}\n"
            f"Copy {example_path} to {config_path} and fill in real values."
        )

    with open(config_path, "r") as f:
        cfg = yaml.safe_load(f)

    if cfg.get("environment") != env:
        raise ConfigError(
            f"Config file {config_path} has environment '{cfg.get('environment')}', "
            f"expected '{env}'. Check for a misconfigured/copy-pasted config file."
        )

    return cfg


def get_db_url(cfg: dict) -> str:
    """Build a SQLAlchemy/psycopg2-style Postgres connection URL from config."""
    db = cfg["database"]
    return (
        f"postgresql+psycopg2://{db['user']}:{db['password']}"
        f"@{db['host']}:{db['port']}/{db['name']}"
    )


def get_store_path(cfg: dict, store: str, *parts) -> Path:
    """
    Build a path under the configured datastore.

    store: "dvd_store" or "cd_store"
    *parts: additional path components, e.g. get_store_path(cfg, "dvd_store", "raw", "123")
    """
    storage = cfg["storage"]
    if store not in ("dvd_store", "cd_store"):
        raise ConfigError(f"Unknown store '{store}' (expected 'dvd_store' or 'cd_store')")

    base = Path(storage["datastore_root"]) / storage[store]
    return base.joinpath(*parts) if parts else base
