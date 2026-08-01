"""Async client for Samsung appliances fitted with a DONGLE_ Wi-Fi module.

These are the pre-RT-OCF appliances (roughly 2016-2018) that expose a local
REST API on TCP 8888. They are *not* the generation LocalThings talks to --
those speak CoAP over DTLS on UDP 49152-49160 and have 8888 closed.

Three properties of this API force a hand-rolled client instead of aiohttp:

1. The appliance negotiates **TLS 1.0 only**. TLS 1.1 and 1.2 are refused
   outright, so a modern default SSLContext cannot connect at all.

2. It demands a **client certificate** -- Samsung's AC14K_M intermediate CA,
   which has been public for years. Without one nginx answers
   ``400 No required SSL certificate was sent`` before any routing happens.

3. Its traffic carries a **malformed header**: ``X-API-Version : v1.0.0``,
   with a space before the colon. Python's email-based parsers -- behind
   ``http.client`` and ``http.server`` alike -- read that as a missing
   header/body separator, discard *every* header after it (``Content-Length``
   included) and move the remainder into the message payload. On the request
   side that is fatal: with no Content-Length the handler never reads the body,
   so the pairing token vanishes while the appliance answers perfectly.
   Hence the deliberately tolerant parser below.

Authentication is two independent gates. The certificate opens the channel but
identifies nobody -- it ships in every SmartThings install. The real credential
is a per-appliance **bearer token**, issued only after someone physically
presses a button on the machine. The token is not bound to a source IP, so a
token obtained from one host works from any other.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import socket
import ssl
from typing import Any

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 8888
CALLBACK_PORT = 8889

# A pairing request stays pending for ~63s and then expires. Exactly one POST
# per window: a second POST while one is pending returns 403 and tells you
# nothing useful.
PAIR_WINDOW = 63.0
PAIR_TIMEOUT = 180.0

_TOKEN_RE = re.compile(r'"DeviceToken"\s*:\s*"([^"]+)"')


class DongleError(Exception):
    """Any failure talking to the appliance."""


class DongleAuthError(DongleError):
    """The token was rejected, or no token was supplied."""


class DonglePairingError(DongleError):
    """Pairing did not complete."""


def build_client_ssl_context(certfile: str, keyfile: str | None = None) -> ssl.SSLContext:
    """Build the client context. Blocking -- call from an executor."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    # TLSv1 is deprecated and its enum may vanish in future Pythons; the
    # appliance offers nothing newer, so failing to lower this is fatal.
    with contextlib.suppress(ValueError, AttributeError):
        ctx.minimum_version = ssl.TLSVersion.TLSv1
    with contextlib.suppress(ssl.SSLError):
        ctx.set_ciphers("ALL:@SECLEVEL=0")
    ctx.load_cert_chain(certfile, keyfile)
    return ctx


def build_server_ssl_context(certfile: str, keyfile: str | None = None) -> ssl.SSLContext:
    """Context for the pairing callback listener. Blocking -- use an executor.

    The appliance connects back over TLS, so the listener needs a server
    certificate. The AC14K_M bundle serves fine -- the appliance does not
    validate it.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    with contextlib.suppress(ValueError, AttributeError):
        ctx.minimum_version = ssl.TLSVersion.TLSv1
    with contextlib.suppress(ssl.SSLError):
        ctx.set_ciphers("ALL:@SECLEVEL=0")
    ctx.load_cert_chain(certfile, keyfile)
    return ctx


def local_ip_towards(host: str, port: int = DEFAULT_PORT) -> str:
    """Which of our addresses would reach ``host``.

    The appliance calls back to whatever we put in the Host header, so on a
    multi-VLAN host we must advertise the interface that can actually reach it.
    No packets are sent -- connect() on UDP only sets the route.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((host, port))
        return str(sock.getsockname()[0])
    finally:
        sock.close()


def _parse_http(raw: bytes) -> tuple[int, dict[str, str], str]:
    """Split an HTTP response without a strict parser.

    Deliberately tolerant: header names are stripped, so ``X-API-Version :``
    parses like any other. Only the first blank line separates head from body,
    and the body is returned verbatim.
    """
    text = raw.decode("utf-8", "replace")
    head, _, body = text.partition("\r\n\r\n")
    lines = head.split("\r\n")
    if not lines or not lines[0]:
        raise DongleError("empty response from appliance")
    parts = lines[0].split(" ")
    if len(parts) < 2 or not parts[1].isdigit():
        raise DongleError(f"malformed status line: {lines[0]!r}")
    status = int(parts[1])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if sep:
            headers[name.strip().lower()] = value.strip()
    return status, headers, body


