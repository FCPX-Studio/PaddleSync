"""
product.created / product.updated / product.imported -> Product

Docs: https://developer.paddle.com/webhooks/products/product-updated
"""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import StringConstraints, field_validator

from schemas.base import PaddleModel, PaddleWebhook, ProductId, Status

# Reverse-DNS bundle ID sent by the plugin, e.g. "com.fcpxstudio.myplugin"
BundleId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$", max_length=255),
]


# Current version label, e.g. "1.2.0" (ProductVersion.label is 32 characters)
VersionLabel = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]

# File in the download storage, relative to DOWNLOAD_STORAGE_BASE_URL, e.g. "trim-assist/TrimAssist-1.2.0.pkg".
# Slash-separated segments of letters, digits, ".", "_" and "-": no leading "/", no empty segment, and no
# "." or ".." segment (checked below), so it can't point outside the storage. ProductVersion.download_path is 512.
DownloadPath = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$", max_length=512),
]

# Product.name is 255 characters
ProductName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class ProductCustomData(PaddleModel):
    """
    custom_data set on the product in the Paddle dashboard, every key required:
    {"bundle_id": "com.fcpxstudio.myplugin", "version": "1.2.0", "type": "plugin",
     "download": "myplugin/MyPlugin-1.2.0.pkg"}
    """

    # -> Product.bundle_id: the FxPlug bundle ID, sent by the plugin on activation (must match it exactly)
    bundle_id: BundleId
    # -> Product.version_id: the ProductVersion with this label (created the first time it is synced)
    version: VersionLabel
    # -> Product.type. Not Paddle's own product `type` ("standard" / "custom"), which is ignored
    type: Literal["plugin", "asset"]
    # -> ProductVersion.download_path of `version`. Upload the file before saving the product in Paddle:
    # PaddleSync reads its size when the webhook arrives
    download: DownloadPath

    @field_validator("download")
    @classmethod
    def no_dot_segments(cls, value: str) -> str:
        if any(segment in (".", "..") for segment in value.split("/")):
            raise ValueError("download must not contain '.' or '..' segments")
        return value


class Product(PaddleModel):
    id: ProductId  # -> Product.paddle_product_id
    name: ProductName  # -> Product.name
    status: Status  # -> Product.archived
    custom_data: ProductCustomData  # required: a product without all its custom_data keys can't be saved
    updated_at: datetime  # -> Product.updated_at: older webhooks are skipped (out-of-order delivery)


ProductWebhook = PaddleWebhook[Product]
