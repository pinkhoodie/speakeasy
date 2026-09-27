"""Card images: bounded HTTPS fetch for product cards, and Hermes MEDIA files under allowed roots.

No redirects, private DNS answers, arbitrary ports, credentials, or oversized bodies. Connects to
the vetted IP while preserving HTTPS hostname verification and SNI. Local images are served only
from configured roots (default: the Hermes home), never through a symlink, only real image bytes.
"""
from __future__ import annotations

import http.client
import ipaddress
import os
import socket
import ssl
import stat
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

MAX_IMAGE_BYTES = 1_000_000
MAX_LOCAL_IMAGE_BYTES = 8_000_000
LOCAL_IMAGE_EXTS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".gif": "image/gif", ".webp": "image/webp"}


class ImageRejected(ValueError):
    pass


def vetted_image_url(raw: str) -> tuple[str, str, str]:
    if len(raw) > 1500:
        raise ImageRejected("image URL too long")
    parts = urlsplit(raw)
    host = parts.hostname or ""
    if (parts.scheme != "https" or not host or parts.port not in (None, 443)
            or parts.username or parts.password or parts.fragment
            or host == "localhost" or host.endswith((".local", ".internal", ".test", ".invalid"))):
        raise ImageRejected("image URL rejected")
    try:
        if not ipaddress.ip_address(host).is_global:
            raise ImageRejected("private image host")
    except ValueError as exc:
        if isinstance(exc, ImageRejected):
            raise
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return host, path, raw


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, ip: str):
        super().__init__(host, timeout=5)
        self.pinned_ip = ip
        self.context = ssl.create_default_context()

    def connect(self) -> None:
        sock = socket.create_connection((self.pinned_ip, 443), timeout=self.timeout)
        try:
            self.sock = self.context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


@lru_cache(maxsize=16)
def fetch_image(raw: str) -> tuple[bytes, str]:
    host, path, _ = vetted_image_url(raw)
    answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not answers:
        raise ImageRejected("image host unavailable")
    addresses = {answer[4][0] for answer in answers}
    if any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ImageRejected("image host has a private address")
    connection = PinnedHTTPS(host, str(sorted(addresses)[0]))
    try:
        connection.request("GET", path, headers={"Accept": "image/jpeg,image/png,image/webp,image/gif",
                                                 "User-Agent": "SpeakeasyCards/1"})
        response = connection.getresponse()
        if response.status != 200:
            raise ImageRejected("image not available")  # no redirect following
        mime = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        if mime not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
            raise ImageRejected("not a supported image")
        data = response.read(MAX_IMAGE_BYTES + 1)
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ImageRejected("image size rejected")
        return data, mime
    finally:
        connection.close()


def default_image_roots(hermes_home: Path, extra: list[str] | tuple[str, ...] = ()) -> tuple[Path, ...]:
    """The Hermes home (where Hermes writes MEDIA), plus absolute roots from settings."""
    roots = [Path(hermes_home)]
    for raw in extra or ():
        path = Path(str(raw).strip()).expanduser()
        if str(raw).strip() and path.is_absolute():
            roots.append(path)
    return tuple(roots)


def _sniff(head: bytes) -> str | None:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def vetted_local_image(raw: str, roots: tuple[Path, ...]) -> Path:
    """Absolute path under a resolved allowed root, image extension, final component not a symlink."""
    if not isinstance(raw, str) or len(raw) > 1024 or "\x00" in raw:
        raise ImageRejected("image path rejected")
    path = Path(os.path.expanduser(raw))
    if not path.is_absolute() or ".." in path.parts:
        raise ImageRejected("image path rejected")
    if path.suffix.lower() not in LOCAL_IMAGE_EXTS:
        raise ImageRejected("not a supported image")
    try:
        if path.is_symlink():
            raise ImageRejected("symlinked image rejected")
        parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise ImageRejected("image unavailable") from exc
    for root in roots:
        try:
            base = Path(root).expanduser().resolve(strict=True)
        except OSError:
            continue
        if parent == base or base in parent.parents:
            return parent / path.name
    raise ImageRejected("image outside allowed directories")


def read_local_image(raw: str, roots: tuple[Path, ...]) -> tuple[bytes, str]:
    """Open without following symlinks, require a regular file within the size cap, sniff the type."""
    path = vetted_local_image(raw, roots)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError as exc:
        raise ImageRejected("image unavailable") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ImageRejected("not a regular file")
        if not 0 < info.st_size <= MAX_LOCAL_IMAGE_BYTES:
            raise ImageRejected("image size rejected")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(MAX_LOCAL_IMAGE_BYTES + 1)
    finally:
        os.close(fd)
    if not data or len(data) > MAX_LOCAL_IMAGE_BYTES:
        raise ImageRejected("image size rejected")
    mime = _sniff(data[:16])
    if mime is None or mime != LOCAL_IMAGE_EXTS[path.suffix.lower()]:
        raise ImageRejected("not a supported image")
    return data, mime
