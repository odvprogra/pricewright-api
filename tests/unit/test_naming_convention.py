"""Constraint and index names are deterministic, list every column and name the table once."""

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Table,
    UniqueConstraint,
)

from pricewright.infrastructure.database import NAMING_CONVENTION


def test_naming_convention_names_every_kind_of_constraint() -> None:
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table(
        "parents",
        metadata,
        Column("tenant_id", Integer),
        Column("id", Integer, primary_key=True),
        UniqueConstraint("tenant_id", "id"),
    )
    Table(
        "children",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("tenant_id", Integer),
        Column("parent_id", Integer),
        ForeignKeyConstraint(["tenant_id", "parent_id"], ["parents.tenant_id", "parents.id"]),
        Index(None, "tenant_id", "parent_id"),
        CheckConstraint("id > 0", name="positive_id"),
    )

    tables = metadata.tables.values()
    names = {str(constraint.name) for table in tables for constraint in table.constraints} | {
        str(index.name) for table in tables for index in table.indexes
    }

    assert names == {
        "pk_parents",
        "uq_parents_tenant_id_id",
        "pk_children",
        "fk_children_tenant_id_parent_id_parents",
        "ix_children_tenant_id_parent_id",
        "ck_children_positive_id",
    }
