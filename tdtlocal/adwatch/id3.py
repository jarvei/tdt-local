"""The ``admode`` text Mediaset's DAI leaves in an HLS ``X-MESSAGE-DATA`` attribute."""

from __future__ import annotations

import base64


def admode(value: str | None) -> str | None:
    """``out_<ms>`` / ``in`` inside a base64 ``X-MESSAGE-DATA`` attribute, or ``None``."""
    if not value:
        return None
    try:
        raw = base64.b64decode(value, validate=False)
    except (ValueError, TypeError):
        return None
    text = raw.decode("latin-1", "replace")
    index = text.find("admode")
    if index < 0:
        return None
    j = index + len("admode")
    while j < len(text) and ord(text[j]) < 32:
        j += 1
    k = j
    while k < len(text) and ord(text[k]) >= 32:
        k += 1
    return text[j:k] if k > j else None
