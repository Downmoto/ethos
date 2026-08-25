"""Small file-reading primitives shared by capabilities."""

from pathlib import Path


class FileTooLargeError(ValueError):
    """A bounded read found content beyond its permitted prefix."""

    pass


def read_bounded_utf8(path: Path, max_bytes: int) -> str:
    """Read UTF-8 only after proving the complete file fits the byte limit."""

    with path.open("rb") as file:
        content = file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise FileTooLargeError
    return content.decode("utf-8")
