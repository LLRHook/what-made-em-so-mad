from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO

_CHUNK_BYTES = 1024 * 1024


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK_BYTES), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_stream_to_file(src: BinaryIO, dest: Path) -> str:
    """Stream `src` into `dest` and return the SHA-256 of the bytes written."""
    h = hashlib.sha256()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as out:
        while True:
            chunk = src.read(_CHUNK_BYTES)
            if not chunk:
                break
            h.update(chunk)
            out.write(chunk)
    return h.hexdigest()
