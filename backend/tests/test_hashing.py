from __future__ import annotations

import hashlib
import io
from pathlib import Path

from app.services.hashing import sha256_file, sha256_stream_to_file


def test_sha256_file_matches_hashlib(tmp_path: Path) -> None:
    body = b"hello world" * 4096
    p = tmp_path / "x.bin"
    p.write_bytes(body)
    assert sha256_file(p) == hashlib.sha256(body).hexdigest()


def test_sha256_stream_writes_and_hashes(tmp_path: Path) -> None:
    body = b"abcdef" * 1024
    dest = tmp_path / "nested" / "out.bin"
    src = io.BytesIO(body)
    digest = sha256_stream_to_file(src, dest)
    assert digest == hashlib.sha256(body).hexdigest()
    assert dest.read_bytes() == body
