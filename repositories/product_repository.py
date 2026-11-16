from db import Database
from db.models import ProductLicenseRow, ProductRow

from sqlalchemy import cast, literal, select, update
from sqlalchemy.dialects.postgresql import INTERVAL, insert

from repositories.errors import MissingDependency
from schemas.product import Product
from schemas.price import Price


class ProductRepository(Database):
    """
    Writes the product catalog from Paddle: Product and ProductLicense
    """

    def upsert_product(self, product: Product) -> None:
        """
        Insert or update the Product matching a Paddle product.
        version_id and trial_duration are left untouched on update.

        Args:
            product (Product): Paddle product
        """
        stmt = insert(ProductRow).values(
            paddle_product_id=product.id,
            bundle_id=product.custom_data.bundle_id,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[ProductRow.paddle_product_id],
            set_={"bundle_id": stmt.excluded.bundle_id},
        )
        with self.session() as s:
            s.execute(stmt)

    def upsert_license(self, license: Price) -> None:
        """
        Insert or update the ProductLicense matching a Paddle price.
        For a trial price, also sets the trial length of its Product.
        activation_seats is left untouched on update.

        Args:
            license (Price): Paddle price

        Raises:
            MissingDependency: The price's product isn't synced yet
        """
        # Postgres interval, e.g. '1 month': months and years have no fixed length in days
        lifetime = license.lifetime
        life_duration = cast(literal(f"{lifetime.frequency} {lifetime.interval}"), INTERVAL) if lifetime else None

        with self.session() as s:
            product_id = s.scalar(select(ProductRow.id).where(ProductRow.paddle_product_id == license.product_id))
            if product_id is None:
                raise MissingDependency(f"Product {license.product_id} not found for price {license.id}")

            stmt = insert(ProductLicenseRow).values(
                product_id=product_id,
                paddle_price_id=license.id,
                is_trial=license.is_trial,
                life_duration=life_duration,
                price=license.unit_price.amount,
                currency_code=license.unit_price.currency_code,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[ProductLicenseRow.paddle_price_id],
                set_={
                    "is_trial": stmt.excluded.is_trial,
                    "life_duration": stmt.excluded.life_duration,
                    "price": stmt.excluded.price,
                    "currency_code": stmt.excluded.currency_code,
                },
            )
            s.execute(stmt)

            if license.is_trial:
                s.execute(update(ProductRow).where(ProductRow.id == product_id).values(trial_duration=life_duration))
