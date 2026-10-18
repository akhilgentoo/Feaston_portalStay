"""Core booking rules for the FeastOn homestay app."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil


@dataclass(slots=True)
class BookingRequest:
    days: int
    people: int
    adults: int
    children: int = 0
    infants: int = 0
    group_type: str = "family"


@dataclass(slots=True)
class BookingDecision:
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    total_guests: int = 0
    recommended_rooms: int = 0
    nightly_rate: int = 0
    estimated_total: int = 0
    long_stay_discount: int = 0


GROUP_RATE_MULTIPLIER = {
    "family": 1.0,
    "friends": 1.06,
    "pilgrims": 0.95,
    "culture_lovers": 1.02,
}


def evaluate_booking(request: BookingRequest) -> BookingDecision:
    errors: list[str] = []

    if request.days < 1:
        errors.append("Stay duration must be at least 1 day.")
    if request.days > 60:
        errors.append("For stays beyond 60 days, please contact us directly.")

    if request.people not in (1, 2):
        errors.append("Total people must be 1 or 2.")
    if request.adults < 1:
        errors.append("At least one adult guest is required.")
    if request.children < 0 or request.infants < 0:
        errors.append("Children and infant counts cannot be negative.")

    total_guests = request.adults + request.children + request.infants
    if total_guests != request.people:
        errors.append("Total people must match adults + children + infants.")
    if total_guests > 2:
        errors.append("This homestay currently accepts only 1 or 2 guests per booking.")

    if errors:
        return BookingDecision(is_valid=False, errors=errors, total_guests=total_guests)

    paying_guests = request.adults + request.children
    recommended_rooms = max(1, ceil(max(paying_guests, 1) / 3))

    base_rate = 3500
    multiplier = GROUP_RATE_MULTIPLIER.get(request.group_type, 1.0)
    nightly_rate = int(base_rate * multiplier)
    subtotal = nightly_rate * recommended_rooms * request.days

    long_stay_discount = 10 if request.days >= 21 else 5 if request.days >= 14 else 0
    estimated_total = int(subtotal * (1 - long_stay_discount / 100))

    return BookingDecision(
        is_valid=True,
        total_guests=total_guests,
        recommended_rooms=recommended_rooms,
        nightly_rate=nightly_rate,
        estimated_total=estimated_total,
        long_stay_discount=long_stay_discount,
    )
