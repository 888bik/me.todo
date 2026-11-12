"""Helpers shared by the test modules."""

import tempfile
import uuid
from pathlib import Path


def make_data_dir():
    """A throwaway plugin data folder that needs no TemporaryDirectory cleanup."""
    path = Path(tempfile.gettempdir()) / ("me-todo-" + uuid.uuid4().hex)
    path.mkdir()
    return path
