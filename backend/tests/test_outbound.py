"""The outbound target policy (phase-12-plan C4; ``lattice.outbound``).

Shape rules are unit-tested; the connect-time rules against real sockets: a local HTTP
server stands in for an internal service, and resolvers that change their answer stand
in for DNS rebinding. A URL parser test alone would not prove the connection is checked.
"""

import asyncio
import ipaddress
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpcore
import pytest

from lattice.outbound import (
    GuardedBackend,
    IpAddress,
    OutboundPolicy,
    OutboundRefused,
    Target,
    classify,
    fetch,
    outbound_client,
    parse_target,
)


def _ips(*values: str) -> list[IpAddress]:
    return [ipaddress.ip_address(v) for v in values]


@dataclass
class ScriptedResolver:
    """Answers from a script, one answer per call (the last repeats); counts calls."""

    answers: dict[str, list[list[IpAddress]]]
    calls: list[str] = field(default_factory=list)

    async def __call__(self, host: str, port: int) -> list[IpAddress]:
        self.calls.append(host)
        script = self.answers.get(host)
        if not script:
            raise OutboundRefused("target_unreachable")
        return script.pop(0) if len(script) > 1 else script[0]


@dataclass
class LocalServer:
    port: int
    connections: int = 0
    response: bytes = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"


@pytest.fixture
async def server() -> AsyncIterator[LocalServer]:
    state = LocalServer(port=0)

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        state.connections += 1
        await reader.readuntil(b"\r\n\r\n")
        if state.response:
            writer.write(state.response)
            await writer.drain()
        else:
            await asyncio.sleep(5)
        writer.close()

    listener = await asyncio.start_server(handle, "127.0.0.1", 0)
    state.port = listener.sockets[0].getsockname()[1]
    try:
        yield state
    finally:
        listener.close()
        await listener.wait_closed()


# --- shape -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://idp.example.com",  # not https (unregistered)
        "ftp://idp.example.com",
        "file:///etc/passwd",
        "gopher://idp.example.com",
        "https://user:pass@idp.example.com",  # embedded credentials
        "https://user@idp.example.com",
        "https://idp.example.com:8443",  # not 443 (unregistered)
        "https://idp.example.com:0",
        "https://idp.example.com:99999",
        "https://idp.example.com/?q=1",  # query in a base URL
        "https://idp.example.com/#frag",
        "https://idp.example.com/ x",
        "https://idp.example.com/\n",
        "https://",
        "https://-bad-.example.com",
        "https://127.1",  # non-canonical IPv4 forms
        "https://0x7f.0.0.1",
        "https://2130706433",
        "https://0177.0.0.1",
        "https://017700000001",
        "https://" + "a" * 2001,
        "",
    ],
)
def test_shape_refusals(url: str) -> None:
    with pytest.raises(OutboundRefused) as refused:
        OutboundPolicy().check_url(url)
    assert refused.value.code == "target_invalid"
    assert url not in str(refused.value) or not url


def test_targets_are_normalised() -> None:
    assert parse_target("https://IdP.Example.COM./realms/x") == Target(
        "https", "idp.example.com", 443
    )
    assert parse_target("https://[2606:4700::1111]:443") == Target("https", "2606:4700::1111", 443)
    assert parse_target("https://bücher.example").host == "xn--bcher-kva.example"
    # A discovery endpoint may carry a query; never a fragment or userinfo.
    assert parse_target("https://idp.example.com/token?x=1", base=False).port == 443
    with pytest.raises(OutboundRefused):
        parse_target("https://idp.example.com/token#x", base=False)


# --- address classes ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("8.8.8.8", "public"),
        ("93.184.216.34", "public"),
        ("2606:4700::1111", "public"),
        ("127.0.0.1", "private"),
        ("127.255.255.254", "private"),
        ("10.1.2.3", "private"),
        ("172.16.0.1", "private"),
        ("172.31.255.255", "private"),
        ("192.168.1.1", "private"),
        ("100.64.0.1", "private"),  # CGNAT
        ("::1", "private"),
        ("fd00::1", "private"),  # unique local
        ("fc00::1", "private"),
        ("169.254.169.254", "refused"),  # cloud metadata, link-local
        ("169.254.0.1", "refused"),
        ("fe80::1", "refused"),
        ("fe80::1%1", "refused"),
        ("0.0.0.0", "refused"),  # noqa: S104 - an address under test, not a bind
        ("::", "refused"),
        ("224.0.0.1", "refused"),
        ("ff02::1", "refused"),
        ("255.255.255.255", "refused"),
        ("240.0.0.1", "refused"),
        ("198.18.0.1", "refused"),  # benchmarking
        ("192.0.2.1", "refused"),  # documentation
        ("2001:db8::1", "refused"),
        ("::ffff:127.0.0.1", "refused"),  # IPv4-mapped
        ("::ffff:8.8.8.8", "refused"),  # mapped, even when public
        ("::127.0.0.1", "refused"),  # IPv4-compatible
        ("64:ff9b::7f00:1", "refused"),  # NAT64 of 127.0.0.1
        ("2002:7f00:1::1", "refused"),  # 6to4 of 127.0.0.1
        ("2001:0:4136:e378:8000:63bf:3fff:fdd2", "refused"),  # Teredo
        ("fec0::1", "refused"),
    ],
)
def test_address_classes(address: str, expected: str) -> None:
    assert classify(ipaddress.ip_address(address)) == expected


