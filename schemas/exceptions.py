"""
Errors raised while applying a Paddle event to the DB (see listener.hot_event for the retry policy).

Each error takes the values it is about and builds its own message. Messages carry Paddle ids
only, never customer data (emails, names): they end up in the logs.
"""
from collections.abc import Iterable

from events import RetryLater


class MissingDependencyError(RetryLater):
    """
    A row the webhook refers to isn't in the DB yet (e.g. a transaction arrives before its
    customer or price webhook). Transient: the event is retried later by EventConsumer, which logs
    it as one WARNING line (events.RetryLater), not a traceback.
    """

    def __init__(self, entity: str, entity_ids: str | Iterable[str], needed_by: str, needed_by_id: str) -> None:
        """
        Args:
            entity (str): Kind of the missing row(s), e.g. "Product"
            entity_ids (str | Iterable[str]): Paddle id(s) of the missing row(s)
            needed_by (str): Kind of the entity that refers to them, e.g. "price"
            needed_by_id (str): Paddle id of that entity
        """
        self.entity = entity
        self.entity_ids = [entity_ids] if isinstance(entity_ids, str) else sorted(entity_ids)
        self.needed_by = needed_by
        self.needed_by_id = needed_by_id
        super().__init__(f"{entity} {', '.join(self.entity_ids)} not found for {needed_by} {needed_by_id}")


class PermanentError(Exception):
    """
    The event can never be applied, whatever the retries (e.g. it conflicts with existing data).
    Logged and acked by the listener, like a ValidationError.
    """


class EmailConflictError(RetryLater):
    """
    Another Paddle customer already uses this email (User.email is unique).

    Transient, retried by EventConsumer (one WARNING line, events.RetryLater): Paddle keeps emails unique, so a conflict means the DB is
    behind (e.g. the other customer's email change hasn't been synced yet). A conflict that
    outlives the retries ends up in `paddle:events:dead`, where it can be inspected and replayed.
    """

    def __init__(self, customer_id: str) -> None:
        """
        Args:
            customer_id (str): Paddle id of the customer that could not be saved.
                The email itself is never taken: the message ends up in the logs.
        """
        self.customer_id = customer_id
        super().__init__(f"Email of customer {customer_id} is already used by another customer")


class BundleIdConflictError(PermanentError):
    """
    Another Paddle product already uses this bundle ID (Product.bundle_id is unique).
    A catalog misconfiguration: fix the product's custom_data in the Paddle dashboard.
    """

    def __init__(self, product_id: str, bundle_id: str) -> None:
        """
        Args:
            product_id (str): Paddle id of the product that could not be saved
            bundle_id (str): Its bundle ID, already used by another product
        """
        self.product_id = product_id
        self.bundle_id = bundle_id
        super().__init__(f"Bundle ID {bundle_id!r} of product {product_id} is already used by another product")
