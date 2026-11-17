"""
Download storage (Cloudflare R2, public bucket): PaddleSync only reads the size of a product's file.

A file is at DOWNLOAD_STORAGE_BASE_URL + "/" + download_path (ProductVersion.download_path).
"""
import httpx

from config import settings
from logger import logger

# A product webhook waits for this, keep it short: a missing size is not an error
TIMEOUT_SECONDS = 5.0


def download_url(path: str) -> str:
    return f"{settings.DOWNLOAD_STORAGE_BASE_URL.rstrip('/')}/{path}"


async def fetch_download_size(path: str, transport: httpx.AsyncBaseTransport | None = None) -> int | None:
    """
    Size of a file in the download storage, from the Content-Length of a HEAD request.
    Never raises: the product is synced anyway, without a size (ProductVersion.download_size NULL).

    Args:
        path (str): download_path, relative to DOWNLOAD_STORAGE_BASE_URL
        transport (httpx.AsyncBaseTransport | None): For tests (httpx.MockTransport)

    Returns:
        int | None: Size in bytes, None if the file is missing, the storage unreachable, or no
            Content-Length was sent
    """
    url = download_url(path)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=True, transport=transport) as client:
            response = await client.head(url)
        response.raise_for_status()
        size = int(response.headers["content-length"])
        if size < 0:
            raise ValueError(f"negative Content-Length {size}")
        return size
    except (httpx.HTTPError, KeyError, ValueError) as e:
        logger.warning("No download size for %s: %s", url, str(e) or type(e).__name__)
        return None