# --- resolution (no connection) ------------------------------------------------------------


async def test_public_hostname_resolving_private_is_refused() -> None:
    for answer in (["10.0.0.5"], ["169.254.169.254"], ["::1"], ["93.184.216.34", "127.0.0.1"]):
        policy = OutboundPolicy(resolver=ScriptedResolver({"idp.example.com": [_ips(*answer)]}))
        with pytest.raises(OutboundRefused) as refused:
            await policy.check_target("https://idp.example.com")
        assert refused.value.code == "target_not_public", answer
    public = OutboundPolicy(resolver=ScriptedResolver({"idp.example.com": [_ips("8.8.8.8")]}))
    assert (await public.check_target("https://idp.example.com")).host == "idp.example.com"


@pytest.mark.parametrize(
    "url", ["https://127.0.0.1", "https://[::1]", "https://10.0.0.1", "https://169.254.169.254"]
)
async def test_ip_literals_are_judged_like_names(url: str) -> None:
    with pytest.raises(OutboundRefused, match="target_not_public"):
        await OutboundPolicy(resolver=ScriptedResolver({})).check_target(url)


async def test_unresolvable_host_is_unreachable() -> None:
    with pytest.raises(OutboundRefused, match="target_unreachable"):
        await OutboundPolicy(resolver=ScriptedResolver({})).check_target("https://nx.example.com")


async def test_registered_private_target_is_exact() -> None:
    registered = Target("https", "idp.internal", 9443)
    resolver = ScriptedResolver({"idp.internal": [_ips("10.0.0.7")]})
    policy = OutboundPolicy(frozenset({registered}), resolver=resolver)
    assert await policy.check_target("https://idp.internal:9443") == registered
    for other in (
        "https://idp.internal",  # another port
        "https://idp.internal:9444",
        "http://idp.internal:9443",  # another scheme
        "https://other.internal:9443",  # another host
    ):
        with pytest.raises(OutboundRefused):
            await policy.check_target(other)
    # Registration never admits link-local (metadata) or reserved addresses.
    metadata = OutboundPolicy(
        frozenset({registered}),
        resolver=ScriptedResolver({"idp.internal": [_ips("169.254.169.254")]}),
    )
    with pytest.raises(OutboundRefused, match="target_not_public"):
        await metadata.check_target("https://idp.internal:9443")


async def test_discovery_endpoints_share_the_issuer_class() -> None:
    resolver = ScriptedResolver(
        {
            "idp.example.com": [_ips("8.8.8.8")],
            "keys.example.com": [_ips("10.0.0.1")],
            "idp.internal": [_ips("10.0.0.7")],
        }
    )
    internal = Target("https", "idp.internal", 443)
    policy = OutboundPolicy(frozenset({internal}), resolver=resolver)
    public_issuer = "https://idp.example.com"
    # A public issuer cannot point Lattice at a private address, even a registered one.
    for endpoint in ("https://keys.example.com/jwks", "https://idp.internal/token"):
        with pytest.raises(OutboundRefused, match="target_not_public"):
            await policy.check_discovery_endpoints(public_issuer, [endpoint])
    with pytest.raises(OutboundRefused, match="target_invalid"):
        await policy.check_discovery_endpoints(public_issuer, ["http://idp.example.com/token"])
    with pytest.raises(OutboundRefused, match="target_invalid"):
        await policy.check_discovery_endpoints(public_issuer, [None])
    # A registered issuer may use its own registered endpoints.
    found = await policy.check_discovery_endpoints(
        "https://idp.internal", ["https://idp.internal/token"]
    )
    assert found == [internal]


# --- connect time --------------------------------------------------------------------------


async def test_registered_loopback_target_connects(server: LocalServer) -> None:
    target = Target("http", "localhost", server.port)
    policy = OutboundPolicy(frozenset({target}))
    response = await fetch(policy, "GET", f"http://localhost:{server.port}/x")
    assert (response.status_code, response.body) == (200, b"ok")
    assert server.connections == 1


