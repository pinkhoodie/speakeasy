"""Screens, pictures and files shared with a spoken request ("look at this").

Limits the Mac and the plugin agree on; ``/voice/status`` reports them under ``attachments``.
Images are sized for being re-sent: Hermes sends every image in a session again on each later
model call, and its whole request body is capped at 10 MB (base64 adds a third).
"""
from __future__ import annotations

MAX_ATTACHMENTS = 3                 # pictures and files waiting for the next request
MAX_REQUEST_IMAGES = 4              # the attachments plus one screen capture
MAX_IMAGE_BYTES = 2_500_000         # one re-encoded JPEG (the Mac aims for about 300 KB)
MAX_REQUEST_IMAGE_BYTES = 6_500_000  # every image in one request, before base64
MAX_FILE_BYTES = 10_000_000         # a non-image file, saved on the Hermes machine, never inlined
