import pytest

from app.geo import _extract_location_candidates


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "A pothole is forming near Don Bosco Institute of Technology",
            "Don Bosco Institute of Technology",
        ),
        ("पवई येथे मोठा खड्डा आहे", "पवई"),
        ("घाटकोपर में कचरा जमा है", "घाटकोपर"),
    ],
)
def test_extract_location_candidates_preserves_multilingual_places(text, expected):
    candidates = _extract_location_candidates(text)

    assert candidates
    assert expected in candidates
