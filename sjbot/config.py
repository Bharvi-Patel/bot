"""Small settings the tools share."""
from __future__ import annotations

import os


def full_image_url(value: str | None) -> str | None:
    """The catalog stores bare file names. Set SJ_IMAGE_BASE_URL to the folder the site serves them from
    (for example https://example.com/media/products) and this returns a full link.
    Without it, the bare name is returned unchanged and the bot should not show it as a link."""
    if not value:
        return None
    if value.startswith(("http://", "https://")):
        return value
    base = os.getenv("SJ_IMAGE_BASE_URL", "").strip()
    if not base:
        return value
    return base.rstrip("/") + "/" + value.lstrip("/")
