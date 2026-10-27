"""Shared building blocks for every step contract."""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    """Base for step I/O: unknown fields are rejected so agents cannot smuggle extra data."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# Personal identifiers never travel in contracts; only vault tokens do (SRS privacy NFRs).
VaultToken = Annotated[str, Field(pattern=r"^tok_[A-Za-z0-9_-]{6,}$")]

Confidence = Annotated[float, Field(ge=0.0, le=1.0)]
Rupees = Annotated[int, Field(ge=0)]


class SchemeId(StrEnum):
    PMJJBY = "pmjjby"
    PMSBY = "pmsby"
    RUPAY = "rupay"


class EventType(StrEnum):
    ACCIDENT = "accident"
    ILLNESS = "illness"
    NATURAL = "natural"
    SUICIDE = "suicide"
    UNKNOWN = "unknown"
