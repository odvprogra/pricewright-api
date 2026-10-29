"""Today's names for the ids a response carries (ADR-0029).

Records point at people, service accounts and customers by id. A use case whose response shows them
looks the names up once for everything it returns, after the read, so a list costs the same few
queries whatever its length. Users and service accounts are deactivated and customers archived,
never deleted, so every id a record holds still has a name.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from uuid import UUID

from pricewright.application.ports import UnitOfWork
from pricewright.domain.actors import Actor
from pricewright.domain.audit import ActorType
from pricewright.domain.customers import Customer


@dataclass(frozen=True, slots=True)
class Names:
    actors: Mapping[Actor, str]
    """A user's full name or a service account's name."""
    customers: Mapping[UUID, Customer]

    def of(self, actor: Actor) -> str:
        return self.actors[actor]

    def customer(self, customer_id: UUID) -> Customer:
        return self.customers[customer_id]


async def look_up_names(
    uow: UnitOfWork, actors: Collection[Actor], customer_ids: Collection[UUID]
) -> Names:
    """The names in ``uow``'s tenant: one query per kind of record asked for."""
    by_type = {
        kind: {actor.id for actor in actors if actor.type is kind}
        for kind in (ActorType.USER, ActorType.SERVICE_ACCOUNT)
    }
    users = await uow.users.with_ids(by_type[ActorType.USER])
    accounts = await uow.service_accounts.with_ids(by_type[ActorType.SERVICE_ACCOUNT])
    named = {Actor.person(user.id): user.full_name for user in users}
    named |= {Actor(ActorType.SERVICE_ACCOUNT, account.id): account.name for account in accounts}
    customers = await uow.customers.with_ids(set(customer_ids))
    return Names(named, {customer.id: customer for customer in customers})