async def test_unregistered_private_address_is_never_connected(server: LocalServer) -> None:
    # The name resolves to the local server; the policy refuses before any connection.
    resolver = ScriptedResolver({"internal.example.com": [_ips("127.0.0.1")]})
    with pytest.raises(OutboundRefused, match="target_not_public"):
        await fetch(OutboundPolicy(resolver=resolver), "GET", "https://internal.example.com/")
    assert server.connections == 0


async def test_dns_rebinding_between_check_and_connect_is_refused(server: LocalServer) -> None:
    """The name is public when checked and private when connected: the connect-time
    check (a second resolution, inside the network backend) refuses it."""
    resolver = ScriptedResolver({"rebind.example.com": [_ips("93.184.216.34"), _ips("127.0.0.1")]})
    policy = OutboundPolicy(resolver=resolver)
    await policy.check_target("https://rebind.example.com")  # passes: public
    with pytest.raises(OutboundRefused, match="target_not_public"):
        await fetch(policy, "GET", "https://rebind.example.com/")
    assert resolver.calls == ["rebind.example.com", "rebind.example.com"]
    assert server.connections == 0


async def test_registered_target_rebinding_to_metadata_is_refused(server: LocalServer) -> None:
    target = Target("http", "svc.internal", server.port)
    resolver = ScriptedResolver({"svc.internal": [_ips("169.254.169.254")]})
    policy = OutboundPolicy(frozenset({target}), resolver=resolver)
    with pytest.raises(OutboundRefused, match="target_not_public"):
        await fetch(policy, "GET", f"http://svc.internal:{server.port}/")
    assert server.connections == 0


async def test_the_backend_connects_only_to_its_own_target() -> None:
    backend = GuardedBackend(OutboundPolicy(), Target("https", "idp.example.com", 443))
    with pytest.raises(httpcore.ConnectError, match="target_invalid"):
        await backend.connect_tcp("other.example.com", 443)
    with pytest.raises(httpcore.ConnectError, match="target_invalid"):
        await backend.connect_tcp("idp.example.com", 8443)
    with pytest.raises(httpcore.ConnectError, match="target_invalid"):
        await backend.connect_unix_socket("/var/run/docker.sock")


async def test_redirects_are_refused(server: LocalServer) -> None:
    server.response = (
        b"HTTP/1.1 302 Found\r\nLocation: http://169.254.169.254/\r\n"
        b"Content-Length: 0\r\nConnection: close\r\n\r\n"
    )
    policy = OutboundPolicy(frozenset({Target("http", "localhost", server.port)}))
    with pytest.raises(OutboundRefused, match="redirect_refused"):
        await fetch(policy, "GET", f"http://localhost:{server.port}/")
    assert server.connections == 1  # the redirect target was never contacted


async def test_responses_are_capped(server: LocalServer) -> None:
    body = b"x" * 2048
    server.response = b"HTTP/1.1 200 OK\r\nContent-Length: 2048\r\nConnection: close\r\n\r\n" + body
    policy = OutboundPolicy(frozenset({Target("http", "localhost", server.port)}))
    with pytest.raises(OutboundRefused, match="response_too_large"):
        await fetch(policy, "GET", f"http://localhost:{server.port}/", max_bytes=1024)


async def test_slow_targets_time_out(server: LocalServer) -> None:
    server.response = b""
    policy = OutboundPolicy(frozenset({Target("http", "localhost", server.port)}))
    with pytest.raises(OutboundRefused, match="timeout"):
        await fetch(policy, "GET", f"http://localhost:{server.port}/", total_timeout=0.3)


async def test_refusals_never_carry_addresses_or_bodies(server: LocalServer) -> None:
    resolver = ScriptedResolver({"internal.example.com": [_ips("10.9.8.7")]})
    with pytest.raises(OutboundRefused) as refused:
        await fetch(OutboundPolicy(resolver=resolver), "GET", "https://internal.example.com/")
    assert "10.9.8.7" not in str(refused.value)
    assert "internal.example.com" not in str(refused.value)


def test_client_uses_the_guarded_backend_and_ignores_proxies() -> None:
    """Pins the httpx internals ``outbound_client`` relies on (httpx 0.28)."""
    client = outbound_client(OutboundPolicy(), Target("https", "idp.example.com", 443))
    transport = client._transport
    pool = transport._pool  # type: ignore[attr-defined]
    assert isinstance(pool, httpcore.AsyncConnectionPool)
    assert isinstance(pool._network_backend, GuardedBackend)
    assert client.follow_redirects is False
    assert client.trust_env is False
    assert not client._mounts  # no proxy mounts from the environment
