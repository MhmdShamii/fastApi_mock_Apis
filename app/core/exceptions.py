class AppError(Exception):
    """Base class for domain errors that map onto HTTP responses."""

    status_code: int = 500
    detail: str = "Internal server error"

    def __init__(self, detail: str | None = None):
        if detail is not None:
            self.detail = detail
        super().__init__(self.detail)


class BadRequestError(AppError):
    """The request is malformed in a way Pydantic validation doesn't cover."""

    status_code = 400
    detail = "Bad request"


class NotFoundError(AppError):
    """A requested resource does not exist."""

    status_code = 404
    detail = "Resource not found"


class ConflictError(AppError):
    """The request conflicts with the current state (e.g. duplicate)."""

    status_code = 409
    detail = "Resource conflict"


class UnauthorizedError(AppError):
    """Authentication is missing or invalid."""

    status_code = 401
    detail = "Not authenticated"


class ForbiddenError(AppError):
    """The caller is authenticated but not allowed to perform this action."""

    status_code = 403
    detail = "Not permitted"
