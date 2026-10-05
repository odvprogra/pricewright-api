import uuid

from pricewright.domain.actors import Actor
from pricewright.domain.audit import ActorType
from pricewright.domain.auth import Principal
from pricewright.domain.users import Role

TENANT = uuid.uuid7()


def test_actor_of_a_person_or_an_integration() -> None:
    person = Principal(TENANT, uuid.uuid7(), Role.SALES_REP)
    integration = Principal(TENANT, uuid.uuid7())

    assert Actor.of(person) == Actor(ActorType.USER, person.subject_id)
    assert Actor.of(integration) == Actor(ActorType.SERVICE_ACCOUNT, integration.subject_id)


def test_actor_person_is_a_user() -> None:
    user_id = uuid.uuid7()

    assert Actor.person(user_id) == Actor(ActorType.USER, user_id)
    assert Actor.person(user_id).is_person
    assert not Actor(ActorType.SERVICE_ACCOUNT, user_id).is_person
