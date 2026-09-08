import hashlib
from pathlib import Path


def compute_sha256(path) -> str:
    """Compute SHA256 hash of a file using 65536-byte chunks."""
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()
