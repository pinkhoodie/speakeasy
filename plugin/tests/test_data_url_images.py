"""Hermes' API server turns every `MEDIA:<path>` image into an inline base64 data URL (remote frontends
can't read the host's files). Those are the images Speakeasy actually receives, so they must become cards."""
from __future__ import annotations

import base64
from pathlib import Path

from speakeasy.text import delivery_text, public_result, split_result

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\x00" * 60_000 + b"\xff\xd9"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20_000


def md(mime: str, blob: bytes) -> str:
    return f"![image](data:{mime};base64,{base64.b64encode(blob).decode()})"


def test_inline_images_from_the_api_become_picture_cards(tmp_path: Path) -> None:
    output = ("Here are two photos of the hotel. The first is the main building.\n\n"
              + md("image/jpeg", JPEG) + "\n\n" + md("image/png", PNG))
    assert len(output) > 8000  # the display-text cap used to cut these mid-image
    result = split_result(output, (tmp_path,))
    images = [c for c in result["cards"] if c["kind"] == "image"]
    assert len(images) == 2
    assert [Path(c["path"]).read_bytes() for c in images] == [JPEG, PNG]
    assert all(tmp_path in Path(c["path"]).parents for c in images)
    assert "base64" not in result["full"] and "data:" not in result["full"]
    assert result["full"].startswith("Here are two photos")
    assert all("path" not in c for c in public_result(result)["cards"])  # the app never sees paths
    assert delivery_text(result).count("MEDIA:") == 2  # chat delivery still gets the pictures


def test_same_image_twice_is_saved_once(tmp_path: Path) -> None:
    result = split_result("Look:\n\n" + md("image/jpeg", JPEG) + "\n\n" + md("image/jpeg", JPEG), (tmp_path,))
    assert len({c["path"] for c in result["cards"] if c["kind"] == "image"}) == 1


def test_bytes_that_do_not_match_the_claimed_type_are_dropped(tmp_path: Path) -> None:
    fake = md("image/png", b"<svg onload=alert(1)>" + b"x" * 100)
    result = split_result("Here it is.\n\n" + fake, (tmp_path,))
    assert not result.get("cards")
    assert "base64" not in result["full"]
    assert not list(tmp_path.rglob("*.png"))


def test_oversized_inline_image_is_dropped_not_truncated_into_the_text(tmp_path: Path) -> None:
    huge = b"\xff\xd8\xff" + b"\x00" * 9_000_000
    result = split_result("Big one.\n\n" + md("image/jpeg", huge), (tmp_path,))
    assert not result.get("cards")
    assert result["full"] == "Big one."
