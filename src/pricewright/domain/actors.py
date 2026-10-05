"""Who did something to a record: a person or an integration."""

import uuid
from dataclasses import dataclass

from pricewright.domain.audit import ActorType
from pricewright.domain.auth import Principal


@dataclass(frozen=True, slots=True)
class Actor:
    type: ActorType
    id: uuid.UUID
    """The user's or the service account's id."""

    @classmethod
    def of(cls, principal: Principal) -> Actor:
        kind = ActorType.SERVICE_ACCOUNT if principal.is_service_account else ActorType.USER
        return cls(kind, principal.subject_id)

    @classmethod
    def person(cls, user_id: uuid.UUID) -> Actor:
        return cls(ActorType.USER, user_id)

    @property
    def is_person(self) -> bool:
        return self.type is ActorType.USER
