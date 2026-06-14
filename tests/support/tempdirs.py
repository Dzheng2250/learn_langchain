"""Temporary directory helpers that keep test artifacts out of the repository root."""

import shutil
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


@contextmanager
def writable_temp_directory(prefix: str):
    """Yield an isolated `.test_tmp` directory and best-effort clean it afterward."""
    root = Path(".test_tmp") / f"{prefix}-{uuid4().hex}"
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)
