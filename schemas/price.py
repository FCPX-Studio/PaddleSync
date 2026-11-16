"""
price.created / price.updated / price.imported -> ProductLicense

The license terms come from the price custom_data, set in the Paddle dashboard (unknown keys are ignored):
- {"trial": true, "duration": 14}   trial offer of its product, 14-day trial (usually a $0 one-time price)
- {"duration": 365}                 paid license expiring 365 days after it is issued
- no custom_data / no duration      perpetual paid license
Paddle's own `trial_period` / `billing_cycle` (subscriptions) are not used: trials are anonymous per-machine
trials started by the installer, and licenses are one-time purchases.

Docs: https://developer.paddle.com/webhooks/prices/price-updated
"""
from datetime import datetime
from typing import Annotated

from pydantic import Field, computed_field, model_validator

from schemas.base import Money, PaddleModel, PaddleWebhook, PriceId, ProductId, Status


class PriceCustomData(PaddleModel):
    """
    custom_data set on the price in the Paddle dashboard
    """

    trial: bool = False  # -> ProductLicense.is_trial
    duration: Annotated[int, Field(ge=1)] | None = None  # days -> ProductLicense.life_duration

    @model_validator(mode="after")
    def trial_needs_duration(self) -> "PriceCustomData":
        if self.trial and self.duration is None:
            raise ValueError("a trial price needs a duration (days) in its custom_data")
        return self


class Price(PaddleModel):
    id: PriceId  # -> ProductLicense.paddle_price_id
    product_id: ProductId  # -> Product lookup by paddle_product_id
    unit_price: Money  # -> ProductLicense unit price + currency
    custom_data: PriceCustomData | None = None  # Paddle sends null when none is set
    status: Status  # -> ProductLicense.archived
    updated_at: datetime  # -> ProductLicense.updated_at: older webhooks are skipped (out-of-order delivery)

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


PriceWebhook = PaddleWebhook[Price]
