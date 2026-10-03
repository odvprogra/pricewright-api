import uuid

import pytest

from pricewright.api.pagination import InvalidCursorError, decode_cursor, encode_cursor


def test_cursors_round_trip_an_id_without_exposing_it() -> None:
    after = uuid.uuid7()

    cursor = encode_cursor(after)

    assert cursor is not None
    assert str(after) not in cursor
    assert decode_cursor(cursor) == after


def test_no_continuation_means_no_cursor() -> None:
    assert encode_cursor(None) is None
    assert decode_cursor(None) is None


@pytest.mark.parametrize("cursor", ["not-a-cursor!", "YWJj"], ids=["not-base64", "wrong-length"])
def test_decode_cursor_rejects_cursors_this_api_did_not_return(cursor: str) -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor(cursor)
