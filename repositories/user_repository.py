from db import Database
from db.models import OrderItemRow, OrderRow, ProductLicenseRow, UserRow

from psycopg import errors as pg_errors
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from schemas.customer import Customer
from schemas.exceptions import EmailConflictError, MissingDependencyError
from schemas.transaction import Transaction

# Named by the db naming convention (uq_%(table_name)s_%(column_0_N_name)s)
EMAIL_CONSTRAINT = "uq_User_email"


class UserRepository(Database):
    """
    Writes User, Order and OrderItem (owned by PaddleSync)
    """

    def upsert_user(self, customer: Customer) -> None:
        """
        Insert or update the User matching a Paddle customer.
        subscribed_at is left untouched on update.
        Stale webhooks are skipped: the row is only updated if the customer's `updated_at` is not older
        than the stored one (Paddle does not guarantee delivery order, and redelivers old events).

        Args:
            customer (Customer): Paddle customer

        Raises:
            EmailConflictError: Another customer already uses this email (transient, retried)
        """
        stmt = insert(UserRow).values(
            paddle_customer_id=customer.id,
            email=customer.email,
            name=customer.name,
            marketing=customer.marketing_consent,
            updated_at=customer.updated_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[UserRow.paddle_customer_id],
            set_={
                "email": stmt.excluded.email,
                "name": stmt.excluded.name,
                "marketing": stmt.excluded.marketing,
                "updated_at": stmt.excluded.updated_at,
            },
            # Equal timestamps re-apply the same version (idempotent redelivery)
            where=or_(UserRow.updated_at.is_(None), UserRow.updated_at <= stmt.excluded.updated_at),
        )
        # Wraps the whole session: the violation can surface at commit, not only at execute
        try:
            with self.session() as s:
                s.execute(stmt)
        except IntegrityError as e:
            if isinstance(e.orig, pg_errors.UniqueViolation) and e.orig.diag.constraint_name == EMAIL_CONSTRAINT:
                # from None: the chained psycopg error holds the email (DETAIL: Key (email)=...),
                # and this error is logged on every retry
                raise EmailConflictError(customer.id) from None
            raise

    def upsert_order(self, order: Transaction) -> int:
        """
        Insert the Order and its OrderItems for a completed Paddle transaction.
        Idempotent: a retry returns the existing order and does not duplicate items.

        Args:
            order (Transaction): Completed Paddle transaction

        Raises:
            MissingDependencyError: The customer or one of the prices isn't synced yet

        Returns:
            int: Order.id
        """
        line_items = order.details.line_items
        price_ids = {item.price_id for item in line_items}

        with self.session() as s:
            user_id = s.scalar(select(UserRow.id).where(UserRow.paddle_customer_id == order.customer_id))
            if user_id is None:
                raise MissingDependencyError("User", order.customer_id, "transaction", order.id)

            license_ids = dict(
                s.execute(
                    select(ProductLicenseRow.paddle_price_id, ProductLicenseRow.id).where(
                        ProductLicenseRow.paddle_price_id.in_(price_ids)
                    )
                ).all()
            )
            missing = price_ids - license_ids.keys()
            if missing:
                raise MissingDependencyError("ProductLicense", missing, "transaction", order.id)

            # DO UPDATE (not DO NOTHING) so RETURNING gives the id on a retry too
            order_stmt = insert(OrderRow).values(
                user_id=user_id,
                paddle_order_id=order.id,
                total=order.details.totals.grand_total,
                currency_code=order.details.totals.currency_code,
            )
            order_stmt = order_stmt.on_conflict_do_update(
                index_elements=[OrderRow.paddle_order_id],
                set_={
                    "total": order_stmt.excluded.total,
                    "currency_code": order_stmt.excluded.currency_code,
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

            return order_id
