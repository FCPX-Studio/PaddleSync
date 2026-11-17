from datetime import timedelta

from db import Database
from db.models import ProductLicenseRow, ProductRow, ProductVersionRow

from psycopg import errors as pg_errors
from sqlalchemy import case, func, or_, select, update
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

    def upsert_product(self, product: Product, download_size: int | None) -> bool:
        """
        Insert or update the Product matching a Paddle product (name, custom_data bundle_id and type).
        Its custom_data `version` becomes the current version (version_id): the ProductVersion with this
        label is created, or updated with the custom_data `download` (same version, new file: no new version).
        Stale webhooks are skipped: the row is only updated if the product's `updated_at` is not older
        than the stored one (Paddle does not guarantee delivery order, and redelivers old events),
        the version included.

        Args:
            product (Product): Paddle product
            download_size (int | None): Size in bytes of the custom_data `download` file, None if unknown

        Raises:
            BundleIdConflictError: Another product already uses this bundle ID (permanent, not retried)

        Returns:
            bool: False if the webhook was stale and nothing was written
        """
        stmt = insert(ProductRow).values(
            paddle_product_id=product.id,
            bundle_id=product.custom_data.bundle_id,
            name=product.name,
            type=product.custom_data.type,
            archived=product.status == "archived",
            updated_at=product.updated_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[ProductRow.paddle_product_id],
            set_={
                "bundle_id": stmt.excluded.bundle_id,
                "name": stmt.excluded.name,
                "type": stmt.excluded.type,
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
                self._set_version(
                    s, product_id, product.custom_data.version, product.custom_data.download, download_size
                )
                return True
        except IntegrityError as e:
            if isinstance(e.orig, pg_errors.UniqueViolation) and e.orig.diag.constraint_name == BUNDLE_ID_CONSTRAINT:
                raise BundleIdConflictError(product.id, product.custom_data.bundle_id) from e
            raise

    @staticmethod
    def _set_version(
        s: Session, product_id: int, label: str, download_path: str, download_size: int | None
    ) -> None:
        """
        Point the product to its version `label`, created if missing, else given this download.
        Runs inside the caller's transaction. ON CONFLICT DO UPDATE on UNIQUE (product_id, label): safe against
        redeliveries and concurrent events.

        download_size: an unknown size (None) keeps the stored one while the file is the same (the HEAD
        request failed this time), and clears it when the file changed (the old size is another file's).
        """
        stmt = insert(ProductVersionRow).values(
            product_id=product_id, label=label, download_path=download_path, download_size=download_size
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[ProductVersionRow.product_id, ProductVersionRow.label],
            set_={
                "download_path": stmt.excluded.download_path,
                "download_size": case(
                    (
                        ProductVersionRow.download_path == stmt.excluded.download_path,
                        func.coalesce(stmt.excluded.download_size, ProductVersionRow.download_size),
                    ),
                    else_=stmt.excluded.download_size,
                ),
            },
        ).returning(ProductVersionRow.id)
        version_id = s.execute(stmt).scalar_one()
        s.execute(update(ProductRow).where(ProductRow.id == product_id).values(version_id=version_id))

    def upsert_license(self, license: Price) -> bool:
        """
        Insert or update the ProductLicense matching a Paddle price (name, description, unit price).
        Terms from the price custom_data: a trial price is stored as a trial license (is_trial,
        life_duration = trial length, AuthAPI reads its product's trial offer from it), a paid price with
        a duration as an expiring license, any other price as a perpetual license (life_duration NULL).
        activation_seats comes from Paddle too (1 for a trial, custom_data `seats` else, default 2): a manual
        DB edit is overwritten by the next price event. Licenses already issued keep their own seats.
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
                name=license.name,
                description=license.description,
                is_trial=license.is_trial,
                life_duration=life_duration,
                activation_seats=license.activation_seats,
                price=license.unit_price.amount,
                currency_code=license.unit_price.currency_code,
                archived=license.status == "archived",
                updated_at=license.updated_at,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[ProductLicenseRow.paddle_price_id],
                set_={
                    "name": stmt.excluded.name,
                    "description": stmt.excluded.description,
                    "is_trial": stmt.excluded.is_trial,
                    "life_duration": stmt.excluded.life_duration,
                    "activation_seats": stmt.excluded.activation_seats,
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
