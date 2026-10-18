from feaston.booking import BookingRequest, evaluate_booking


def test_rejects_zero_day_stays():
    decision = evaluate_booking(BookingRequest(days=0, people=1, adults=1, children=0, infants=0))
    assert decision.is_valid is False
    assert "Stay duration must be at least 1 day." in decision.errors


def test_accepts_single_day_booking():
    decision = evaluate_booking(BookingRequest(days=1, people=1, adults=1, children=0, infants=0))
    assert decision.is_valid is True
    assert decision.total_guests == 1
    assert decision.recommended_rooms == 1
    assert decision.long_stay_discount == 0
    assert decision.estimated_total > 0


def test_rejects_when_people_count_and_breakdown_do_not_match():
    decision = evaluate_booking(BookingRequest(days=15, people=2, adults=1, children=0, infants=0))
    assert decision.is_valid is False
    assert "Total people must match adults + children + infants." in decision.errors


def test_rejects_people_over_two():
    decision = evaluate_booking(BookingRequest(days=2, people=3, adults=2, children=1, infants=0))
    assert decision.is_valid is False
    assert "Total people must be 1 or 2." in decision.errors
