import base64
import json
import uuid

import pytest

from pricewright.api.pagination import InvalidCursorError, decode_cursor, encode_cursor
from pricewright.application.pagination import Keyset, page_of

QUERY = {"tier": "gold", "sort": "-name", "q": None}


@pytest.mark.parametrize("value", [None, "Northfield ácme"], ids=["by-id", "by-value"])
def test_cursors_round_trip_a_position_without_exposing_it(value: str | None) -> None:
    after = Keyset(uuid.uuid7(), value)

    cursor = encode_cursor(after, QUERY)

    assert cursor is not None
    assert str(after.id) not in cursor
    assert decode_cursor(cursor, QUERY) == after


def test_no_continuation_means_no_cursor() -> None:
    assert encode_cursor(None) is None
    assert decode_cursor(None) is None


def test_a_query_without_values_is_the_same_query() -> None:
    cursor = encode_cursor(Keyset(uuid.uuid7()), {"tier": None})

    assert decode_cursor(cursor, {}) == decode_cursor(cursor)


@pytest.mark.parametrize(
    "query",
    [{"tier": "silver", "sort": "-name"}, {"tier": "gold", "sort": "name"}, {}],
    ids=["other-filter", "other-sort", "no-filters"],
)
def test_a_cursor_only_continues_the_query_that_returned_it(query: dict[str, object]) -> None:
    cursor = encode_cursor(Keyset(uuid.uuid7(), "Acme"), QUERY)

    with pytest.raises(InvalidCursorError, match="another query"):
        decode_cursor(cursor, query)


def _encoded(payload: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


@pytest.mark.parametrize(
    "cursor",
    [
        "not-a-cursor!",
        "YWJj",
        _encoded(["a", "list"]),
        _encoded({"query": "x"}),
        _encoded({"id": 12, "query": "x"}),
        _encoded({"id": str(uuid.uuid7()), "value": 3, "query": "x"}),
    ],
    ids=["not-base64", "not-json", "not-an-object", "no-id", "id-not-text", "value-not-text"],
)
def test_decode_cursor_rejects_cursors_this_api_did_not_return(cursor: str) -> None:
    with pytest.raises(InvalidCursorError, match="not one this API returned"):
        decode_cursor(cursor)


def test_page_of_keeps_the_extra_row_out_and_points_after_the_last_item() -> None:
    rows = [uuid.uuid7() for _ in range(3)]

    more = page_of(rows, 2, Keyset)
    last = page_of(rows, 3, Keyset)

    assert (more.items, more.next_after) == (rows[:2], Keyset(rows[1]))
    assert (last.items, last.next_after) == (rows, None)
