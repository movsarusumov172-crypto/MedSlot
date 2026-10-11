"""Patient profile validation and persistence after explicit consent."""

import re
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from medslot import texts
from medslot.models import PatientProfile

PHONE_SEPARATORS = re.compile(r"[\s().-]")


def validate_full_name(value: str) -> str:
    """Normalize a full name and require at least a given and family name."""
    full_name = " ".join(value.split())
    parts = full_name.split(" ")
    if not 2 <= len(full_name) <= 200 or len(parts) < 2:
        raise ValueError(texts.PROFILE_NAME_INVALID)
    if any(not any(character.isalpha() for character in part) for part in parts):
        raise ValueError(texts.PROFILE_NAME_INVALID)
    return full_name


def normalize_phone(value: str) -> str:
    """Validate a phone number and normalize common Russian domestic forms."""
    normalized = PHONE_SEPARATORS.sub("", value)
    if not normalized.startswith("+") and not normalized.isdigit():
        raise ValueError(texts.PHONE_INVALID)
    digits = normalized[1:] if normalized.startswith("+") else normalized
    if not digits.isdigit():
        raise ValueError(texts.PHONE_INVALID)
    if len(digits) == 11 and digits.startswith("8") and not normalized.startswith("+"):
        digits = "7" + digits[1:]
    elif len(digits) == 10 and not normalized.startswith("+"):
        digits = "7" + digits
    if not 10 <= len(digits) <= 15:
        raise ValueError(texts.PHONE_INVALID)
    return f"+{digits}"


async def get_profile(session: AsyncSession, patient_id: int) -> PatientProfile | None:
    """Read one patient's consented contact profile."""
    return await session.get(PatientProfile, patient_id)


async def save_profile(
    session: AsyncSession,
    patient_id: int,
    full_name: str,
    phone: str,
    consented_at: datetime | None = None,
) -> PatientProfile:
    """Save validated details only when the caller has recorded consent."""
    full_name = validate_full_name(full_name)
    phone = normalize_phone(phone)
    profile = await session.get(PatientProfile, patient_id)
    if profile is None:
        profile = PatientProfile(telegram_id=patient_id)
        session.add(profile)
    profile.full_name = full_name
    profile.phone = phone
    profile.consented_at = consented_at or datetime.now(UTC)
    await session.commit()
    await session.refresh(profile)
    return profile


async def update_full_name(
    session: AsyncSession, patient_id: int, full_name: str
) -> PatientProfile:
    """Update the name without changing the existing consent record."""
    profile = await session.get(PatientProfile, patient_id)
    if profile is None:
        raise ValueError(texts.PROFILE_MISSING)
    profile.full_name = validate_full_name(full_name)
    await session.commit()
    return profile


async def update_phone(session: AsyncSession, patient_id: int, phone: str) -> PatientProfile:
    """Update the phone number without changing the existing consent record."""
    profile = await session.get(PatientProfile, patient_id)
    if profile is None:
        raise ValueError(texts.PROFILE_MISSING)
    profile.phone = normalize_phone(phone)
    await session.commit()
    return profile
