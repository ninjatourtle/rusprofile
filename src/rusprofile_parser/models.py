from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(slots=True)
class CompanyCandidate:
    name: str
    url: str
    inn: str | None = None
    ogrn: str | None = None


@dataclass(slots=True)
class CompanyResult:
    name: str
    url: str
    inn: str | None
    ogrn: str | None
    registration_date: str | None
    director_appointment_date: str | None
    revenue: int | None
    profit: int | None
    email: str | None
    website: str | None
    matched: bool
    reasons: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
