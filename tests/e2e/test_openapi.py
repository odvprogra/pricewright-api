from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel, Field

from pricewright.main import app_factory, render_openapi

COMMITTED_SPEC = Path(__file__).resolve().parents[2] / "openapi.json"


def test_committed_openapi_spec_matches_the_application() -> None:
    committed = COMMITTED_SPEC.read_text(encoding="utf-8")

    assert committed == render_openapi(app_factory()), (
        "openapi.json is out of date: run `just openapi` and commit it"
    )


class _Order(BaseModel):
    quantity: int = Field(ge=1, le=365)


def test_rendered_spec_writes_whole_numbers_as_integers() -> None:
    app = FastAPI()

    @app.post("/orders")
    async def create_order(order: _Order) -> _Order:
        return order

    rendered = render_openapi(app)

    assert '"maximum": 365,' in rendered
    assert "365.0" not in rendered


class _Invoice(BaseModel):
    """Due in 30 days (§ 4), payable in €."""


def test_rendered_spec_writes_text_as_utf8_not_escapes() -> None:
    app = FastAPI()

    @app.post("/invoices")
    async def create_invoice(invoice: _Invoice) -> _Invoice:
        return invoice

    rendered = render_openapi(app)

    assert "Due in 30 days (§ 4), payable in €." in rendered
    assert "\\u00a7" not in rendered
