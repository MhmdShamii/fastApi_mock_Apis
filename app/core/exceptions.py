class AppError(Exception):
    """Base class for domain errors that map onto HTTP responses."""

    status_code: int = 500
    detail: str = "Internal server error"

    def __init__(self, detail: str | None = None):
        if detail is not None:
            self.detail = detail
        super().__init__(self.detail)


class NotFoundError(AppError):
    """A requested resource does not exist."""

    status_code = 404
    detail = "Resource not found"


class ConflictError(AppError):
    """The request conflicts with the current state (e.g. duplicate)."""

    status_code = 409
    detail = "Resource conflict"
