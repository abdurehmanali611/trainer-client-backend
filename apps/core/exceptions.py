"""Feature 15 exceptions: the read-only write gate's 403 response."""
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler as drf_exception_handler


class BlockedAccessError(APIException):
    """A write from a client whose payment deadline passed.

    ``detail`` is a plain dict so DRF's handler emits it verbatim; the
    custom ``exception_handler`` below rebuilds it with ``blocked`` as a
    real boolean (DRF's ErrorDetail pass stringifies every value).
    """

    status_code = 403
    default_code = "blocked"

    def __init__(self, reason: str):
        self.reason = reason
        self.public_detail = (
            "Payment required to make changes. Your trainer must "
            "approve your payment first."
        )
        super().__init__(
            {
                "detail": self.public_detail,
                "blocked": True,
                "reason": reason,
            },
            code="blocked",
        )


def exception_handler(exc, context):
    """Default DRF handling, plus a verbatim boolean `blocked` flag."""
    response = drf_exception_handler(exc, context)
    if response is not None and isinstance(exc, BlockedAccessError):
        response.data = {
            "detail": exc.public_detail,
            "blocked": True,
            "reason": exc.reason,
        }
    return response
