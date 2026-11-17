import asyncio

import httpx
import pytest

from storage import download_url, fetch_download_size


def size_with(handler) -> int | None:
    return asyncio.run(fetch_download_size("myplugin/MyPlugin-1.0.0.pkg", transport=httpx.MockTransport(handler)))


def test_download_url_joins_without_double_slash():
    assert download_url("myplugin/x.pkg") == "https://downloads.test/myplugin/x.pkg"


def test_size_from_content_length():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "HEAD"
        assert str(request.url) == "https://downloads.test/myplugin/MyPlugin-1.0.0.pkg"
        return httpx.Response(200, headers={"content-length": "123456789"})

    assert size_with(handler) == 123456789


def test_follows_redirects():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "downloads.test":
            return httpx.Response(302, headers={"location": "https://cdn.test/x.pkg"})
        return httpx.Response(200, headers={"content-length": "42"})

    assert size_with(handler) == 42


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404),
        httpx.Response(500, headers={"content-length": "10"}),
        httpx.Response(200),  # no Content-Length (httpx sets none on an empty HEAD response here)
        httpx.Response(200, headers={"content-length": "abc"}),
        httpx.Response(200, headers={"content-length": "-1"}),
    ],
)
def test_unknown_size_is_none(response):
    assert size_with(lambda request: response) is None


def test_network_error_is_none():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    assert size_with(handler) is None
