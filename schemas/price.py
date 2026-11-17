"""
price.created / price.updated / price.imported -> ProductLicense

The license terms come from the price custom_data, set in the Paddle dashboard (unknown keys are ignored):
- {"trial": true, "duration": 14}   trial offer of its product, 14-day trial (usually a $0 one-time price),
                                    always 1 activation seat (a trial is bound to one machine)
- {"duration": 365}                 paid license expiring 365 days after it is issued
- {"seats": 3}                      paid license activatable on 3 machines (default 2)
- no custom_data / no duration      perpetual paid license, 2 seats
Paddle's own `trial_period` / `billing_cycle` (subscriptions) are not used: trials are anonymous per-machine
trials started by the installer, and licenses are one-time purchases.

The price `name` (required here, shown to customers) and `description` label the license on the website.

Docs: https://developer.paddle.com/webhooks/prices/price-updated
"""
from datetime import datetime
from typing import Annotated

from pydantic import Field, StringConstraints, computed_field, field_validator, model_validator

from schemas.base import Money, PaddleModel, PaddleWebhook, PriceId, ProductId, Status


# ProductLicense.name is 255 characters
PriceName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class PriceCustomData(PaddleModel):
    """
    custom_data set on the price in the Paddle dashboard
    """

    trial: bool = False  # -> ProductLicense.is_trial
    duration: Annotated[int, Field(ge=1)] | None = None  # days -> ProductLicense.life_duration
    # -> ProductLicense.activation_seats of a paid license (a trial always has 1)
    seats: Annotated[int, Field(ge=1)] = 2

    @model_validator(mode="after")
    def trial_needs_duration(self) -> "PriceCustomData":
        if self.trial and self.duration is None:
            raise ValueError("a trial price needs a duration (days) in its custom_data")
        return self


class Price(PaddleModel):
    id: PriceId  # -> ProductLicense.paddle_price_id
    product_id: ProductId  # -> Product lookup by paddle_product_id
    # -> ProductLicense.name: the label shown to customers (checkout, website). Optional in Paddle but
    # required here: a price without a name is rejected and its license is not synced
    name: PriceName
    # -> ProductLicense.description (None when empty)
    description: str | None = None
    unit_price: Money  # -> ProductLicense unit price + currency
    custom_data: PriceCustomData | None = None  # Paddle sends null when none is set
    status: Status  # -> ProductLicense.archived
    updated_at: datetime  # -> ProductLicense.updated_at: older webhooks are skipped (out-of-order delivery)

    @field_validator("description")
    @classmethod
    def empty_description_is_none(cls, value: str | None) -> str | None:
        return (value or "").strip() or None

    @computed_field
    @property
    def is_trial(self) -> bool:
        """-> ProductLicense.is_trial"""
        return self.custom_data is not None and self.custom_data.trial

    @computed_field
    @property
    def duration_days(self) -> int | None:
        """
        -> ProductLicense.life_duration: the trial length for a trial price, else the license lifetime
        (None = perpetual)
        """
        return self.custom_data.duration if self.custom_data else None

    @computed_field
    @property
    def activation_seats(self) -> int:
        """
        -> ProductLicense.activation_seats (copied into each UserLicense when it is issued):
        1 for a trial (bound to a single machine: UserTrial is unique per product and machine),
        else custom_data `seats` (default 2)
        """
        if self.is_trial:
            return 1
        return (self.custom_data or PriceCustomData()).seats


PriceWebhook = PaddleWebhook[Price]
