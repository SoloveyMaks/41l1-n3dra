#!/usr/bin/env python3
"""Scoped host relay: UNIX socket -> fixed loopback HTTP proxy upstream.

The nedra app container cannot reach the host's ``127.0.0.1:14600`` proxy. This
service exposes *only that one upstream* on a UNIX socket that root mounts as a
directory into the app container.

Scope, precisely: the upstream address and port are compiled in; there is no
CLI to point the relay at another host port. It still speaks the ordinary HTTP
proxy protocol, so a client that can open the socket can issue CONNECT for any
target the upstream proxy allows. Target filtering is intentionally not
claimed here; the boundary is the single upstream plus UNIX socket permissions.

Usage:
    nedra-telegram-proxy.py --socket /run/user/1000/nedra/telegram-proxy/proxy.sock

The socket is created 0660 in a 0750 directory; the container joins the owner's
host group with ``--group-add keep-groups``.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import signal
import socket
import stat
import sys
from pathlib import Path
from typing import Optional

CHUNK = 65536
UPSTREAM_HOST = "127.0.0.1"
UPSTREAM_PORT = 14600


def log(message: str) -> None:
    print(f"[nedra-telegram-proxy] {message}", file=sys.stderr, flush=True)


def prepare_socket(path: Path, mode: int) -> socket.socket:
    # The container mounts this directory, never the socket inode: the relay
    # unlinks and recreates the socket on every restart, and a file bind mount
    # would keep pointing at the dead inode.
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o750)
    with contextlib.suppress(FileNotFoundError):
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode):
            raise SystemExit(f"{path} exists and is not a socket")
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(1.0)
            probe.connect(str(path))
        except OSError:
            path.unlink()
        else:
            raise SystemExit(f"{path} is already served by another process")
        finally:
            probe.close()

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    os.umask(0o077)
    server.bind(str(path))
    os.chmod(path, mode)
    server.listen(64)
    server.setblocking(False)
    return server


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(CHUNK)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, OSError, asyncio.CancelledError):
        pass
    finally:
        if writer.can_write_eof():
            with contextlib.suppress(Exception):
                writer.write_eof()


def build_handler(active: set):
    async def handle(
        unix_reader: asyncio.StreamReader,
        unix_writer: asyncio.StreamWriter,
    ) -> None:
        active.add(unix_writer)
        try:
            tcp_reader, tcp_writer = await asyncio.open_connection(
                UPSTREAM_HOST, UPSTREAM_PORT,
            )
        except OSError:
            log("upstream proxy is unavailable")
            unix_writer.close()
            with contextlib.suppress(Exception):
                await unix_writer.wait_closed()
            active.discard(unix_writer)
            return
        try:
            await asyncio.gather(
                pipe(unix_reader, tcp_writer),
                pipe(tcp_reader, unix_writer),
            )
        finally:
            for stream in (tcp_writer, unix_writer):
                stream.close()
                with contextlib.suppress(Exception):
                    await stream.wait_closed()
            active.discard(unix_writer)

    return handle


async def serve(args: argparse.Namespace) -> int:
    path = Path(args.socket)
    server_socket = prepare_socket(path, args.mode)
    active: set = set()
    server = await asyncio.start_unix_server(
        build_handler(active), sock=server_socket,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    log(f"listening {path} -> {UPSTREAM_HOST}:{UPSTREAM_PORT}")
    try:
        await stop.wait()
    finally:
        server.close()
        for writer in list(active):
            writer.close()
        with contextlib.suppress(Exception):
            await server.wait_closed()
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        log("stopped")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True, help="UNIX socket path")
    parser.add_argument(
        "--mode", type=lambda value: int(value, 8), default=0o660,
        help="socket mode in octal (default 660)",
    )
    args = parser.parse_args(argv)
    try:
        return asyncio.run(serve(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())