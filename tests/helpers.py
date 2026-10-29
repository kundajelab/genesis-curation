from collections.abc import Callable
from typing import Any


def rejects(action: Callable[[], Any], text: str = "") -> None:
    try:
        action()
    except (ValueError, OSError) as error:
        assert text.lower() in str(error).lower(), str(error)
    else:
        raise AssertionError("Invalid operation was accepted")
