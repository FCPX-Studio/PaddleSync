from db import Database
from db.models import ProductLicenseRow, ProductRow

from psycopg import errors as pg_errors
from sqlalchemy import cast, literal, or_, select, update
from sqlalchemy.dialects.postgresql import INTERVAL, insert
from sqlalchemy.exc import IntegrityError

from schemas.exceptions import BundleIdConflictError, MissingDependencyError
from schemas.product import Product
from schemas.price import Price

# Named by the db naming convention (uq_%(table_name)s_%(column_0_N_name)s)
BUNDLE_ID_CONSTRAINT = "uq_Product_bundle_id"


class ProductRepository(Database):
    """
    Writes the product catalog from Paddle: Product and ProductLicense
    """

    def upsert_product(self, product: Product) -> bool:
        """
        Insert or update the Product matching a Paddle product.
        version_id and trial_duration are left untouched on update.
        Stale webhooks are skipped: the row is only updated if the product's `updated_at` is not older
        than the stored one (Paddle does not guarantee delivery order, and redelivers old events).

        Args:
            product (Product): Paddle product

        Raises:
            BundleIdConflictError: Another product already uses this bundle ID (permanent, not retried)

        Returns:
            bool: False if the webhook was stale and nothing was written
        """
        stmt = insert(ProductRow).values(
            paddle_product_id=product.id,
            bundle_id=product.custom_data.bundle_id,
            archived=product.status == "archived",
            updated_at=product.updated_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[ProductRow.paddle_product_id],
            set_={
                "bundle_id": stmt.excluded.bundle_id,
                "archived": stmt.excluded.archived,
                "updated_at": stmt.excluded.updated_at,
            },
            # Equal timestamps re-apply the same version (idempotent redelivery)
            where=or_(ProductRow.updated_at.is_(None), ProductRow.updated_at <= stmt.excluded.updated_at),
        ).returning(ProductRow.id)
        # Wraps the whole session: the violation can surface at commit, not only at execute
        try:
            with self.session() as s:
                return s.execute(stmt).scalar_one_or_none() is not None
        except IntegrityError as e:
            if isinstance(e.orig, pg_errors.UniqueViolation) and e.orig.diag.constraint_name == BUNDLE_ID_CONSTRAINT:
                raise BundleIdConflictError(product.id, product.custom_data.bundle_id) from e
            raise

    def upsert_license(self, license: Price) -> bool:
        """
        Insert or update the ProductLicense matching a Paddle price.
        For a trial price, also sets the trial length of its Product.
        activation_seats is left untouched on update.
        Stale webhooks are skipped (see upsert_product), the Product trial length included.

        Args:
            license (Price): Paddle price

        Raises:
            MissingDependencyError: The price's product isn't synced yet

        Returns:
            bool: False if the webhook was stale and nothing was written
        """
        # Postgres interval, e.g. '1 month': months and years have no fixed length in days
        lifetime = license.lifetime
        life_duration = cast(literal(f"{lifetime.frequency} {lifetime.interval}"), INTERVAL) if lifetime else None

        with self.session() as s:
            product_id = s.scalar(select(ProductRow.id).where(ProductRow.paddle_product_id == license.product_id))
            if product_id is None:
                raise MissingDependencyError("Product", license.product_id, "price", license.id)

            stmt = insert(ProductLicenseRow).values(
                product_id=product_id,
                paddle_price_id=license.id,
                is_trial=license.is_trial,
                life_duration=life_duration,
                price=license.unit_price.amount,
                currency_code=license.unit_price.currency_code,
                archived=license.status == "archived",
                updated_at=license.updated_at,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[ProductLicenseRow.paddle_price_id],
                set_={
                    "is_trial": stmt.excluded.is_trial,
                    "life_duration": stmt.excluded.life_duration,
                    "price": stmt.excluded.price,
                    "currency_code": stmt.excluded.currency_code,
                    "archived": stmt.excluded.archived,
                    "updated_at": stmt.excluded.updated_at,
                },
                where=or_(
                    ProductLicenseRow.updated_at.is_(None), ProductLicenseRow.updated_at <= stmt.excluded.updated_at
                ),
            ).returning(ProductLicenseRow.id)
            if s.execute(stmt).scalar_one_or_none() is None:
                return False  # stale: a newer version of this price is already stored

            if license.is_trial:
                s.execute(update(ProductRow).where(ProductRow.id == product_id).values(trial_duration=life_duration))
            return True
