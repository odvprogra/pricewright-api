"""Every endpoint that takes an id, as another tenant would call it (ADR-0006, ADR-0009).

The integration suite calls each one with Northfield's ids as Larkspur's admin and expects a 404.
The architecture guard fails when a route with a path parameter has no case here, so a new endpoint
cannot skip the check.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class IsolationCase:
    method: str
    path: str
    """Route template; ``{user_id}``, ``{account_id}``, ``{key_id}`` ... are Northfield's ids."""
    json: dict[str, object] | None = None
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return f"{self.method} {self.path}"


CASES = (
    IsolationCase("GET", "/api/v1/users/{user_id}"),
    IsolationCase(
        "PATCH", "/api/v1/users/{user_id}", json={"is_active": False}, headers={"If-Match": '"1"'}
    ),
    IsolationCase("POST", "/api/v1/users/{user_id}/unlock"),
    IsolationCase("GET", "/api/v1/service-accounts/{account_id}"),
    IsolationCase("GET", "/api/v1/service-accounts/{account_id}/keys"),
    IsolationCase("POST", "/api/v1/service-accounts/{account_id}/keys", json={}),
    IsolationCase("DELETE", "/api/v1/service-accounts/{account_id}/keys/{key_id}"),
    IsolationCase("GET", "/api/v1/product-categories/{category_id}"),
    IsolationCase(
        "PATCH",
        "/api/v1/product-categories/{category_id}",
        json={"name": "Hijacked"},
        headers={"If-Match": '"1"'},
    ),
    IsolationCase("GET", "/api/v1/products/{product_id}"),
    IsolationCase(
        "PATCH",
        "/api/v1/products/{product_id}",
        json={"is_active": False},
        headers={"If-Match": '"1"'},
    ),
    IsolationCase("GET", "/api/v1/customers/{customer_id}"),
    IsolationCase(
        "PATCH",
        "/api/v1/customers/{customer_id}",
        json={"tier": "gold"},
        headers={"If-Match": '"1"'},
    ),
    IsolationCase("GET", "/api/v1/pricing-rules/{rule_id}"),
    IsolationCase(
        "PATCH",
        "/api/v1/pricing-rules/{rule_id}",
        json={"is_active": False},
        headers={"If-Match": '"1"'},
    ),
    IsolationCase("GET", "/api/v1/quotes/{quote_id}"),
    IsolationCase(
        "PATCH",
        "/api/v1/quotes/{quote_id}",
        json={"notes": "Hijacked"},
        headers={"If-Match": '"1"'},
    ),
    IsolationCase(
        "POST",
        "/api/v1/quotes/{quote_id}/lines",
        json={"product_id": "01900000-0000-7000-8000-000000000000", "quantity": "1"},
        headers={"If-Match": '"1"'},
    ),
    IsolationCase(
        "PATCH",
        "/api/v1/quotes/{quote_id}/lines/{line_id}",
        json={"quantity": "99"},
        headers={"If-Match": '"1"'},
    ),
    IsolationCase(
        "DELETE", "/api/v1/quotes/{quote_id}/lines/{line_id}", headers={"If-Match": '"1"'}
    ),
)
