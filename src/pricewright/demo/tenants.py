"""Who the demo tenants are: settings, people, products, customers, pricing rules and the kinds
of quotes in their history (ADR-0024).

Both are fictional. Northfield Supply distributes industrial and office supplies and sells the
demand dataset's products (ADR-0025); Larkspur Tool Co. is a small tool supplier that exists to
show tenant isolation. People's emails use ``.example``, a domain reserved for examples (RFC 2606).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from pricewright.application.customers import NewCustomer
from pricewright.demo.catalog import northfield_catalog
from pricewright.demo.customers import demo_customers
from pricewright.demo.randomness import generator
from pricewright.domain.catalog import UnitOfMeasure
from pricewright.domain.customers import CustomerTier
from pricewright.domain.pricing_rules import Bracket, RuleKind
from pricewright.domain.users import Role


@dataclass(frozen=True, slots=True)
class Person:
    full_name: str
    email: str
    role: Role


@dataclass(frozen=True, slots=True)
class ProductSpec:
    sku: str
    name: str
    category: str
    unit: UnitOfMeasure
    list_price: Decimal
    unit_cost: Decimal


@dataclass(frozen=True, slots=True)
class RuleSpec:
    """A pricing rule with its scope named by SKU or category and its window in days from the
    demo's as-of date; a rule without ``starts`` applies from the day the tenant went live."""

    kind: RuleKind
    name: str
    rate: Decimal | None = None
    brackets: tuple[Bracket, ...] = ()
    sku: str | None = None
    category: str | None = None
    customer_tier: CustomerTier | None = None
    starts: int | None = None
    ends: int | None = None


class StoryKind(StrEnum):
    """How a quote's story goes, from its draft to where it stands on the as-of date."""

    WON = "won"
    WON_ORDER_CANCELLED = "won, order cancelled"
    WON_AFTER_REJECTION = "won after a rejection and a revision"
    WON_AFTER_CHANGE = "won after the customer asked for a change"
    WON_AFTER_EXPIRY = "won after the offer expired and was revised"
    SENT_AFTER_EXPIRY = "sent again after the offer expired"
    WON_AFTER_RECALL = "won after a recall"
    WON_WITH_OVERRIDE = "won with a manager's price override"
    MANAGER_OFFER = "a manager's offer, sent"
    LOST = "lost: sent, then cancelled"
    CANCELLED_DRAFT = "draft cancelled"
    CANCELLED_APPROVED = "approved, then cancelled"
    REJECTED = "rejected"
    EXPIRED_SENT = "sent and expired"
    EXPIRED_APPROVED = "approved and expired"
    EXPIRED_PENDING = "pending approval and expired"
    PENDING = "pending approval"
    APPROVED = "approved"
    SENT = "sent"
    ACCEPTED = "accepted"
    DRAFT = "draft"


@dataclass(frozen=True, slots=True)
class StoryPlan:
    """How many quotes of each kind the history holds, and the line quantities, by category, that
    need approval with a gold customer (``heavy``) or no longer do (``eased``)."""

    counts: Mapping[StoryKind, int]
    heavy: Mapping[str, tuple[int, int]]
    eased: Mapping[str, tuple[int, int]]


@dataclass(frozen=True, slots=True)
class DemoTenant:
    name: str
    currency: str
    tax_rate: Decimal
    approval_threshold: Decimal
    quote_prefix: str
    order_prefix: str
    quote_validity_days: int
    admin: Person
    people: tuple[Person, ...]
    """Everyone but the admin, who creates their accounts."""
    products: tuple[ProductSpec, ...]
    customers: tuple[NewCustomer, ...]
    rules: tuple[RuleSpec, ...]
    stories: StoryPlan

    def with_role(self, role: Role) -> tuple[Person, ...]:
        return tuple(person for person in (self.admin, *self.people) if person.role is role)

    @property
    def account_owners(self) -> Mapping[str, str]:
        """Each account's rep, by email: the reps take the accounts in turn."""
        reps = self.with_role(Role.SALES_REP)
        return {
            customer.account_number: reps[index % len(reps)].email
            for index, customer in enumerate(self.customers)
        }

    @property
    def manager(self) -> Person | None:
        managers = self.with_role(Role.SALES_MANAGER)
        return managers[0] if managers else None

    @property
    def approver(self) -> Person:
        """Who keeps the rules and decides the reps' approvals: a manager, else the admin."""
        return self.manager or self.admin


def _brackets(*steps: tuple[int, str]) -> tuple[Bracket, ...]:
    return tuple(Bracket(Decimal(quantity), Decimal(rate)) for quantity, rate in steps)


FASTENERS, SAFETY, PAPER = "Fasteners & Fixings", "Safety & PPE", "Office Paper & Mailing"
JANITORIAL = "Janitorial & Sanitation"
_FLOOR, _VOLUME = RuleKind.MARGIN_FLOOR, RuleKind.VOLUME_TIER
_TIER, _PROMOTION = RuleKind.CUSTOMER_TIER, RuleKind.PROMOTION
_GOLD, _SILVER = CustomerTier.GOLD, CustomerTier.SILVER

