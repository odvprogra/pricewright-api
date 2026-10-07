"""The demo tenants' customers: fictional businesses with tiers, payment terms and tax ids.

Names combine an invented place with a trade, drawn from these lists rather than a fake-data
library, so they read as the businesses a distributor serves and the set never changes with a
library's release. Tax ids are shaped like a US EIN but start with 00, a prefix the IRS never
assigns, so none can belong to a real company.
"""

import random
from collections.abc import Mapping

from pricewright.application.customers import NewCustomer
from pricewright.demo.randomness import between, chance, pick, shuffled
from pricewright.domain.customers import CustomerTier

PLACES = (
    "Alderbrook",
    "Ashgrove",
    "Bayline",
    "Birchmont",
    "Blue Heron",
    "Brightwater",
    "Cedar Ridge",
    "Clearfork",
    "Copperline",
    "Crescent Valley",
    "Driftwood",
    "Eastbrook",
    "Elmstead",
    "Fairhaven",
    "Fox Hollow",
    "Glenmoor",
    "Granite Point",
    "Hollowell",
    "Ironwood",
    "Juniper Hill",
    "Kestrel Bay",
    "Lakeshore",
    "Millbrook",
    "Northwind",
    "Oakhaven",
    "Pinecrest",
    "Quarry Hill",
    "Redfern",
    "Ridgeline",
    "Riverstone",
    "Sagebrush",
    "Silver Maple",
    "Stonegate",
    "Thornbury",
    "Tidewater",
    "Westbrook",
    "Willow Creek",
    "Wrenfield",
)
TRADES = (
    "Fabrication",
    "Mechanical Contractors",
    "Facility Services",
    "Property Management",
    "Precision Machining",
    "Builders",
    "Logistics",
    "Print & Mail",
    "Electric",
    "Plumbing & Heating",
    "Medical Clinic",
    "Hospitality Group",
    "Food Processing",
    "Auto Body",
    "Packaging",
    "Laboratories",
    "Storage Centers",
    "Landscaping",
    "Millwork",
    "Marine Services",
)
SUFFIXES = ("LLC", "Inc.", "Co.", "")
PAYMENT_TERMS: Mapping[CustomerTier, tuple[int, ...]] = {
    # Net 30 is the usual B2B default; some small accounts pay on receipt or in 15 days, larger
    # ones negotiate longer terms.
    CustomerTier.STANDARD: (30, 30, 30, 30, 30, 30, 15, 15, 0, 0),
    CustomerTier.SILVER: (30, 30, 45),
    CustomerTier.GOLD: (45, 45, 60),
}
TAX_ID_SHARE = 0.75
"""Most business customers give a tax id; the rest are left without one."""


def _names(rng: random.Random, count: int) -> list[str]:
    names: list[str] = []
    while len(names) < count:
        suffix = pick(rng, SUFFIXES)
        name = f"{pick(rng, PLACES)} {pick(rng, TRADES)} {suffix}".strip()
        if name not in names:
            names.append(name)
    return names


def demo_customers(
    rng: random.Random, *, account_prefix: str, tiers: Mapping[CustomerTier, int]
) -> tuple[NewCustomer, ...]:
    """``sum(tiers.values())`` customers numbered ``{account_prefix}0001`` on, tiers shuffled."""
    tier_of = shuffled(rng, [tier for tier, count in tiers.items() for _ in range(count)])
    customers = []
    for number, (name, tier) in enumerate(zip(_names(rng, len(tier_of)), tier_of, strict=True), 1):
        tax_id = f"00-{between(rng, 0, 9_999_999):07d}" if chance(rng, TAX_ID_SHARE) else None
        customers.append(
            NewCustomer(
                account_number=f"{account_prefix}{number:04d}",
                name=name,
                tier=tier,
                payment_terms_days=pick(rng, PAYMENT_TERMS[tier]),
                tax_id=tax_id,
            )
        )
    return tuple(customers)
