"""Every route that takes an id is in the tenant isolation suite (ADR-0006).

Adding an endpoint like ``/quotes/{quote_id}`` fails here until ``tests/isolation_cases.py`` says
how to call it, so the suite never silently falls behind the API. Routes are read from the OpenAPI
spec: the public contract, independent of how FastAPI nests its routers.
"""

from pricewright.api.app import create_app
from tests.fakes import fake_services
from tests.isolation_cases import CASES


def test_every_route_with_a_path_parameter_has_an_isolation_case() -> None:
    spec = create_app(title="test", services=fake_services()).openapi()
    routes = {
        f"{method.upper()} {path}"
        for path, operations in spec["paths"].items()
        if "{" in path
        for method in operations
    }

    assert routes == {case.name for case in CASES}
