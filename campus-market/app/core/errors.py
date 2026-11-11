"""Transport-independent errors, later mapped to HTTP error responses."""
from __future__ import annotations


class BusinessError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


def require(condition: bool, code: str, message: str, status: int = 400) -> None:
    if not condition:
        raise BusinessError(code, message, status)
