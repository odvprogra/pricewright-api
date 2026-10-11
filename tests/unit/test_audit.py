import uuid
from datetime import UTC, datetime

import pytest

from pricewright.domain.audit import (
    SYSTEM_ACTOR_ID,
    ActorType,
    AuditAction,
    AuditEvent,
    AuditValue,
    changed,
    created,
)
from pricewright.domain.auth import Permission, Principal
from pricewright.domain.users import Role

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
TENANT_ID = uuid.uuid7()


def test_audit_event_record_names_the_user_who_acted() -> None:
    admin = Principal(TENANT_ID, uuid.uuid7(), Role.ADMIN)
    user_id = uuid.uuid7()

    event = AuditEvent.record(
        admin, AuditAction.USER_UPDATED, user_id, {"role": ("sales_rep", "admin")}, now=NOW
    )

    assert event.id.version == 7
    assert (event.tenant_id, event.occurred_at) == (TENANT_ID, NOW)
    assert (event.actor_type, event.actor_id) == (ActorType.USER, admin.subject_id)
    assert (event.resource_type, event.resource_id) == ("user", user_id)
    assert event.changes == {"role": ("sales_rep", "admin")}
    assert event.request_id is None


def test_audit_event_record_names_the_service_account_that_acted() -> None:
    account = Principal(TENANT_ID, uuid.uuid7(), scopes=frozenset({Permission.TENANT_READ}))

    event = AuditEvent.record(account, AuditAction.TENANT_UPDATED, TENANT_ID, {}, now=NOW)

    assert (event.actor_type, event.actor_id) == (ActorType.SERVICE_ACCOUNT, account.subject_id)


def test_audit_event_by_system_names_the_system_with_the_nil_uuid() -> None:
    quote_id = uuid.uuid7()

    event = AuditEvent.by_system(
        TENANT_ID, AuditAction.QUOTE_EXPIRED, quote_id, {"status": ("sent", "expired")}, now=NOW
    )

    assert (event.actor_type, event.actor_id) == (ActorType.SYSTEM, SYSTEM_ACTOR_ID)
    assert SYSTEM_ACTOR_ID.int == 0  # the nil UUID
    assert (event.tenant_id, event.occurred_at, event.resource_id) == (TENANT_ID, NOW, quote_id)
    assert event.resource_type == "quote"
    assert event.changes == {"status": ("sent", "expired")}


IRREGULAR_PAST_TENSES = frozenset({"sent"})


@pytest.mark.parametrize("action", list(AuditAction))
def test_audit_action_names_its_resource_before_the_verb(action: AuditAction) -> None:
    resource_type, verb = action.value.split(".")

    assert action.resource_type == resource_type
    # Past tense: it already happened.
    assert verb.endswith("ed") or verb.rpartition("_")[2] in IRREGULAR_PAST_TENSES


def test_created_changes_start_every_set_field_from_nothing() -> None:
    assert created({"name": "ops-copilot", "is_active": True, "expires_at": None}) == {
        "name": (None, "ops-copilot"),
        "is_active": (None, True),
    }


def test_changed_keeps_only_the_fields_that_differ() -> None:
    before: dict[str, AuditValue] = {"full_name": "Blair", "role": "sales_rep", "is_active": True}
    after: dict[str, AuditValue] = {
        "full_name": "Blair",
        "role": "sales_manager",
        "is_active": False,
    }

    assert changed(before, after) == {
        "role": ("sales_rep", "sales_manager"),
        "is_active": (True, False),
    }