NORTHFIELD_RULES = (
    RuleSpec(_FLOOR, "Company margin floor", Decimal("0.15")),
    # Paper is a thin-margin commodity: a lower floor for the category, the most specific wins.
    RuleSpec(_FLOOR, "Paper and mailing margin floor", Decimal("0.08"), category=PAPER),
    RuleSpec(
        _VOLUME,
        "Fastener volume pricing",
        brackets=_brackets((10, "0.04"), (25, "0.08"), (50, "0.12")),
        category=FASTENERS,
    ),
    RuleSpec(
        _VOLUME,
        "Safety volume pricing",
        brackets=_brackets((12, "0.05"), (48, "0.10")),
        category=SAFETY,
    ),
    RuleSpec(
        _VOLUME,
        "Paper volume pricing",
        brackets=_brackets((10, "0.03"), (40, "0.06")),
        category=PAPER,
    ),
    RuleSpec(
        _VOLUME,
        "Copy paper pallet pricing",
        brackets=_brackets((20, "0.06"), (40, "0.10")),
        sku="OFP-0146",  # Copy Paper, Letter 8.5 x 11 in, 20 lb, 92 Bright
    ),
    RuleSpec(_TIER, "Silver account discount", Decimal("0.03"), customer_tier=_SILVER),
    RuleSpec(_TIER, "Gold account discount", Decimal("0.06"), customer_tier=_GOLD),
    RuleSpec(_TIER, "Gold safety program", Decimal("0.09"), category=SAFETY, customer_tier=_GOLD),
    RuleSpec(
        _PROMOTION,
        "Safety stock-up promotion",
        Decimal("0.10"),
        category=SAFETY,
        starts=-150,
        ends=-136,
    ),
    RuleSpec(
        _PROMOTION, "Paper savings event", Decimal("0.06"), category=PAPER, starts=-80, ends=-50
    ),
    RuleSpec(
        _PROMOTION,
        "Janitorial restock promotion",
        Decimal("0.08"),
        category=JANITORIAL,
        starts=-21,
        ends=24,
    ),
    RuleSpec(
        _PROMOTION, "Fastener clearance", Decimal("0.07"), category=FASTENERS, starts=30, ends=61
    ),
)


_K = StoryKind
NORTHFIELD_STORIES = StoryPlan(
    counts={
        _K.WON: 49,
        _K.WON_ORDER_CANCELLED: 5,
        _K.WON_AFTER_REJECTION: 4,
        _K.WON_AFTER_CHANGE: 4,
        _K.WON_AFTER_EXPIRY: 2,
        _K.SENT_AFTER_EXPIRY: 1,
        _K.WON_AFTER_RECALL: 2,
        _K.WON_WITH_OVERRIDE: 3,
        _K.MANAGER_OFFER: 2,
        _K.LOST: 10,
        _K.CANCELLED_DRAFT: 4,
        _K.CANCELLED_APPROVED: 2,
        _K.REJECTED: 5,
        _K.EXPIRED_SENT: 8,
        _K.EXPIRED_APPROVED: 2,
        _K.EXPIRED_PENDING: 2,
        _K.PENDING: 8,
        _K.APPROVED: 6,
        _K.SENT: 14,
        _K.ACCEPTED: 5,
        _K.DRAFT: 12,
    },
    # A gold customer's fasteners from 50 boxes (12% + 6%) and safety items from 48 (10% + 9%)
    # give away more than the 15% threshold; at 12 to 40 they stay under it.
    heavy={FASTENERS: (50, 120), SAFETY: (48, 96)},
    eased={FASTENERS: (12, 40), SAFETY: (12, 40)},
)


def _northfield(seed: int) -> DemoTenant:
    return DemoTenant(
        name="Northfield Supply",
        currency="USD",
        tax_rate=Decimal("0.0725"),
        approval_threshold=Decimal("0.15"),
        quote_prefix="NF",
        order_prefix="NFO",
        quote_validity_days=30,
        admin=Person("Avery Collins", "avery@northfield.example", Role.ADMIN),
        people=(
            Person("Morgan Reyes", "morgan@northfield.example", Role.SALES_MANAGER),
            Person("Jordan Patel", "jordan@northfield.example", Role.SALES_REP),
            Person("Taylor Brooks", "taylor@northfield.example", Role.SALES_REP),
            Person("Casey Nguyen", "casey@northfield.example", Role.SALES_REP),
        ),
        products=tuple(
            ProductSpec(
                item.sku, item.name, item.category, item.unit, item.list_price, item.unit_cost
            )
            for item in northfield_catalog()
        ),
        customers=demo_customers(
            generator(seed, "northfield/customers"),
            account_prefix="C-",
            tiers={CustomerTier.GOLD: 10, CustomerTier.SILVER: 20, CustomerTier.STANDARD: 50},
        ),
        rules=NORTHFIELD_RULES,
        stories=NORTHFIELD_STORIES,
    )


