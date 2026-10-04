"""Money in JSON (ADR-0003): ``{"amount": "12.5000", "currency": "USD"}``.

The amount is a decimal string, never a JSON number, so no client parses it into a float. Responses
always carry four decimal places; requests may send fewer, never more.
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from pricewright.domain.money import AMOUNT_DECIMAL_PLACES, Money

_FOUR_PLACES = Decimal(1).scaleb(-AMOUNT_DECIMAL_PLACES)


class MoneyJson(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    amount: str = Field(
        pattern=rf"^-?\d{{1,14}}(\.\d{{1,{AMOUNT_DECIMAL_PLACES}}})?$",
        description="A decimal string with up to 4 decimal places; never a JSON number.",
        examples=["12.5000"],
    )
    currency: str = Field(
        pattern=r"^[A-Z]{3}$", description="ISO 4217 code: the tenant's currency.", examples=["USD"]
    )

    @classmethod
    def of(cls, money: Money) -> MoneyJson:
        return cls(amount=str(money.amount.quantize(_FOUR_PLACES)), currency=money.currency)

    def to_money(self) -> Money:
        return Money(Decimal(self.amount), self.currency)
