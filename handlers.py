"""
One handler per Paddle entity: validate the entity, then write it to the DB.

`event.payload` is the webhook's `data` object (the entity), as published by EventAPI
(see events.models.PaddleEvent), not the whole webhook body.

Handlers are async (listener awaits them) but the repositories are sync: a DB call blocks the
event loop while it runs, which is fine since EventConsumer processes one message at a time.

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



EventHandler = Callable[[PaddleEvent], Awaitable[None]]
_publisher: EventPublisher | None = None

product_repository = ProductRepository()
user_repository = UserRepository()


def _get_publisher() -> EventPublisher:
    global _publisher
    if _publisher is None:
        _publisher = EventPublisher()
    return _publisher


def _log_stale(event: PaddleEvent, entity_id: str) -> None:
    logger.info("Skipped stale %s %s: a newer version of %s is stored", event.event_type, event.paddle_event_id, entity_id)


async def handle_product(event: PaddleEvent) -> None:
    product = Product.model_validate(event.payload)
    if not product_repository.upsert_product(product):
        _log_stale(event, product.id)


async def handle_customer(event: PaddleEvent) -> None:
    customer = Customer.model_validate(event.payload)
    if not user_repository.upsert_user(customer):
        _log_stale(event, customer.id)


async def handle_price(event: PaddleEvent) -> None:
    price = Price.model_validate(event.payload)
    if not product_repository.upsert_license(price):
        _log_stale(event, price.id)


async def handle_transaction_completed(event: PaddleEvent) -> None:
    transaction = Transaction.model_validate(event.payload)
    if not transaction.is_purchase:
        logger.info("Skipping transaction %s with origin %s", transaction.id, transaction.origin)
        return

    order_id = user_repository.upsert_order(transaction)
    # After the commit. If publishing fails the event is retried: upsert_order returns the same
    # order and the event is published again, so AuthAPI must be idempotent too (it is).
    await _get_publisher().publish(ORDERS_READY, OrderReady(order_id=order_id, paddle_order_id=transaction.id))


async def handle_adjustment(event: PaddleEvent) -> None:
    adjustment = Adjustment.model_validate(event.payload)
    reason = adjustment.refund_reason
    item_ids = adjustment.full_item_ids
    if reason is None or not item_ids:
        # Credit, chargeback warning / reverse, pending or rejected, partial / tax-only refund...
        logger.info(
            "Skipping adjustment %s (%s, %s): no line item fully refunded",
            adjustment.id, adjustment.action, adjustment.status,
        )
        return

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