_TOOLS, _HAND, _LAYOUT, _STORAGE = (
    "Power Tool Accessories",
    "Hand Tools",
    "Measuring & Layout",
    "Workshop Storage",
)
_EA, _SET, _PACK = UnitOfMeasure.EACH, UnitOfMeasure.SET, UnitOfMeasure.PACKAGE


def _product(sku: str, name: str, category: str, unit: UnitOfMeasure, prices: str) -> ProductSpec:
    list_price, unit_cost = prices.split("/")
    return ProductSpec(sku, name, category, unit, Decimal(list_price), Decimal(unit_cost))


LARKSPUR_PRODUCTS = (
    _product(
        "LK-1001", "Carbide Circular Saw Blade, 7-1/4 in, 24 Teeth", _TOOLS, _EA, "18.90/10.40"
    ),
    _product(
        "LK-1002", "Bi-Metal Reciprocating Saw Blades, 6 in, Pack of 5", _TOOLS, _PACK, "14.50/7.90"
    ),
    _product("LK-1003", "Titanium-Coated Drill Bit Set, 21-Piece", _TOOLS, _SET, "39.00/21.50"),
    _product("LK-1004", "Masonry Drill Bit Set, 5-Piece", _TOOLS, _SET, "16.75/9.10"),
    _product("LK-1005", "Impact Driver Bit Set, 40-Piece", _TOOLS, _SET, "24.90/13.20"),
    _product("LK-1006", "Bi-Metal Hole Saw Kit, 9-Piece", _TOOLS, _SET, "54.00/31.00"),
    _product("LK-1007", "Flap Disc, 4-1/2 in, 60 Grit, Pack of 10", _TOOLS, _PACK, "29.50/15.80"),
    _product("LK-1008", "Cut-Off Wheel, 4-1/2 in, Pack of 25", _TOOLS, _PACK, "22.00/11.40"),
    _product("LK-2001", "Claw Hammer, 16 oz, Fiberglass Handle", _HAND, _EA, "19.50/10.30"),
    _product("LK-2002", "Adjustable Wrench, 10 in", _HAND, _EA, "16.25/8.70"),
    _product("LK-2003", "Curved-Jaw Locking Pliers, 10 in", _HAND, _EA, "14.90/7.60"),
    _product("LK-2004", "Hex Key Set, SAE and Metric, 26-Piece", _HAND, _SET, "15.40/7.90"),
    _product("LK-2005", "Socket Set, 3/8 in Drive, 40-Piece", _HAND, _SET, "64.00/38.50"),
    _product("LK-3001", "Magnetic Torpedo Level, 9 in", _LAYOUT, _EA, "11.80/6.10"),
    _product("LK-3002", "Box Level, 48 in", _LAYOUT, _EA, "42.00/24.60"),
    _product("LK-3003", "Combination Square, 12 in", _LAYOUT, _EA, "17.60/9.20"),
    _product("LK-3004", "Chalk Line Reel, 100 ft", _LAYOUT, _EA, "9.90/4.70"),
    _product("LK-4001", "Rolling Tool Chest, 5-Drawer", _STORAGE, _EA, "189.00/121.00"),
    _product("LK-4002", "Parts Organizer, 20 Compartments", _STORAGE, _EA, "21.50/11.80"),
)

LARKSPUR_RULES = (
    RuleSpec(_FLOOR, "Margin floor", Decimal("0.20")),
    RuleSpec(
        _VOLUME,
        "Accessory volume pricing",
        brackets=_brackets((6, "0.05"), (24, "0.10")),
        category=_TOOLS,
    ),
    RuleSpec(_TIER, "Gold contractor discount", Decimal("0.05"), customer_tier=_GOLD),
)


LARKSPUR_STORIES = StoryPlan(
    counts={_K.WON: 4, _K.LOST: 1, _K.SENT: 2, _K.PENDING: 1, _K.EXPIRED_SENT: 1, _K.DRAFT: 1},
    # Accessories from 24 (10%) for the gold contractor (5%) pass Larkspur's 10% threshold.
    heavy={_TOOLS: (24, 40)},
    eased={_TOOLS: (6, 18)},
)


def _larkspur(seed: int) -> DemoTenant:
    return DemoTenant(
        name="Larkspur Tool Co.",
        currency="USD",
        tax_rate=Decimal("0.06"),
        approval_threshold=Decimal("0.10"),
        quote_prefix="LT",
        order_prefix="LTO",
        quote_validity_days=21,
        admin=Person("Robin Hale", "robin@larkspur.example", Role.ADMIN),
        people=(Person("Riley Shaw", "riley@larkspur.example", Role.SALES_REP),),
        products=LARKSPUR_PRODUCTS,
        customers=demo_customers(
            generator(seed, "larkspur/customers"),
            account_prefix="L-",
            tiers={CustomerTier.GOLD: 1, CustomerTier.SILVER: 2, CustomerTier.STANDARD: 5},
        ),
        rules=LARKSPUR_RULES,
        stories=LARKSPUR_STORIES,
    )


def demo_tenants(seed: int) -> tuple[DemoTenant, ...]:
    """Northfield first: loading stops there, changing nothing, if it already exists."""
    return (_northfield(seed), _larkspur(seed))
