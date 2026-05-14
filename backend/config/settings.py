"""Application settings (pydantic-settings)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from loguru import logger
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the backend.

    All fields are overridable through environment variables with the
    ``STT_`` prefix (e.g. ``STT_DATABASE_URL``) or a local ``.env`` file.
    """

    database_url: str = "sqlite:///./data/stanford_town.db"
    secret_key_path: str = "~/.stanford-town-vue/secret.key"
    logs_dir: str = "~/.stanford-town-vue/logs"
    # Resolved relative to the backend/ root (where pyproject.toml lives), not CWD.
    assets_dir: str = "assets"
    # Where original Stanford Town on-disk fork demos live, in ``storage/`` and
    # ``compressed_storage/`` subdirs. Drop demo sims here to launch from them.
    forks_dir: str = "~/.stanford-town-vue/forks"
    frontend_dev_origin: str = "http://localhost:5173"

    model_config = SettingsConfigDict(
        env_prefix="STT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def expanded_secret_key_path(self) -> Path:
        """Return the secret key path with ``~`` expanded to the home directory."""
        return Path(self.secret_key_path).expanduser().resolve()

    def expanded_logs_dir(self) -> Path:
        return Path(self.logs_dir).expanduser().resolve()

    def expanded_assets_dir(self) -> Path:
        """Assets dir, resolved against the backend/ package root if relative.

        ``settings.assets_dir`` is intentionally relative ("assets") so the
        bundle works whether uvicorn is run from the backend/ directory or
        from the project root.
        """
        p = Path(self.assets_dir).expanduser()
        if p.is_absolute():
            return p.resolve()
        backend_root = Path(__file__).resolve().parent.parent  # config/ -> backend/
        return (backend_root / p).resolve()

    def expanded_forks_dir(self) -> Path:
        """On-disk fork demos dir, with ``~`` expanded; resolved if relative.

        Relative values anchor at the ``backend/`` package root, matching
        :meth:`expanded_assets_dir`.
        """
        p = Path(self.forks_dir).expanduser()
        if p.is_absolute():
            return p.resolve()
        backend_root = Path(__file__).resolve().parent.parent  # config/ -> backend/
        return (backend_root / p).resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor."""
    return Settings()


def bootstrap_secret_key(path: Path | str | None = None) -> bytes:
    """Generate a Fernet key at ``path`` if missing, and return its raw bytes.

    When ``path`` is omitted, the location from :func:`get_settings` is used.
    Warns the operator on first creation so they can back the file up.
    """
    from cryptography.fernet import Fernet

    if path is None:
        key_path = get_settings().expanded_secret_key_path()
    else:
        key_path = Path(path).expanduser()

    key_path.parent.mkdir(parents=True, exist_ok=True)

    if key_path.exists():
        return key_path.read_bytes()

    key = Fernet.generate_key()
    key_path.write_bytes(key)
    try:
        key_path.chmod(0o600)
    except OSError:
        # chmod is a no-op on Windows; ignore.
        pass
    logger.warning(
        "Generated new Fernet secret key at {} — BACK THIS UP. Losing it "
        "will make existing encrypted LLM profile keys unreadable.",
        key_path,
    )
    return key


def redact_db_url(url: str) -> str:
    """Return ``url`` with any ``user:password@`` credentials masked.

    Leaves all other components intact so the host/port/path remain visible
    for debugging.
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.netloc or "@" not in parts.netloc:
        return url
    userinfo, _, host = parts.netloc.rpartition("@")
    if ":" in userinfo:
        user, _, _ = userinfo.partition(":")
        new_userinfo = f"{user}:***"
    else:
        new_userinfo = userinfo or "***"
    new_netloc = f"{new_userinfo}@{host}" if new_userinfo else host
    return urlunsplit((parts.scheme, new_netloc, parts.path, parts.query, parts.fragment))
