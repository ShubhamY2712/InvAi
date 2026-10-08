"""Errors raised by services. app/main.py turns each into an HTTP response {"detail": ...} with its status code,
exactly like FastAPI's HTTPException, so services don't need to depend on FastAPI."""


class ServiceError(Exception):
    status_code = 400

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class BadRequest(ServiceError):
    status_code = 400


class Unauthorized(ServiceError):
    status_code = 401


class NotFound(ServiceError):
    status_code = 404


class Conflict(ServiceError):
    status_code = 409


class InvalidInput(ServiceError):
    status_code = 422


class Unavailable(ServiceError):
    status_code = 503
