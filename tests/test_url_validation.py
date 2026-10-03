import asyncio
from dataclasses import FrozenInstanceError
import ipaddress
import socket
from unittest.mock import AsyncMock, Mock

import pytest

from backend.security.url_validation import (
    BlockedURL, DNSResolutionError, InvalidURL, resolve_and_validate_url,
    resolve_hostname, validate_redirect_target, validate_resolved_addresses,
    validate_subrequest_url, validate_url,
)


PUBLIC = ("93.184.216.34", "2606:4700:4700::1111")


@pytest.mark.asyncio
@pytest.mark.parametrize("host", [
    "localhost", "LOCALHOST", "localhost.", "foo.localhost", "foo.localhost.",
    "something.local", "something.local.", "PRINTER.LOCAL.", "local",
    "127.0.0.1", "10.0.0.1", "172.16.0.1", "172.31.255.255", "192.168.1.1",
    "169.254.169.254", "0.0.0.0", "100.64.0.1", "224.0.0.1", "240.0.0.1",
    "192.0.2.1", "[::1]", "[fe80::1]", "[fc00::1]", "[::]", "[ff02::1]",
    "[2001:db8::1]", "[::ffff:127.0.0.1]", "[::ffff:192.168.1.1]",
    "localhost\u3002", "printer\uff0elocal\uff0e",
])
async def test_unsafe_hosts_blocked_before_dns(host):
    resolver = AsyncMock(return_value=PUBLIC)
    with pytest.raises(BlockedURL):
        await resolve_and_validate_url(f"https://{host}/", resolver=resolver)
    resolver.assert_not_awaited()


@pytest.mark.parametrize("raw", [
    "https:///path", "https:", "", "https://", "https://public.example:abc/",
    "https://public.example:65536/", "https://public.example:0/", "https://public.example:/",
    "https://user:pass@public.example/", "https://localhost@public.example/",
    "https://public.example@127.0.0.1/", "https://@public.example/",
    "https://127.1/", "https://2130706433/", "https://0x7f000001/", "https://0177.0.0.1/",
    "https://public.123/", "https://public.example../", "https://%6cocalhost/",
    "https://[fe80::1%25eth0]/", "https://public.example\\@localhost/",
    "https://public.example\n/", " https://public.example/", "https://public.example/a b",
])
def test_invalid_and_ambiguous_urls(raw):
    with pytest.raises(InvalidURL):
        validate_url(raw)


@pytest.mark.parametrize("raw", ["file:///etc/passwd", "ftp://public.example", "gopher://public.example",
                                "data:text/plain,hello", "javascript:alert(1)", "ws://public.example",
                                "wss://public.example", "about:blank", "chrome://settings"])
def test_disallowed_schemes(raw):
    with pytest.raises(InvalidURL) as error:
        validate_url(raw)
    assert error.value.code == "URL_SCHEME_NOT_ALLOWED"


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme", ["http", "https"])
async def test_normalization_and_public_dns(scheme):
    resolver = AsyncMock(return_value=[PUBLIC[1], PUBLIC[0], PUBLIC[0]])
    result = await resolve_and_validate_url(f"{scheme.upper()}://PUBLIC.Example.:8443/a%2Fb?token=secret#section", resolver=resolver)
    assert result.url == f"{scheme}://public.example:8443/a%2Fb?token=secret"
    assert result.hostname == "public.example"
    assert result.port == 8443
    assert result.resolved_ips == PUBLIC
    resolver.assert_awaited_once_with("public.example", 8443)
    with pytest.raises(FrozenInstanceError):
        result.url = "changed"


@pytest.mark.asyncio
async def test_idna_host_normalization():
    resolver = AsyncMock(return_value=PUBLIC)
    result = await resolve_and_validate_url("https://ПРИМЕР.РФ./путь?q=1", resolver=resolver)
    assert result.hostname == "xn--e1afmkfd.xn--p1ai"
    assert result.url == "https://xn--e1afmkfd.xn--p1ai/путь?q=1"


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["127.0.0.1.example.com", "example.com.evil", "localhost.example.com"])
async def test_no_substring_host_matching(host):
    resolver = AsyncMock(return_value=PUBLIC)
    assert (await resolve_and_validate_url(f"https://{host}/", resolver=resolver)).hostname == host


