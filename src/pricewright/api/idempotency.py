"""The ``Idempotency-Key`` header (ADR-0022): read, checked and paired with a request fingerprint.

The fingerprint is a SHA-256 of the method, the path and the validated body as canonical JSON, so
the same request sent with other whitespace or field order is still the same request. Headers are
left out: a retry may carry a fresher ``If-Match``.
"""

import hashlib
import json
from collections.abc import Mapping
from typing import Annotated

from fastapi import Header, Request, Response
from pydantic import BaseModel

from pricewright.domain.idempotency import MAX_KEY_LENGTH, IdempotentRequest

REPLAYED_HEADER = "Idempotent-Replayed"
_QUOTE = '"'

IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        description=(
            "Makes the request safe to retry (ADR-0022): a retry with the same key and the same "
            "request gets back what the first one created, with `Idempotent-Replayed: true`, "
            f"instead of creating it again. 1 to {MAX_KEY_LENGTH} visible ASCII characters "
            'without `"` or `\\`, quoted as a Structured Field String or bare; a UUID is a good '
            "key. Keys are "
            "the caller's own and kept for 24 hours. The same key with a different request is a "
            "422 (`idempotency_key_reused`); while the first request is still running, a 409 "
            "(`idempotency_key_in_use`)."
        ),
        examples=['"8e03978e-40d5-43e8-bc93-6894a57f9324"'],
    ),
]


def _unquoted(value: str) -> str:
    """The draft sends a Structured Field String (``"..."``); many clients send the bare key."""
    value = value.strip()
    if len(value) >= len(_QUOTE) * 2 and value.startswith(_QUOTE) and value.endswith(_QUOTE):
        return value[1:-1]
    return value


_IN_USE = "a request with this Idempotency-Key is still running (`idempotency_key_in_use`)"
_KEY_REFUSED = "an Idempotency-Key that is invalid or was used for a different request"


def idempotency_errors(
    responses: Mapping[int | str, dict[str, object]],
) -> dict[int | str, dict[str, object]]:
    """A creation's documented errors, plus those of its ``Idempotency-Key``."""
    merged = {status: dict(response) for status, response in responses.items()}
    for status, cause, alone in (
        (409, _IN_USE, "A request with this Idempotency-Key is still running"),
        (422, _KEY_REFUSED, f"Invalid fields, or {_KEY_REFUSED}"),
    ):
        described = merged.get(status, {}).get("description")
        merged[status] = {"description": alone if described is None else f"{described}; or {cause}"}
    return merged


def idempotent_request(
    key: str | None, request: Request, body: BaseModel | None
) -> IdempotentRequest | None:
    """The key with a fingerprint of ``request``, or None when the client sent no key.

    Secrets in the body (``SecretStr``) are masked by ``model_dump``, so a fingerprint never
    hashes a password.
    """
    if key is None:
        return None
    dumped = None if body is None else body.model_dump(mode="json")
    canonical = json.dumps(
        {"method": request.method, "path": request.url.path, "body": dumped},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return IdempotentRequest(_unquoted(key), hashlib.sha256(canonical.encode()).hexdigest())


def mark_replayed(response: Response, replayed: bool) -> None:
    if replayed:
        response.headers[REPLAYED_HEADER] = "true"
