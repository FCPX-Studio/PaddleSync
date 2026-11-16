"""
adjustment.created / adjustment.updated -> OrderItem refund state (+ OrderRefunded for AuthAPI)

Paddle reports refunds, credits and chargebacks of a transaction as adjustments, item by item.
Only an approved refund or chargeback of a whole line item ("full") revokes its licenses:
a partial or tax-only refund, a credit, or a pending / rejected adjustment changes nothing.

Docs: https://developer.paddle.com/webhooks/adjustments/adjustment-updated
"""
from datetime import datetime
from typing import Literal

from pydantic import Field

from schemas.base import AdjustmentId, PaddleModel, PaddleWebhook, TransactionId, TransactionItemId

# Paddle action -> OrderItem.refund_reason / UserLicense.revoked_reason
REVOKING_ACTIONS: dict[str, Literal["refund", "chargeback"]] = {"refund": "refund", "chargeback": "chargeback"}


class AdjustmentItem(PaddleModel):
    item_id: TransactionItemId  # -> OrderItem.paddle_item_id
    # full: the whole line item. partial / tax / proration: an amount only, licenses are kept
    type: str


class Adjustment(PaddleModel):
    id: AdjustmentId
    # refund, credit, chargeback, chargeback_reverse, chargeback_warning, credit_reverse...
    action: str
    # pending_approval, approved, rejected, reversed
    status: str
    transaction_id: TransactionId  # -> Order.paddle_order_id
    items: list[AdjustmentItem] = Field(min_length=1)
    updated_at: datetime  # -> OrderItem.refunded_at

    @property
    def refund_reason(self) -> Literal["refund", "chargeback"] | None:
        """Why the licenses of its full items are revoked, None if this adjustment revokes nothing"""
        if self.status != "approved":
            return None
        return REVOKING_ACTIONS.get(self.action)

    @property
    def full_item_ids(self) -> list[str]:
        """Line items refunded / charged back as a whole, in order, without duplicates"""
        return list(dict.fromkeys(item.item_id for item in self.items if item.type == "full"))


AdjustmentWebhook = PaddleWebhook[Adjustment]
