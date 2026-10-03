"""URL/DNS policy for initial targets, redirects and every network subrequest.

Validation is not DNS pinning: consumers must enforce it before each request and
control actual egress. No browser, HTTP, persistence or startup networking here.

Decisions where the spec is silent: reject userinfo, scoped IPs and ambiguous
numeric hosts; use stdlib IDNA and lowercase, remove one trailing host dot and
URL fragments, preserve path/query, allow ports 1..65535. Classify mapped IPv6
by underlying IPv4, require global unicast addresses and validate every answer.
Async resolver injection returns address strings. DNS awaits have a 5s default
deadline; cancelling a to_thread await cannot stop an in-flight OS lookup.
Stage 6b must also bound concurrency and enforce actual destination/egress.
"""

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit


class InvalidURL(ValueError):
    code = "INVALID_URL"

    def __init__(self, code: str | None = None):
        if code is not None:
            self.code = code
        # Never include raw URLs, credentials, queries, DNS answers or topology.
        super().__init__("Этот адрес нельзя анализировать.")


class BlockedURL(InvalidURL):
    code = "URL_BLOCKED_PRIVATE_NETWORK"


class DNSResolutionError(InvalidURL):
    code = "URL_DNS_RESOLUTION_FAILED"


@dataclass(frozen=True)
class ParsedURL:
    """Syntax-normalized only; a hostname still requires DNS approval."""

    url: str
    scheme: str
    hostname: str
    port: int | None


@dataclass(frozen=True)
class ValidatedURL(ParsedURL):
    resolved_ips: tuple[str, ...]


Resolver = Callable[[str, int], Awaitable[Iterable[str]]]


def _check_raw(value: str) -> None:
    if (not isinstance(value, str) or not value
            or any(ord(char) <= 32 or ord(char) == 127 or char == "\\" for char in value)):
        raise InvalidURL()


def _public_ip(value: str):
    try:
        if not isinstance(value, str) or "%" in value:
            raise ValueError("Scoped or invalid IP")
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise DNSResolutionError() from exc
    underlying = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
    classified = underlying or address
    if (not classified.is_global or classified.is_private or classified.is_loopback
            or classified.is_link_local or classified.is_multicast
            or classified.is_unspecified or classified.is_reserved):
        raise BlockedURL()
    return address


def validate_resolved_addresses(addresses: Iterable[str]) -> tuple[str, ...]:
    """Every A/AAAA answer must be public; no 'first safe answer' fallback."""
    approved = {_public_ip(value) for value in addresses}
    if not approved:
        raise DNSResolutionError()
    return tuple(str(ip) for ip in sorted(approved, key=lambda ip: (ip.version, int(ip))))


def validate_url(raw_url: str) -> ParsedURL:
    """Parse and normalize without DNS. Not a hostname navigation approval."""
    _check_raw(raw_url)
    try:
        parts = urlsplit(raw_url)
        scheme, host, port = parts.scheme.lower(), parts.hostname, parts.port
    except ValueError as exc:
        raise InvalidURL() from exc
    if scheme not in {"http", "https"}:
        raise InvalidURL("URL_SCHEME_NOT_ALLOWED")
    if "@" in parts.netloc:
        raise InvalidURL("URL_CREDENTIALS_NOT_ALLOWED")
    if not host or port == 0 or parts.netloc.endswith(":"):
        raise InvalidURL()
    try:
        host = host.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise InvalidURL() from exc
    host = host.removesuffix(".")
    if not host or "%" in host:
        raise InvalidURL()
    if host == "localhost" or host.endswith(".localhost") or host == "local" or host.endswith(".local"):
        raise BlockedURL("URL_BLOCKED_LOCAL_HOST")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        labels = host.split(".")
        if (len(host) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
                or re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", labels[-1])):
            # Browser/OS numeric hosts can mean shortened, octal, hex or integer IPs.
            raise InvalidURL()
    else:
        _public_ip(str(literal))
        host = str(literal)
    authority = f"[{host}]" if ":" in host else host
    if port is not None:
        authority += f":{port}"
    url = urlunsplit((scheme, authority, parts.path, parts.query, ""))
    return ParsedURL(url, scheme, host, port)


async def resolve_hostname(hostname: str, port: int) -> tuple[str, ...]:
    """OS A/AAAA resolver boundary, off the event loop; no work on import."""
    answers = await asyncio.to_thread(socket.getaddrinfo, hostname, port,
                                      family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
    return tuple(answer[4][0] for answer in answers)


async def resolve_and_validate_url(raw_url: str, *, resolver: Resolver | None = None,
                                   dns_timeout_seconds: float = 5.0) -> ValidatedURL:
    target = validate_url(raw_url)
    try:
        literal = ipaddress.ip_address(target.hostname)
    except ValueError:
        if dns_timeout_seconds <= 0:
            raise ValueError("DNS timeout must be positive")
        try:
            async with asyncio.timeout(dns_timeout_seconds):
                addresses = await (resolver or resolve_hostname)(
                    target.hostname, target.port or (443 if target.scheme == "https" else 80))
        except (OSError, TimeoutError) as exc:
            raise DNSResolutionError() from exc
        ips = validate_resolved_addresses(addresses)
    else:
        ips = validate_resolved_addresses([str(literal)])
    return ValidatedURL(target.url, target.scheme, target.hostname, target.port, ips)


async def validate_redirect_target(target_url: str, *, base_url: str | None = None,
                                   resolver: Resolver | None = None) -> ValidatedURL:
    _check_raw(target_url)
    if base_url is not None:
        target_url = urljoin(validate_url(base_url).url, target_url)
    return await resolve_and_validate_url(target_url, resolver=resolver)


async def validate_subrequest_url(target_url: str, *, resolver: Resolver | None = None) -> ValidatedURL:
    return await resolve_and_validate_url(target_url, resolver=resolver)