@pytest.mark.asyncio
@pytest.mark.parametrize("host,canonical", [
    ("93.184.216.34", "93.184.216.34"),
    ("[2001:4860:4860:0:0:0:0:8888]", "2001:4860:4860::8888"),
    ("[::ffff:93.184.216.34]", "::ffff:93.184.216.34"),
])
async def test_public_literals_do_not_resolve(host, canonical):
    resolver = AsyncMock(side_effect=AssertionError("No DNS for literal"))
    underlying = ipaddress.ip_address(canonical)
    assert (getattr(underlying, "ipv4_mapped", None) or underlying).is_global
    result = await resolve_and_validate_url(f"https://{host}/", resolver=resolver)
    assert result.resolved_ips == (canonical,)
    resolver.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("answers", [
    ["10.0.0.1"], ["127.0.0.1"], ["169.254.169.254"], ["fe80::1"],
    [PUBLIC[0], "10.0.0.1"], [PUBLIC[0], "169.254.169.254"],
    [PUBLIC[0], "fe80::1"], [PUBLIC[1], "192.168.1.1"],
    [PUBLIC[0], "::ffff:127.0.0.1"], ["10.0.0.1", PUBLIC[0]],
])
async def test_all_dns_answers_must_be_public(answers):
    with pytest.raises(BlockedURL):
        await resolve_and_validate_url("https://attacker.example/", resolver=AsyncMock(return_value=answers))


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["empty", "invalid_ip", "os_error", "timeout"])
async def test_dns_failures_controlled(failure):
    resolver = AsyncMock(return_value=[] if failure == "empty" else ["not-an-ip"])
    if failure == "os_error":
        resolver.side_effect = socket.gaierror("Internal DNS topology must not leak")
    if failure == "timeout":
        async def stalled(*args):
            await asyncio.Event().wait()
        resolver.side_effect = stalled
    with pytest.raises(DNSResolutionError) as error:
        await resolve_and_validate_url("https://public.example/?token=secret", resolver=resolver, dns_timeout_seconds=0.01)
    assert error.value.code == "URL_DNS_RESOLUTION_FAILED"
    assert "secret" not in str(error.value) and "topology" not in str(error.value)


def test_dns_address_normalization():
    assert validate_resolved_addresses([PUBLIC[1].upper(), PUBLIC[0], PUBLIC[0]]) == PUBLIC


@pytest.mark.asyncio
async def test_os_resolver_adapter_without_real_dns(monkeypatch):
    boundary = Mock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC[0], 443)),
                                  (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (PUBLIC[1], 443, 0, 0))])
    monkeypatch.setattr(socket, "getaddrinfo", boundary)
    result = await resolve_and_validate_url("https://public.example/")
    assert result.resolved_ips == PUBLIC
    boundary.assert_called_once_with("public.example", 443, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", [validate_redirect_target, validate_subrequest_url])
@pytest.mark.parametrize("target", ["http://127.0.0.1/private.png", "https://localhost/admin",
                                  "https://printer.local/script.js", "https://mixed.example/frame"])
async def test_redirect_and_every_subrequest_same_policy(consumer, target):
    resolver = AsyncMock(return_value=[PUBLIC[0], "fe80::1"])
    with pytest.raises(BlockedURL):
        await consumer(target, resolver=resolver)


@pytest.mark.asyncio
async def test_redirect_sequence_and_relative_targets():
    resolver = AsyncMock(return_value=PUBLIC)
    first = await resolve_and_validate_url("https://public.example/start", resolver=resolver)
    second = await validate_redirect_target("../next?q=1#fragment", base_url=first.url, resolver=resolver)
    assert second.url == "https://public.example/next?q=1"
    third = await validate_redirect_target("https://other.example/", base_url=second.url, resolver=resolver)
    assert third.hostname == "other.example"
    resolver.return_value = ["10.0.0.1"]
    with pytest.raises(BlockedURL):
        await validate_redirect_target("//private.internal/admin", base_url=third.url, resolver=resolver)
    with pytest.raises(InvalidURL):
        await validate_redirect_target("\nhttps://public.example", base_url=third.url, resolver=resolver)


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["document", "script.js", "stylesheet.css", "image.png", "font.woff", "xhr", "iframe"])
async def test_public_subrequests_pass(resource):
    assert (await validate_subrequest_url(f"https://public.example/{resource}", resolver=AsyncMock(return_value=PUBLIC))).resolved_ips == PUBLIC


@pytest.mark.asyncio
async def test_new_dns_answer_checked_again_for_same_target():
    resolver = AsyncMock(side_effect=[PUBLIC, ["127.0.0.1"]])
    await resolve_and_validate_url("https://public.example/", resolver=resolver)
    with pytest.raises(BlockedURL):
        await validate_subrequest_url("https://public.example/", resolver=resolver)
    assert resolver.await_count == 2


def test_startup_health_has_no_dns_or_safety_side_effects(app, monkeypatch):
    from fastapi.testclient import TestClient
    dns = Mock(side_effect=AssertionError("Unexpected DNS"))
    monkeypatch.setattr(socket, "getaddrinfo", dns)
    with TestClient(app) as client:
        assert client.get("/api/v2/health").status_code == 200
        paths = client.get("/openapi.json").json()["paths"]
        assert not any("/sources/url" in path or path.endswith("/refresh") for path in paths)
    dns.assert_not_called()