class DongleClient:
    """One appliance."""

    def __init__(
        self,
        host: str,
        ssl_context: ssl.SSLContext,
        token: str | None = None,
        port: int = DEFAULT_PORT,
    ) -> None:
        self._host = host
        self._port = port
        self._ssl = ssl_context
        self._token = token

    @property
    def host(self) -> str:
        return self._host

    async def _request(
        self,
        method: str,
        path: str,
        *,
        host_header: str | None = None,
        with_auth: bool = True,
        body: str | None = None,
        timeout: float = 15.0,
    ) -> tuple[int, dict[str, str], str]:
        req = f"{method} {path} HTTP/1.1\r\n"
        # For pairing this is deliberately *not* the appliance -- it tells the
        # appliance where to deliver the token.
        req += f"Host: {host_header or f'{self._host}:{self._port}'}\r\n"
        req += "Content-Type: application/json\r\n"
        if with_auth and self._token:
            req += f"Authorization: Bearer {self._token}\r\n"
        req += f"Content-Length: {len(body) if body else 0}\r\n"
        req += "Connection: close\r\n\r\n"

        try:
            async with asyncio.timeout(timeout):
                reader, writer = await asyncio.open_connection(
                    self._host, self._port, ssl=self._ssl
                )
                try:
                    writer.write(req.encode())
                    if body:
                        writer.write(body.encode())
                    await writer.drain()
                    raw = await reader.read()
                finally:
                    writer.close()
                    with contextlib.suppress(Exception):
                        await writer.wait_closed()
        except (TimeoutError, asyncio.TimeoutError) as err:
            raise DongleError(f"timeout talking to {self._host}") from err
        except (OSError, ssl.SSLError) as err:
            raise DongleError(f"connection to {self._host} failed: {err}") from err

        status, headers, resp_body = _parse_http(raw)
        if status == 401:
            raise DongleAuthError("appliance rejected the token")
        return status, headers, resp_body

    async def async_get_device(self, index: int = 0) -> dict[str, Any]:
        """Full state for one appliance."""
        status, _, body = await self._request("GET", f"/devices/{index}")
        if status != 200:
            raise DongleError(f"GET /devices/{index} returned {status}")
        try:
            return json.loads(body)["Device"]
        except (ValueError, KeyError) as err:
            raise DongleError(f"unparseable device payload: {body[:200]!r}") from err

    async def async_get_information(self, index: int = 0) -> dict[str, Any]:
        status, _, body = await self._request("GET", f"/devices/{index}/information")
        if status != 200:
            raise DongleError(f"information returned {status}")
        try:
            return json.loads(body)["Information"]
        except (ValueError, KeyError) as err:
            raise DongleError(f"unparseable information payload: {body[:200]!r}") from err

    async def async_probe(self) -> bool:
        """True if this host speaks the dongle API at all.

        Unauthenticated: a bare request returns 401 when the endpoint exists,
        which is enough to distinguish an appliance from anything else without
        needing a token yet.
        """
        try:
            status, _, _ = await self._request("GET", "/devices", with_auth=False)
        except DongleAuthError:
            return True
        except DongleError:
            return False
        return status in (200, 401, 403)

    async def async_request_token(self, callback: str) -> int:
        """Open a pairing window. ``callback`` is ``ip:port`` for the callback.

        200 -- window opened. 403 -- one is already pending; wait it out.
        """
        status, _, _ = await self._request(
            "POST",
            "/devicetoken/request",
            host_header=callback,
            with_auth=False,
            body="",
        )
        return status


async def async_pair(
    host: str,
    client_ctx: ssl.SSLContext,
    server_ctx: ssl.SSLContext,
    callback_ip: str,
    callback_port: int = CALLBACK_PORT,
    timeout: float = PAIR_TIMEOUT,
    port: int = DEFAULT_PORT,
) -> str:
    """Run the pairing handshake and return the device token.

    Requires someone to physically confirm on the appliance while a window is
    open. That is the only real access control -- the certificate is public,
    so physical presence is what actually authorises the client.

    On washers, **Child Lock must be off**: Samsung disables Smart Control
    entirely while it is engaged, and the panel still beeps on every press, so
    a locked machine looks exactly like a working one that is ignoring you.
    """
    loop = asyncio.get_running_loop()
    token_future: asyncio.Future[str] = loop.create_future()

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # Read raw bytes. Do NOT hand this to an HTTP parser: the callback
        # carries the same malformed "X-API-Version :" header, and a strict
        # parser drops the body containing the token.
        try:
            async with asyncio.timeout(10):
                data = await reader.read(8192)
        except (TimeoutError, asyncio.TimeoutError, OSError):
            data = b""
        match = _TOKEN_RE.search(data.decode("utf-8", "replace"))
        if match and not token_future.done():
            token_future.set_result(match.group(1))
        with contextlib.suppress(Exception):
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()

    try:
        server = await asyncio.start_server(
            _handle, "0.0.0.0", callback_port, ssl=server_ctx
        )
    except OSError as err:
        raise DonglePairingError(
            f"cannot listen on {callback_port} for the pairing callback: {err}"
        ) from err

    client = DongleClient(host, client_ctx, port=port)
    callback = f"{callback_ip}:{callback_port}"
    deadline = loop.time() + timeout

    try:
        while not token_future.done() and loop.time() < deadline:
            with contextlib.suppress(DongleError):
                status = await client.async_request_token(callback)
                _LOGGER.debug("pairing request to %s -> %s", host, status)
            # Wait out this window before posting again; posting into a pending
            # window just yields 403.
            remaining = min(PAIR_WINDOW, max(0.0, deadline - loop.time()))
            with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
                return await asyncio.wait_for(
                    asyncio.shield(token_future), timeout=remaining
                )
        if token_future.done():
            return token_future.result()
        raise DonglePairingError(
            "no token received -- the confirmation button was not pressed while a "
            "window was open, or Child Lock is engaged"
        )
    finally:
        server.close()
        with contextlib.suppress(Exception):
            await server.wait_closed()
