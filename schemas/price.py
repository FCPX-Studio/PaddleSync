"""
price.created / price.updated / price.imported -> ProductLicense (and Product trial length)

Docs: https://developer.paddle.com/webhooks/prices/price-updated
Trials: https://developer.paddle.com/concepts/subscriptions/trials (early access, API only)
"""
from datetime import datetime

from pydantic import computed_field

from schemas.base import Duration, Money, PaddleModel, PaddleWebhook, PriceId, ProductId, Status


class TrialPeriod(Duration):
    requires_payment_method: bool = True


class Price(PaddleModel):
    id: PriceId  # -> ProductLicense.paddle_price_id
    product_id: ProductId  # -> Product lookup by paddle_product_id
    unit_price: Money  # -> ProductLicense unit price + currency
    billing_cycle: Duration | None
    trial_period: TrialPeriod | None
    status: Status  # -> ProductLicense.archived
    updated_at: datetime  # -> ProductLicense.updated_at: older webhooks are skipped (out-of-order delivery)

    @computed_field
    @property
    def is_trial(self) -> bool:
        """-> ProductLicense.is_trial"""
        return self.trial_period is not None

    @computed_field
    @property
    def lifetime(self) -> Duration | None:
        """
        -> ProductLicense lifetime (None = perpetual), and Product trial length for a trial price
        """
        if self.trial_period is None:
            return None
        return Duration(interval=self.trial_period.interval, frequency=self.trial_period.frequency)


PriceWebhook = PaddleWebhook[Price]
