from __future__ import annotations

import pytest

from apps.core.errors import ValidationError
from apps.core.segment_rules import bounds_changed, validate_segment_bounds


def test_valid_bounds():
    assert validate_segment_bounds(1, 3, source_duration_seconds=10) == 2


def test_end_must_exceed_start():
    with pytest.raises(ValidationError) as exc:
        validate_segment_bounds(3, 3)
    assert exc.value.details["fields"][0]["code"] == "END_LE_START"


def test_no_silent_clamp():
    with pytest.raises(ValidationError):
        validate_segment_bounds(1, 20, source_duration_seconds=10)


def test_bounds_changed():
    assert bounds_changed(old_start=1, old_end=2, new_start=1, new_end=3)
    assert not bounds_changed(old_start=1, old_end=2, new_start=1, new_end=2)
