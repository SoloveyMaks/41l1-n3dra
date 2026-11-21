#!/usr/bin/env python3
"""Scoped host relay: UNIX socket -> one loopback HTTP proxy upstream.

The nedra app container cannot reach the host's ``127.0.0.1:14600`` proxy. This
service exposes *only that one upstream* on a UNIX socket that root mounts into
the app container. It is not a general proxy: there is no per-connection
target selection and the upstream must be a loopback address.

Usage:
    nedra-telegram-proxy.py --socket /run/user/1000/nedra/telegram-proxy.sock \
        --upstream 127.0.0.1:14600

The socket is created 0660 in a 0700 directory; the container joins the owner's
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
from typing import Optional, Tuple

CHUNK = 65536
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def log(message: str) -> None:
    print(f"[nedra-telegram-proxy] {message}", file=sys.stderr, flush=True)


def parse_upstream(value: str) -> Tuple[str, int]:
    host, sep, port_text = value.rpartition(":")
    if not sep or not host or not port_text.isdigit():
        raise argparse.ArgumentTypeError("upstream must be HOST:PORT")
    if host.strip("[]") not in LOOPBACK_HOSTS:
        raise argparse.ArgumentTypeError("upstream must be a loopback address")
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("upstream port out of range")
    return host.strip("[]"), port


def prepare_socket(path: Path, mode: int) -> socket.socket:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
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


def build_handler(upstream_host: str, upstream_port: int, active: set):
    async def handle(
        unix_reader: asyncio.StreamReader,
        unix_writer: asyncio.StreamWriter,
    ) -> None:
        active.add(unix_writer)
        try:
            tcp_reader, tcp_writer = await asyncio.open_connection(
                upstream_host, upstream_port,
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
        build_handler(*args.upstream, active), sock=server_socket,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    log(f"listening {path} -> {args.upstream[0]}:{args.upstream[1]}")
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
        "--upstream", required=True, type=parse_upstream,
        help="loopback HTTP proxy HOST:PORT (default deployment: 127.0.0.1:14600)",
    )
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