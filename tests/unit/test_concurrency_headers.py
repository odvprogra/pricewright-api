import pytest
from starlette.exceptions import HTTPException

from pricewright.api.concurrency import etag, expected_version
from pricewright.domain.errors import StaleVersionError


def test_etag_is_the_quoted_version() -> None:
    assert etag(7) == '"7"'


def test_expected_version_reads_a_strong_etag() -> None:
    assert expected_version(' "7" ') == 7


@pytest.mark.parametrize("if_match", [None, "*", " * "])
def test_expected_version_requires_a_real_etag(if_match: str | None) -> None:
    with pytest.raises(HTTPException) as error:
        expected_version(if_match)

    assert error.value.status_code == 428


@pytest.mark.parametrize("if_match", ['W/"7"', "7", '"7", "8"', '"seven"'])
def test_expected_version_treats_weak_or_unknown_tags_as_stale(if_match: str) -> None:
    with pytest.raises(StaleVersionError):
        expected_version(if_match)
