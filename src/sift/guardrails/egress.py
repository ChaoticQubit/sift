"""Fetcher egress policy — split by trust (AGENTS.md §3a; decided 2026-07-22).

The interpreter agent has NO tools, so agent-context egress is only the model API call.
This module governs the deterministic link fetcher (extract/links.py calls it at F3):
credential-less, any public URL, SSRF-guarded and TOCTOU-safe.

What is pinned (both http and https): DNS is resolved ONCE per hop, EVERY resolved
address is validated public, and the TCP connection is opened to that validated IP
literal — urllib3 never re-resolves. For https, SNI and certificate verification run
against the original hostname (urllib3 `server_hostname` + `assert_hostname`), so
pinning does not weaken TLS. What is NOT pinned: nothing in this path; gallery-dl /
yt-dlp manage their own connections under their instagram.com scope (enforced at F2).

Redirects are never auto-followed: each hop's Location is re-resolved and re-validated
(hop count capped). No cookies or Authorization are ever sent; URLs carrying userinfo
credentials are rejected outright.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import certifi
import urllib3

_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}

# IPv6 prefixes whose last 32 bits embed the REAL IPv4 destination: the NAT64
# well-known prefix (RFC 6052 — a local DNS64/NAT64 gateway translates these to the
# embedded IPv4, so 64:ff9b::a9fe:a9fe reaches 169.254.169.254) and the deprecated
# IPv4-compatible ::a.b.c.d range. Python's is_global is True for both wrappers, so
# they must be decoded before the public check, like ipv4_mapped already is.
_EMBEDDED_V4_PREFIXES = (
    ipaddress.ip_network("64:ff9b::/96"),  # NAT64 well-known prefix
    ipaddress.ip_network("::/96"),  # deprecated IPv4-compatible ::a.b.c.d
)


class EgressError(Exception):
    """A fetch was refused by the SSRF egress policy."""


# The account-scoped fetchers (gallery-dl / yt-dlp) may touch ONLY Instagram and its
# CDNs (AGENTS.md §3a — enforced in code at F2, not assumed from tool defaults).
_INSTAGRAM_HOSTS = ("instagram.com", "cdninstagram.com", "fbcdn.net")


def is_instagram_host(host: str) -> bool:
    """Subdomain-aware suffix match on dot boundaries, never a substring check:
    scontent.cdninstagram.com passes; evil-instagram.com.attacker.net does not."""
    host = host.lower().rstrip(".")
    return any(host == h or host.endswith("." + h) for h in _INSTAGRAM_HOSTS)


def assert_instagram_permalink(url: str) -> str:
    """Every future yt-dlp invocation MUST route its URL through this gate (AGENTS.md
    §3a); cookie_fetch uses the same host test on every recorded/downloaded URL.
    Returns the url unchanged or raises EgressError."""
    split = urlsplit(url)
    if split.scheme not in ("http", "https"):
        raise EgressError(f"non-http(s) URL refused for account-scoped fetcher: {url}")
    if not split.hostname or not is_instagram_host(split.hostname):
        raise EgressError(
            f"non-Instagram URL refused for account-scoped fetcher: {url}"
        )
    return url


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address):
        # ::ffff:10.0.0.1, 64:ff9b::7f00:1, ::127.0.0.1: judge the embedded IPv4
        # destination, not the IPv6 wrapper.
        if addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        elif any(addr in net for net in _EMBEDDED_V4_PREFIXES):
            addr = ipaddress.IPv4Address(int(addr) & 0xFFFF_FFFF)
    # is_global is False for private, loopback, link-local, unspecified (0.0.0.0/::),
    # unique-local fc00::/7, reserved, and shared ranges.
    return addr.is_global and not addr.is_multicast


def _resolve(host: str, port: int) -> list[str]:
    """Resolve ONCE; return every address. Numeric hosts (incl. encoded IPv4 forms like
    0x7f000001, which getaddrinfo canonicalizes) come back as plain IP strings."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise EgressError(f"cannot resolve {host!r}: {exc}") from exc
    addrs = [info[4][0] for info in infos]
    if not addrs:
        raise EgressError(f"no addresses for {host!r}")
    return addrs


def _validate(url: str) -> tuple[str, str, int, str]:
    """Validate one URL: scheme, no credentials, every resolved address public.

    Returns (scheme, host, port, pinned_ip) or raises EgressError.
    """
    split = urlsplit(url)
    if split.scheme not in ("http", "https"):
        raise EgressError(f"scheme {split.scheme!r} refused: {url}")
    if split.username or split.password:
        raise EgressError(f"credentials in URL refused: {url}")
    host = split.hostname
    if not host:
        raise EgressError(f"no host in URL: {url}")
    port = split.port or (443 if split.scheme == "https" else 80)
    addrs = _resolve(host, port)
    for ip in addrs:
        if not _is_public(ip):
            raise EgressError(f"non-public address {ip} for {host!r} refused: {url}")
    return split.scheme, host, port, addrs[0]


def is_public_url(url: str) -> bool:
    """True iff the URL is http(s), credential-free, and resolves ONLY to public IPs."""
    try:
        _validate(url)
    except EgressError:
        return False
    return True


def _request(
    scheme: str, ip: str, port: int, host: str, target: str, timeout: float
) -> urllib3.BaseHTTPResponse:
    """One pinned, credential-less GET. Seam for offline tests."""
    kwargs: dict = {
        "timeout": urllib3.Timeout(connect=timeout, read=timeout),
        "retries": False,
    }
    if scheme == "https":
        pool: urllib3.HTTPConnectionPool = urllib3.HTTPSConnectionPool(
            ip,
            port,
            cert_reqs="CERT_REQUIRED",
            ca_certs=certifi.where(),
            server_hostname=host,  # SNI + cert verification against the real hostname
            assert_hostname=host,
            **kwargs,
        )
    else:
        pool = urllib3.HTTPConnectionPool(ip, port, **kwargs)
    default_port = 443 if scheme == "https" else 80
    host_header = host if port == default_port else f"{host}:{port}"
    try:
        # No cookies, no Authorization — ever. Redirects handled by the caller per hop.
        return pool.urlopen(
            "GET",
            target,
            headers={"Host": host_header, "User-Agent": "sift/0.1"},
            redirect=False,
        )
    finally:
        pool.close()


def guarded_fetch(
    url: str, *, timeout: float = 15.0, max_redirects: int = _MAX_REDIRECTS
) -> urllib3.BaseHTTPResponse:
    """SSRF-safe credential-less GET. Response body is untrusted input (AGENTS.md §3a).

    Every hop: resolve once -> validate every address public -> connect to that IP.
    Raises EgressError on any policy violation.
    """
    for _ in range(max_redirects + 1):
        scheme, host, port, ip = _validate(url)
        split = urlsplit(url)
        target = (split.path or "/") + (f"?{split.query}" if split.query else "")
        response = _request(scheme, ip, port, host, target, timeout)
        if response.status in _REDIRECT_STATUSES:
            location = response.headers.get("Location")
            if not location:
                raise EgressError(f"redirect without Location from {url}")
            url = urljoin(url, location)  # re-validated on the next loop pass
            continue
        return response
    raise EgressError(f"too many redirects (> {max_redirects}) for {url}")
