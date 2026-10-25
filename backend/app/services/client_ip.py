"""Client identity for authentication rate limiting ONLY.

The default ("peer") uses the immediate TCP peer exactly as before. Behind a
proxy that peer is shared, so all clients share one bucket: safe, never
bypassable, but coarse.

Forwarded modes are opt-in and trust headers only when the immediate peer is
inside AUTH_TRUSTED_PROXY_CIDRS. In x-forwarded-for mode every address between
the selected client entry and the peer must itself be a trusted proxy, so a hop
count alone is never treated as proof of who wrote the header. Whenever trust
cannot be established the peer is used, which can only merge buckets, never
create new ones. Nothing here logs or returns addresses.

Do not enable a forwarded mode until the deployment's proxy chain has been
verified; see docs/authentication-sessions.md.
"""

from dataclasses import dataclass
from functools import lru_cache
import ipaddress
import os


MODES = frozenset({"peer", "cf-connecting-ip", "x-forwarded-for"})
MAX_TRUSTED_NETWORKS = 64
MAX_PROXY_HOPS = 5
MAX_FORWARDED_ENTRIES = 32
IPV6_CLIENT_PREFIX = 64


@dataclass(frozen=True)
class ClientIpPolicy:
    mode: str = "peer"
    trusted_proxies: tuple = ()
    proxy_hops: int | None = None


def _setting(name):
    value = os.getenv(name)
    return value.strip() if value is not None and value.strip() else None


def parse_client_ip_policy(mode, cidrs, hops):
    mode = (mode or "peer").strip().lower()
    if mode not in MODES:
        raise ValueError("Invalid AUTH_CLIENT_IP_SOURCE")
    if mode == "peer":
        if cidrs is not None or hops is not None:
            raise ValueError("AUTH_TRUSTED_PROXY_CIDRS and AUTH_TRUSTED_PROXY_HOPS require a forwarded AUTH_CLIENT_IP_SOURCE")
        return ClientIpPolicy()

    if cidrs is None:
        # Never activate header trust without an explicit immediate-proxy allowlist.
        raise ValueError("AUTH_TRUSTED_PROXY_CIDRS is required for forwarded AUTH_CLIENT_IP_SOURCE")
    networks = []
    for item in cidrs.split(","):
        item = item.strip()
        try:
            network = ipaddress.ip_network(item, strict=True)
        except ValueError:
            raise ValueError("Invalid AUTH_TRUSTED_PROXY_CIDRS") from None
        if network.prefixlen == 0:
            raise ValueError("AUTH_TRUSTED_PROXY_CIDRS must not trust every address")
        networks.append(network)
    if not networks or len(networks) > MAX_TRUSTED_NETWORKS:
        raise ValueError("Invalid AUTH_TRUSTED_PROXY_CIDRS")

    if mode == "x-forwarded-for":
        try:
            proxy_hops = int(hops) if hops is not None else None
        except ValueError:
            proxy_hops = None
        if proxy_hops is None or not 1 <= proxy_hops <= MAX_PROXY_HOPS:
            raise ValueError("AUTH_TRUSTED_PROXY_HOPS must be an integer from 1 to 5 for x-forwarded-for")
    else:
        if hops is not None:
            raise ValueError("AUTH_TRUSTED_PROXY_HOPS applies only to x-forwarded-for")
        proxy_hops = None
    return ClientIpPolicy(mode, tuple(networks), proxy_hops)


@lru_cache(maxsize=1)
def client_ip_policy():
    return parse_client_ip_policy(
        _setting("AUTH_CLIENT_IP_SOURCE"),
        _setting("AUTH_TRUSTED_PROXY_CIDRS"),
        _setting("AUTH_TRUSTED_PROXY_HOPS"),
    )


def peer_identity(request):
    # Unchanged legacy behaviour: the ASGI peer, canonicalized, else "unknown".
    peer = request.client.host if request.client else "unknown"
    try:
        return str(ipaddress.ip_address(peer))
    except ValueError:
        return "unknown"


def parse_address(value):
    """One address from a forwarded header: bare, [IPv6]:port or IPv4:port."""
    value = value.strip()
    if not value or len(value) > 64:
        return None
    bracketed = value.startswith("[")
    if bracketed:
        end = value.find("]")
        if end == -1 or (value[end + 1:] and not value[end + 1:].startswith(":")):
            return None
        value = value[1:end]
    elif value.count(":") == 1:
        value = value.split(":", 1)[0]
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    # Brackets are only valid around IPv6; scoped (link-local zone) IDs are not client addresses.
    if (bracketed and address.version != 6) or (address.version == 6 and address.scope_id):
        return None
    return address


def client_bucket(address):
    """IPv4-mapped IPv6 counts as IPv4; other IPv6 clients share their /64."""
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    if address.version == 6:
        network = ipaddress.ip_network((address, IPV6_CLIENT_PREFIX), strict=False)
        return f"{network.network_address}/{IPV6_CLIENT_PREFIX}"
    return str(address)


def _trusted(address, policy):
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    return any(address in network for network in policy.trusted_proxies)


def _header_values(request, name):
    return [value.decode("latin-1") for key, value in request.scope.get("headers", []) if key.lower() == name]


def _cf_connecting_ip(request):
    values = _header_values(request, b"cf-connecting-ip")
    # Duplicate or list-valued headers are ambiguous.
    if len(values) != 1 or "," in values[0]:
        return None
    return parse_address(values[0])


def _x_forwarded_for(request, policy):
    values = _header_values(request, b"x-forwarded-for")
    if not values:
        return None
    # Repeated headers are one list in order (RFC 9110); only the right end is used.
    entries = [entry for value in values for entry in value.split(",")]
    if len(entries) > MAX_FORWARDED_ENTRIES or len(entries) < policy.proxy_hops:
        return None
    selected = entries[len(entries) - policy.proxy_hops:]
    addresses = [parse_address(entry) for entry in selected]
    if any(address is None for address in addresses):
        return None
    client, intermediaries = addresses[0], addresses[1:]
    # Each hop to the right of the client was appended by a proxy and must be one
    # we trust; the client itself must not be a trusted proxy (chain too short).
    if any(not _trusted(address, policy) for address in intermediaries) or _trusted(client, policy):
        return None
    return client


def auth_client_identity(request, policy=None):
    policy = client_ip_policy() if policy is None else policy
    peer = peer_identity(request)
    if policy.mode == "peer" or peer == "unknown":
        return peer
    if not _trusted(ipaddress.ip_address(peer), policy):
        return peer
    if policy.mode == "cf-connecting-ip":
        client = _cf_connecting_ip(request)
    else:
        client = _x_forwarded_for(request, policy)
    return peer if client is None else client_bucket(client)
