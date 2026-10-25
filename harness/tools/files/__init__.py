"""File tools: line-windowed `read_file`, `read_image`, and the contracts and schemas of the
aisuite file tools the catalog borrows.

The windowed reader replaces the toolkit's own (`reader.py`), the borrowed tools get their
schemas and contracts attached (`schemas.py`, `contracts.py`), and the model-facing text of every
result is in `renders.py` while what a person sees is in `present.py`.
"""

from __future__ import annotations

from .contracts import READ_FILE_CONTRACT, apply_file_contracts
from .reader import file_tools

__all__ = [
    "READ_FILE_CONTRACT",
    "apply_file_contracts",
    "file_tools",
]
