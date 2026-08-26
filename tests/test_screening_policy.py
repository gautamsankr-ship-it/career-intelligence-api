import pytest

from app.config import screening_decision


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (69.9, "SKIP"),
        (70, "REVIEW"),
        (77.9, "REVIEW"),
        (78, "AUTO_APPLY"),
        (90, "AUTO_APPLY"),
    ],
)
def test_screening_boundaries(score, expected):
    assert screening_decision(score) == expected
