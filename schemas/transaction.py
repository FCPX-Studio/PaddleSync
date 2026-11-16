"""
transaction.completed -> Order + OrderItem

Docs: https://developer.paddle.com/webhooks/transactions/transaction-completed
"""
from typing import Literal

from pydantic import Field, field_validator

from schemas.base import (
    Amount,
    CurrencyCode,
    CustomerId,
    PaddleModel,
    PaddleWebhook,
    PriceId,
    TransactionId,
    TransactionItemId,
)

# Origins that are a purchase. Others (e.g. "subscription_recurring", the charge after a trial ends)
# are skipped: they would create a second Order for the same ProductLicense.
PURCHASE_ORIGINS = frozenset({"web", "api"})  # "api" includes cardless trials, completed automatically


class Totals(PaddleModel):
    grand_total: Amount  # -> Order grand total
    currency_code: CurrencyCode  # -> Order currency


class LineItem(PaddleModel):
    id: TransactionItemId  # -> OrderItem.paddle_item_id
    price_id: PriceId  # -> ProductLicense lookup by paddle_price_id
    quantity: int = Field(ge=1)  # -> OrderItem.quantity


class TransactionDetails(PaddleModel):
    totals: Totals
    line_items: list[LineItem] = Field(min_length=1)

    @field_validator("line_items")
    @classmethod
    def one_line_per_price(cls, items: list[LineItem]) -> list[LineItem]:
        """
        A ProductLicense appears at most once per Order
        """
        price_ids = [item.price_id for item in items]
        duplicates = sorted({p for p in price_ids if price_ids.count(p) > 1})
        if duplicates:
            raise ValueError(f"price appears on several line items: {', '.join(duplicates)}")
        return items


class Transaction(PaddleModel):
    id: TransactionId  # -> Order.paddle_transaction_id
    status: Literal["completed"]
    customer_id: CustomerId  # nullable in Paddle, but a completed transaction always has one
    currency_code: CurrencyCode
    origin: str
    details: TransactionDetails

    @property
    def is_purchase(self) -> bool:
        return self.origin in PURCHASE_ORIGINS


TransactionWebhook = PaddleWebhook[Transaction]
