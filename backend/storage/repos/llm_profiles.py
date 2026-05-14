"""LlmProfileRepo — encrypted CRUD over saved LLM provider credentials."""

from __future__ import annotations

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from storage.models import LlmProfile


class LlmProfileRepo:
    """LLM provider credential storage with Fernet at-rest encryption."""

    def __init__(self, session: Session, fernet_key: bytes) -> None:
        self.session = session
        self._key = fernet_key

    # ------------------------------------------------------------------ utils
    def _cipher(self) -> Fernet:
        if not self._key:
            raise RuntimeError(
                "LlmProfileRepo requires a non-empty Fernet key for "
                "encryption / decryption operations."
            )
        return Fernet(self._key)

    def _encrypt(self, plaintext: str) -> bytes:
        return self._cipher().encrypt(plaintext.encode("utf-8"))

    def _decrypt(self, blob: bytes) -> str:
        return self._cipher().decrypt(blob).decode("utf-8")

    # ----------------------------------------------------------------- writes
    def create(
        self,
        name: str,
        provider: str,
        model: str,
        api_key: str,
        **fields,
    ) -> LlmProfile:
        enc = self._encrypt(api_key)
        profile = LlmProfile(
            name=name,
            provider=provider,
            model=model,
            api_key=enc,
            **fields,
        )
        self.session.add(profile)
        self.session.commit()
        self.session.refresh(profile)
        return profile

    def update(self, profile_id: int, **fields) -> LlmProfile:
        profile = self.session.get(LlmProfile, profile_id)
        if profile is None:
            raise ValueError(f"LlmProfile id={profile_id} not found")
        if "api_key" in fields and fields["api_key"] is not None:
            fields["api_key"] = self._encrypt(fields["api_key"])
        for k, v in fields.items():
            setattr(profile, k, v)
        self.session.commit()
        self.session.refresh(profile)
        return profile

    def delete(self, profile_id: int) -> None:
        profile = self.session.get(LlmProfile, profile_id)
        if profile is None:
            return
        self.session.delete(profile)
        self.session.commit()

    # ------------------------------------------------------------------ reads
    def get(self, profile_id: int) -> LlmProfile | None:
        return self.session.get(LlmProfile, profile_id)

    def get_decrypted_key(self, profile_id: int) -> str:
        profile = self.session.get(LlmProfile, profile_id)
        if profile is None:
            raise ValueError(f"LlmProfile id={profile_id} not found")
        return self._decrypt(profile.api_key)

    def list(self) -> list[LlmProfile]:
        return list(
            self.session.scalars(select(LlmProfile).order_by(LlmProfile.id)).all()
        )


__all__ = ["LlmProfileRepo"]
