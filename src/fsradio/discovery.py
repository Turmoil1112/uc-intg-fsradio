from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

_LOG = logging.getLogger("discover")

SSDP_ADDR = ("239.255.255.250", 1900)
SSDP_ST = "ssdp:all"
DISCOVERY_PAYLOAD = "\r\n".join(
    [
        "M-SEARCH * HTTP/1.1",
        f"HOST: {SSDP_ADDR[0]}:{SSDP_ADDR[1]}",
        'MAN: "ssdp:discover"',
        "MX: 2",
        f"ST: {SSDP_ST}",
        "",
        "",
    ]
).encode("ascii")


@dataclass(slots=True)
class DiscoveredRadio:
    address: str
    location: str
    usn: str
    server: str
    st: str
    name: str = ""

    @property
    def base_url(self) -> str:
        parsed = urlparse(self.location)
        host = parsed.hostname or self.address
        port = parsed.port or 80
        path = parsed.path.rstrip("/")
        if path.endswith("/device"):
            return f"http://{host}:{port}{path}"
        return f"http://{host}:{port}/device"


def _parse_ssdp_headers(payload: bytes) -> dict[str, str]:
    text = payload.decode("utf-8", errors="ignore")
    lines = text.split("\r\n")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return headers


def _looks_like_frontier(headers: dict[str, str]) -> bool:
    haystack = " ".join(
        [
            headers.get("server", ""),
            headers.get("st", ""),
            headers.get("usn", ""),
            headers.get("location", ""),
        ]
    ).lower()
    return any(token in haystack for token in ("frontier", "fsapi", "radio", "reciva", "undok"))


async def discover_radios(timeout: float = 3.0) -> list[DiscoveredRadio]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _discover_sync, timeout)


def _discover_sync(timeout: float) -> list[DiscoveredRadio]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.settimeout(timeout)
    sock.sendto(DISCOVERY_PAYLOAD, SSDP_ADDR)

    found: dict[str, DiscoveredRadio] = {}
    try:
        while True:
            data, addr = sock.recvfrom(65535)
            headers = _parse_ssdp_headers(data)
            if not _looks_like_frontier(headers):
                continue
            location = headers.get("location", "")
            usn = headers.get("usn", addr[0])
            item = DiscoveredRadio(
                address=addr[0],
                location=location,
                usn=usn,
                server=headers.get("server", ""),
                st=headers.get("st", ""),
            )
            found[item.address] = item
    except socket.timeout:
        pass
    finally:
        sock.close()

    _LOG.debug("Discovered %s Frontier Silicon radios", len(found))
    return list(found.values())
