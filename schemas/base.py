"""
Shared pieces of the Paddle webhook models.

Only the fields PaddleSync uses are declared. Every other field Paddle sends is ignored,
so a new field on Paddle's side never breaks validation.
Docs: https://developer.paddle.com/webhooks/overview
"""
from datetime import datetime
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class PaddleModel(BaseModel):
    """
    Base of every Paddle model: unknown fields are ignored, instances are read-only
    """

    model_config = ConfigDict(extra="ignore", frozen=True)


# Paddle IDs: a type prefix and 26 lowercase alphanumerics, e.g. "ctm_01hv6y1jedq4p1n0yqn5ba3ky4"
EventId = Annotated[str, StringConstraints(pattern=r"^evt_[a-z\d]{26}$")]
NotificationId = Annotated[str, StringConstraints(pattern=r"^ntf_[a-z\d]{26}$")]
CustomerId = Annotated[str, StringConstraints(pattern=r"^ctm_[a-z\d]{26}$")]
ProductId = Annotated[str, StringConstraints(pattern=r"^pro_[a-z\d]{26}$")]
PriceId = Annotated[str, StringConstraints(pattern=r"^pri_[a-z\d]{26}$")]
TransactionId = Annotated[str, StringConstraints(pattern=r"^txn_[a-z\d]{26}$")]
TransactionItemId = Annotated[str, StringConstraints(pattern=r"^txnitm_[a-z\d]{26}$")]
AdjustmentId = Annotated[str, StringConstraints(pattern=r"^adj_[a-z\d]{26}$")]

# ISO 4217 code, e.g. "USD"
CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
# Paddle sends amounts as strings in minor units ("1000" = 10.00 USD), stored as int cents
Amount = Annotated[int, Field(ge=0)]

# Paddle entities can be archived but never deleted
Status = Literal["active", "archived"]


class Money(PaddleModel):
    amount: Amount
    currency_code: CurrencyCode


class Duration(PaddleModel):
    """
    A Paddle period, e.g. 14 days or 1 year.
    Kept as interval + frequency: months and years have no fixed length in days.
    """

    interval: Literal["day", "week", "month", "year"]
    frequency: int = Field(ge=1)


DataT = TypeVar("DataT", bound=PaddleModel)


class PaddleWebhook(PaddleModel, Generic[DataT]):
    """
    Envelope of every Paddle webhook notification
    """

    event_id: EventId
    event_type: str
    notification_id: NotificationId
    occurred_at: datetime
    data: DataT
