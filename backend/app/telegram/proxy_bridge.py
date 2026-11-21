"""Loopback TCP-to-UNIX-socket bridge for the Telegram HTTP proxy.

The app container cannot reach the host's ``127.0.0.1:14600`` proxy. When a
host relay exposes that proxy on a UNIX socket, this bridge gives httpx a plain
``http://127.0.0.1:<ephemeral>`` proxy endpoint inside the container's own
network namespace and splices every connection to the mounted socket.

The listener is bound to loopback only: nothing outside the container can use
it, and the socket is the only host endpoint it can reach. The wire protocol is
still the HTTP proxy protocol; the bridge does not parse or rewrite it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Optional

logger = logging.getLogger("TelegramBot")

_CHUNK = 65536


class UnixSocketProxyBridge:
    def __init__(self, socket_path: str, host: str = "127.0.0.1", port: int = 0):
        self.socket_path = socket_path
        self.host = host
        self._requested_port = port
        self._port: Optional[int] = None
        self._server: Optional[asyncio.AbstractServer] = None
        self._connections: set[asyncio.Task[None]] = set()

    @property
    def port(self) -> Optional[int]:
        return self._port

    async def start(self) -> int:
        """Bind the loopback listener and return its port."""
        if self._server is not None and self._port is not None:
            return self._port
        try:
            self._server = await asyncio.start_server(
                self._handle, self.host, self._requested_port, backlog=32,
            )
        except OSError:
            raise
        sockets = self._server.sockets or ()
        if not sockets:
            await self.close()
            raise OSError("telegram proxy bridge has no listener")
        self._port = int(sockets[0].getsockname()[1])
        return self._port

    async def close(self) -> None:
        server, self._server = self._server, None
        self._port = None
        if server is not None:
            server.close()
            with contextlib.suppress(Exception):
                await server.wait_closed()
        tasks = list(self._connections)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._connections.clear()

    async def _handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._connections.add(task)
        try:
            try:
                unix_reader, unix_writer = await asyncio.open_unix_connection(
                    self.socket_path
                )
            except OSError:
                logger.debug("telegram proxy socket is unavailable")
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
                return
            try:
                await asyncio.gather(
                    self._pipe(reader, unix_writer),
                    self._pipe(unix_reader, writer),
                )
            finally:
                for stream in (unix_writer, writer):
                    stream.close()
                    with contextlib.suppress(Exception):
                        await stream.wait_closed()
        finally:
            if task is not None:
                self._connections.discard(task)

    @staticmethod
    async def _pipe(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            while True:
                chunk = await reader.read(_CHUNK)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()
        except (ConnectionError, OSError, asyncio.CancelledError):
            pass
        finally:
            # Half-close lets the peer finish a response after our side ends.
            if writer.can_write_eof():
                with contextlib.suppress(Exception):
                    writer.write_eof()