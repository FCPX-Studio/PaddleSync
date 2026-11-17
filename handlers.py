"""
One handler per Paddle entity: validate the entity, then write it to the DB.

`event.payload` is the webhook's `data` object (the entity), as published by EventAPI
(see events.models.PaddleEvent), not the whole webhook body.

Handlers are async (listener awaits them) but the repositories are sync: a DB call blocks the
event loop while it runs, which is fine since EventConsumer processes one message at a time.

Each handler returns True if the event was applied, False if it was skipped on purpose (stale, not a
purchase, nothing refunded): it then logs why, and the listener doesn't log it as processed.

Errors (see listener.hot_event):
- pydantic.ValidationError: the payload can't be used, logged and acked.
- anything else (DB down, MissingDependencyError...): retried later by EventConsumer.
"""
from logger import logger
from collections.abc import Awaitable, Callable

from events import EventPublisher, OrderReady, OrderRefunded, PaddleEvent
from events.streams import ORDERS_READY, ORDERS_REFUNDED

from repositories import UserRepository, ProductRepository

from schemas.adjustment import Adjustment
from schemas.customer import Customer
from schemas.price import Price
from schemas.product import Product
from schemas.transaction import Transaction

from storage import fetch_download_size



# Returns True if the event was applied, False if skipped (the handler logs why)
EventHandler = Callable[[PaddleEvent], Awaitable[bool]]
_publisher: EventPublisher | None = None

product_repository = ProductRepository()
user_repository = UserRepository()


def _get_publisher() -> EventPublisher:
    global _publisher
    if _publisher is None:
        _publisher = EventPublisher()
    return _publisher


def _skipped(event: PaddleEvent, reason: str, *args: object) -> bool:
    """Log why an event is skipped on purpose, return False (not applied)"""
    logger.info("Skipped %s %s: " + reason, event.event_type, event.paddle_event_id, *args)
    return False


def _applied_or_stale(event: PaddleEvent, written: bool, entity_id: str) -> bool:
    return written or _skipped(event, "stale, a newer version of %s is stored", entity_id)


async def handle_product(event: PaddleEvent) -> bool:
    product = Product.model_validate(event.payload)
    # Before the DB write, never inside its transaction. On every product event, stale ones included:
    # product events are rare, and a size missing after a failed request is filled in by the next one
    download_size = await fetch_download_size(product.custom_data.download)
    return _applied_or_stale(event, product_repository.upsert_product(product, download_size), product.id)


async def handle_customer(event: PaddleEvent) -> bool:
    customer = Customer.model_validate(event.payload)
    return _applied_or_stale(event, user_repository.upsert_user(customer), customer.id)


async def handle_price(event: PaddleEvent) -> bool:
    price = Price.model_validate(event.payload)
    return _applied_or_stale(event, product_repository.upsert_license(price), price.id)


async def handle_transaction_completed(event: PaddleEvent) -> bool:
    transaction = Transaction.model_validate(event.payload)
    if not transaction.is_purchase:
        return _skipped(event, "transaction %s is not a purchase (origin %s)", transaction.id, transaction.origin)

    order = user_repository.upsert_order(transaction)
    # After the commit. If publishing fails the event is retried: upsert_order returns the same
    # order and the event is published again, so AuthAPI must be idempotent too (it is).
    await _get_publisher().publish(ORDERS_READY, OrderReady(order_id=order.order_id, paddle_order_id=transaction.id))

    # Never fails the event: a retry would publish the order again
    try:
        logger.new_order(order.log)
    except Exception:
        logger.warning("Order log failed for %s", transaction.id)
    return True


async def handle_adjustment(event: PaddleEvent) -> bool:
    adjustment = Adjustment.model_validate(event.payload)
    reason = adjustment.refund_reason
    item_ids = adjustment.full_item_ids
    if reason is None or not item_ids:
        # Credit, chargeback warning / reverse, pending or rejected, partial / tax-only refund...
        return _skipped(
            event, "adjustment %s (%s, %s) fully refunds no line item", adjustment.id, adjustment.action, adjustment.status
        )

    order_id = user_repository.mark_items_refunded(adjustment, reason, item_ids)
    # After the commit. Published again on a retry or redelivery (same items): AuthAPI revokes idempotently
    await _get_publisher().publish(
        ORDERS_REFUNDED,
        OrderRefunded(
            order_id=order_id,
            paddle_order_id=adjustment.transaction_id,
            paddle_item_ids=item_ids,
            reason=reason,
        ),
    )
    return True


# Paddle event type -> handler. Event types not listed here are ignored.
HANDLERS: dict[str, EventHandler] = {
    "product.created": handle_product,
    "product.updated": handle_product,
    "product.imported": handle_product,
    "customer.created": handle_customer,
    "customer.updated": handle_customer,
    "customer.imported": handle_customer,
    "price.created": handle_price,
    "price.updated": handle_price,
    "price.imported": handle_price,
    "transaction.completed": handle_transaction_completed,
    "adjustment.created": handle_adjustment,
    "adjustment.updated": handle_adjustment,
}
