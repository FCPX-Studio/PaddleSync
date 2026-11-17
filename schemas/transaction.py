"""
transaction.completed -> Order + OrderItem

Docs: https://developer.paddle.com/webhooks/transactions/transaction-completed
"""
import re
from typing import Any, Literal

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


# Order.claim_hash: SHA-256 of the claim secret, 64 lowercase hex (the db CHECK)
CLAIM_HASH_PATTERN = re.compile(r"[0-9a-f]{64}")


class TransactionCustomData(PaddleModel):
    """
    custom_data the website sets on the checkout (Paddle.Checkout.open customData). Unknown keys are ignored
    """

    # -> Order.claim_hash: SHA-256 of a secret only the buyer's browser holds (see Order.claim_hash).
    # Anything else (missing, not a string, malformed) becomes None: never reject a paid order over it,
    # the order just can't be claimed on the confirmation page (keys by email)
    claim_hash: str | None = None

    @field_validator("claim_hash", mode="before")
    @classmethod
    def valid_or_none(cls, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        value = value.strip().lower()
        return value if CLAIM_HASH_PATTERN.fullmatch(value) else None


class Transaction(PaddleModel):
    id: TransactionId  # -> Order.paddle_transaction_id
    status: Literal["completed"]
    customer_id: CustomerId  # nullable in Paddle, but a completed transaction always has one
    currency_code: CurrencyCode
    origin: str
    details: TransactionDetails
    # Paddle sends null when none is set. Not an object (a list, a string...): ignored like a missing one
    custom_data: TransactionCustomData | None = None

    @field_validator("custom_data", mode="before")
    @classmethod
    def object_or_none(cls, value: Any) -> Any:
        return value if isinstance(value, dict) else None

    @property
    def claim_hash(self) -> str | None:
        """-> Order.claim_hash, None if the checkout set none (or an invalid one)"""
        return self.custom_data.claim_hash if self.custom_data else None

    @property
    def is_purchase(self) -> bool:
        return self.origin in PURCHASE_ORIGINS


TransactionWebhook = PaddleWebhook[Transaction]
