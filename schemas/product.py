"""
product.created / product.updated / product.imported -> Product

Docs: https://developer.paddle.com/webhooks/products/product-updated
"""
from typing import Annotated

from pydantic import StringConstraints

from schemas.base import PaddleModel, PaddleWebhook, ProductId, Status

# Reverse-DNS bundle ID sent by the plugin, e.g. "com.fcpxstudio.myplugin"
BundleId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$", max_length=255),
]


class ProductCustomData(PaddleModel):
    """
    custom_data set on the product in the Paddle dashboard: {"bundle_id": "com.fcpxstudio.myplugin"}
    """

    bundle_id: BundleId


class Product(PaddleModel):
    id: ProductId  # -> Product.paddle_product_id
    name: str
    status: Status  # no column: archived products are logged and still updated
    custom_data: ProductCustomData  # required: a product without a bundle ID can't be saved


ProductWebhook = PaddleWebhook[Product]
