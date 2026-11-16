from datetime import timedelta

from db import Database
from db.models import ProductLicenseRow, ProductRow, ProductVersionRow

from psycopg import errors as pg_errors
from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from schemas.exceptions import BundleIdConflictError, MissingDependencyError
from schemas.product import Product
from schemas.price import Price

# Named by the db naming convention (uq_%(table_name)s_%(column_0_N_name)s)
BUNDLE_ID_CONSTRAINT = "uq_Product_bundle_id"


class ProductRepository(Database):
    """
    Writes the product catalog from Paddle: Product, ProductVersion and ProductLicense
    """

    def upsert_product(self, product: Product) -> bool:
        """
        Insert or update the Product matching a Paddle product.
        Its custom_data `version` becomes the current version (version_id): the ProductVersion with this
        label is found, or created. Without `version`, version_id is left untouched.
        Stale webhooks are skipped: the row is only updated if the product's `updated_at` is not older
        than the stored one (Paddle does not guarantee delivery order, and redelivers old events),
        the version included.

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
                product_id = s.execute(stmt).scalar_one_or_none()
                if product_id is None:
                    return False  # stale: a newer version of this product is already stored
                if product.custom_data.version is not None:
                    self._set_version(s, product_id, product.custom_data.version)
                return True
        except IntegrityError as e:
            if isinstance(e.orig, pg_errors.UniqueViolation) and e.orig.diag.constraint_name == BUNDLE_ID_CONSTRAINT:
                raise BundleIdConflictError(product.id, product.custom_data.bundle_id) from e
            raise

    @staticmethod
    def _set_version(s: Session, product_id: int, label: str) -> None:
        """
        Point the product to its version `label`, created if missing. Runs inside the caller's transaction.
        ON CONFLICT DO NOTHING on UNIQUE (product_id, label): safe against redeliveries and concurrent events
        """
        s.execute(
            insert(ProductVersionRow)
            .values(product_id=product_id, label=label)
            .on_conflict_do_nothing(index_elements=[ProductVersionRow.product_id, ProductVersionRow.label])
        )
        version_id = s.scalar(
            select(ProductVersionRow.id).where(ProductVersionRow.product_id == product_id, ProductVersionRow.label == label)
        )
        s.execute(update(ProductRow).where(ProductRow.id == product_id).values(version_id=version_id))

    def upsert_license(self, license: Price) -> bool:
        """
        Insert or update the ProductLicense matching a Paddle price.
        Terms from the price custom_data: a trial price is stored as a trial license (is_trial,
        life_duration = trial length, AuthAPI reads its product's trial offer from it), a paid price with
        a duration as an expiring license, any other price as a perpetual license (life_duration NULL).
        activation_seats is left untouched on update.
        Stale webhooks are skipped (see upsert_product).

        Args:
            license (Price): Paddle price

        Raises:
            MissingDependencyError: The price's product isn't synced yet

        Returns:
            bool: False if the webhook was stale and nothing was written
        """
        # Postgres interval of N days (psycopg sends a timedelta as an interval)
        life_duration = timedelta(days=license.duration_days) if license.duration_days else None

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
            return s.execute(stmt).scalar_one_or_none() is not None
