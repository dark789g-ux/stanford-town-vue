"""Tests for the Fernet secret-key bootstrap."""

from __future__ import annotations

import sys
from pathlib import Path

from cryptography.fernet import Fernet

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from config.settings import bootstrap_secret_key  # noqa: E402


def test_bootstrap_creates_key_when_missing(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "secret.key"
    assert not target.exists()

    key = bootstrap_secret_key(target)

    assert target.exists()
    assert isinstance(key, bytes)
    assert len(key) == 44
    assert key.endswith(b"=")
    # Returned bytes match the file on disk
    assert target.read_bytes() == key
    # The key is a valid Fernet key
    Fernet(key)  # raises on invalid


def test_bootstrap_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "secret.key"
    first = bootstrap_secret_key(target)
    second = bootstrap_secret_key(target)
    assert first == second
    # Sanity: a brand-new generation would almost certainly differ.
    third_target = tmp_path / "other.key"
    third = bootstrap_secret_key(third_target)
    assert third != first


def test_bootstrap_returns_valid_fernet_key(tmp_path: Path) -> None:
    key = bootstrap_secret_key(tmp_path / "secret.key")
    cipher = Fernet(key)
    token = cipher.encrypt(b"hello")
    assert cipher.decrypt(token) == b"hello"
