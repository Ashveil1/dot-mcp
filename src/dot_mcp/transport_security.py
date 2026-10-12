"""Exact authority allowlists shared by MCP 1 and MCP 2.

The SDK's ``host:*`` pattern requires a colon; it does not match a bare host.
Keep public hosts exact rather than trusting forwarded headers or all tunnels.
"""
from urllib.parse import urlsplit


def transport_security(public_hosts: str):
    from mcp.server.transport_security import TransportSecuritySettings

    loopback = ("127.0.0.1", "localhost", "[::1]")
    hosts = [value for host in loopback for value in (host, host + ":*")]
    origins = [value for host in loopback
               for value in ("http://" + host, "http://" + host + ":*")]
    for value in public_hosts.split(","):
        value = value.strip()
        if not value:
            continue
        parsed = urlsplit(value if "://" in value else "https://" + value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("MCP_PUBLIC_HOST must contain HTTPS hostnames without credentials")
        port = parsed.port  # Reject malformed ports before starting the server.
        host = parsed.hostname.lower()
        if ":" in host:
            host = "[" + host + "]"
        authorities = (host, host + ":443") if port in (None, 443) else (host + ":" + str(port),)
        for authority in authorities:
            if authority not in hosts:
                hosts.append(authority)
            origin = "https://" + authority
            if origin not in origins:
                origins.append(origin)
    return TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                     allowed_hosts=hosts, allowed_origins=origins)
