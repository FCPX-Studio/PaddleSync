from db import Database
from db.models import OrderItemRow, OrderRow, ProductLicenseRow, ProductRow, UserRow

from logger import OrderLine, OrderLog

from typing import NamedTuple

from psycopg import errors as pg_errors
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from schemas.adjustment import Adjustment
from schemas.customer import Customer
from schemas.exceptions import EmailConflictError, MissingDependencyError
from schemas.transaction import Transaction

# Named by the db naming convention (uq_%(table_name)s_%(column_0_N_name)s)
EMAIL_CONSTRAINT = "uq_User_email"


class UpsertedOrder(NamedTuple):
    order_id: int
    # What the new order log shows: items with their product name and type, user name, total
    log: OrderLog


class UserRepository(Database):
    """
    Writes User, Order and OrderItem (owned by PaddleSync), including OrderItem refunds
    """

    def upsert_user(self, customer: Customer) -> bool:
        """
        Insert or update the User matching a Paddle customer.
        subscribed_at is left untouched on update.
        Stale webhooks are skipped: the row is only updated if the customer's `updated_at` is not older
        than the stored one (Paddle does not guarantee delivery order, and redelivers old events).

        Args:
            customer (Customer): Paddle customer

        Raises:
            EmailConflictError: Another customer already uses this email (transient, retried)

        Returns:
            bool: False if the webhook was stale and nothing was written
        """
        stmt = insert(UserRow).values(
            paddle_customer_id=customer.id,
            email=customer.email,
            name=customer.name,
            marketing=customer.marketing_consent,
            archived=customer.status == "archived",
            updated_at=customer.updated_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[UserRow.paddle_customer_id],
            set_={
                "email": stmt.excluded.email,
                "name": stmt.excluded.name,
                "marketing": stmt.excluded.marketing,
                "archived": stmt.excluded.archived,
                "updated_at": stmt.excluded.updated_at,
            },
            # Equal timestamps re-apply the same version (idempotent redelivery)
            where=or_(UserRow.updated_at.is_(None), UserRow.updated_at <= stmt.excluded.updated_at),
        ).returning(UserRow.id)
        # Wraps the whole session: the violation can surface at commit, not only at execute
        try:
            with self.session() as s:
                return s.execute(stmt).scalar_one_or_none() is not None
        except IntegrityError as e:
            if isinstance(e.orig, pg_errors.UniqueViolation) and e.orig.diag.constraint_name == EMAIL_CONSTRAINT:
                # from None: the chained psycopg error holds the email (DETAIL: Key (email)=...),
                # and this error is logged on every retry
                raise EmailConflictError(customer.id) from None
            raise

    def upsert_order(self, order: Transaction) -> UpsertedOrder:
        """
        Insert the Order and its OrderItems for a completed Paddle transaction, with the claim hash of its
        custom_data (Order.claim_hash).
        Idempotent: a retry returns the existing order and does not duplicate items, and never clears a
        stored claim hash.
        The user and license lookups also read what the new order log shows (user name, product name and
        type, trial or not): no extra query.

        Args:
            order (Transaction): Completed Paddle transaction

        Raises:
            MissingDependencyError: The customer or one of the prices isn't synced yet

        Returns:
            UpsertedOrder: Order.id, and the order as logged
        """
        line_items = order.details.line_items
        price_ids = {item.price_id for item in line_items}

        with self.session() as s:
            user = s.execute(
                select(UserRow.id, UserRow.name).where(UserRow.paddle_customer_id == order.customer_id)
            ).one_or_none()
            if user is None:
                raise MissingDependencyError("User", order.customer_id, "transaction", order.id)
            user_id = user.id

            licenses = {
                row.paddle_price_id: row
                for row in s.execute(
                    select(
                        ProductLicenseRow.paddle_price_id,
                        ProductLicenseRow.id,
                        ProductLicenseRow.is_trial,
                        ProductRow.name,
                        ProductRow.type,
                    )
                    .join(ProductRow, ProductRow.id == ProductLicenseRow.product_id)
                    .where(ProductLicenseRow.paddle_price_id.in_(price_ids))
                )
            }
            license_ids = {price_id: row.id for price_id, row in licenses.items()}
            missing = price_ids - license_ids.keys()
            if missing:
                raise MissingDependencyError("ProductLicense", missing, "transaction", order.id)

            # DO UPDATE (not DO NOTHING) so RETURNING gives the id on a retry too
            order_stmt = insert(OrderRow).values(
                user_id=user_id,
                paddle_order_id=order.id,
                total=order.details.totals.grand_total,
                currency_code=order.details.totals.currency_code,
                claim_hash=order.claim_hash,
            )
            order_stmt = order_stmt.on_conflict_do_update(
                index_elements=[OrderRow.paddle_order_id],
                set_={
                    "total": order_stmt.excluded.total,
                    "currency_code": order_stmt.excluded.currency_code,
                    # A redelivery without the hash (no custom_data, or an invalid value) keeps the stored one
                    "claim_hash": func.coalesce(order_stmt.excluded.claim_hash, OrderRow.claim_hash),
                },
            ).returning(OrderRow.id)
            order_id = s.execute(order_stmt).scalar_one()

            items_stmt = insert(OrderItemRow).values(
                [
                    {
                        "order_id": order_id,
                        "product_license_id": license_ids[item.price_id],
                        "paddle_item_id": item.id,
                        "quantity": item.quantity,
                    }
                    for item in line_items
                ]
            )
            items_stmt = items_stmt.on_conflict_do_nothing(index_elements=[OrderItemRow.paddle_item_id])
            s.execute(items_stmt)

        log = OrderLog(
            paddle_order_id=order.id,
            user_name=user.name,
            total=order.details.totals.grand_total,
            currency=order.details.totals.currency_code,
            items=[
                OrderLine.from_product(
                    licenses[item.price_id].name,
                    licenses[item.price_id].type,
                    licenses[item.price_id].is_trial,
                    item.quantity,
                )
                for item in line_items
            ],
        )
        return UpsertedOrder(order_id=order_id, log=log)

    def mark_items_refunded(self, adjustment: Adjustment, reason: str, item_ids: list[str]) -> int:
        """
        Mark line items of an Order as refunded / charged back (OrderItem.refunded_at, refund_reason).
        Idempotent: items already marked keep their first refund date and reason.

        Args:
            adjustment (Adjustment): Approved Paddle adjustment (its transaction and updated_at are used)
            reason (str): "refund" or "chargeback"
            item_ids (list[str]): Paddle line item IDs (txnitm_…) refunded as a whole

        Raises:
            MissingDependencyError: The order or one of its items isn't synced yet
                (the adjustment arrived before its transaction.completed)

        Returns:
            int: Order.id
        """
        with self.session() as s:
            order_id = s.scalar(select(OrderRow.id).where(OrderRow.paddle_order_id == adjustment.transaction_id))
            if order_id is None:
                raise MissingDependencyError("Order", adjustment.transaction_id, "adjustment", adjustment.id)

            known = set(
                s.scalars(
                    select(OrderItemRow.paddle_item_id).where(
                        OrderItemRow.order_id == order_id, OrderItemRow.paddle_item_id.in_(item_ids)
                    )
                )
            )
            missing = set(item_ids) - known
            if missing:
                raise MissingDependencyError("OrderItem", missing, "adjustment", adjustment.id)

            s.execute(
                update(OrderItemRow)
                .where(
                    OrderItemRow.order_id == order_id,
                    OrderItemRow.paddle_item_id.in_(item_ids),
                    OrderItemRow.refunded_at.is_(None),
                )
                .values(refunded_at=adjustment.updated_at, refund_reason=reason)
            )
            return order_id
