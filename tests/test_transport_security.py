import asyncio

import pytest
from mcp.server.transport_security import TransportSecurityMiddleware
from starlette.requests import Request

from dot_mcp.transport_security import transport_security


def response(host, origin=None, public="fixture.tunnl.gg"):
    headers = [(b"content-type", b"application/json")]
    if host is not None:
        headers.append((b"host", host.encode()))
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    request = Request({"type": "http", "method": "POST", "path": "/mcp/", "headers": headers})
    middleware = TransportSecurityMiddleware(transport_security(public))
    return asyncio.run(middleware.validate_request(request, is_post=True))


@pytest.mark.parametrize("host", ["localhost", "localhost:33041", "127.0.0.1",
                                 "127.0.0.1:33041", "[::1]", "[::1]:33041",
                                 "fixture.tunnl.gg", "fixture.tunnl.gg:443"])
def test_exact_loopback_and_public_hosts(host):
    assert response(host) is None


@pytest.mark.parametrize("host", [None, "evil.test", "other.tunnl.gg", "fixture.tunnl.gg.evil.test",
                                 "fixture.tunnl.gg:444", "localhost.evil.test", "stale.tunnl.gg"])
def test_unlisted_hosts_rejected(host):
    assert response(host).status_code == 421


@pytest.mark.parametrize("origin", ["https://fixture.tunnl.gg", "https://fixture.tunnl.gg:443",
                                   "http://localhost", "http://127.0.0.1", "http://[::1]"])
def test_equivalent_origins(origin):
    assert response("fixture.tunnl.gg", origin) is None


@pytest.mark.parametrize("origin", ["https://evil.test", "https://fixture.tunnl.gg:444",
                                   "http://fixture.tunnl.gg", "null"])
def test_unlisted_origins_rejected(origin):
    assert response("fixture.tunnl.gg", origin).status_code == 403


def test_public_configuration_normalized_and_keeps_nondefault_port_exact():
    config = " https://FIXTURE.tunnl.gg/mcp/, custom.test:8443 "
    assert response("fixture.tunnl.gg:443", public=config) is None
    assert response("custom.test:8443", "https://custom.test:8443", config) is None
    assert response("custom.test", public=config).status_code == 421
    assert response("custom.test:443", public=config).status_code == 421
    assert transport_security(config).enable_dns_rebinding_protection is True


@pytest.mark.parametrize("value", ["https://user:password@host.test", "http://host.test",
                                  "https://host.test:invalid", "https:///missing"])
def test_invalid_public_configuration_fails_closed(value):
    with pytest.raises(ValueError):
        transport_security(value)
