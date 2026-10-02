from pathlib import Path

from pricewright.main import app_factory, render_openapi

COMMITTED_SPEC = Path(__file__).resolve().parents[2] / "openapi.json"


def test_committed_openapi_spec_matches_the_application() -> None:
    committed = COMMITTED_SPEC.read_text(encoding="utf-8")

    assert committed == render_openapi(app_factory()), (
        "openapi.json is out of date: run `just openapi` and commit it"
    )
