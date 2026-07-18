from enum import Enum


class OrderStatus(str, Enum):
    """Lifecycle status of an order. Mirrors the values the real Wakilni flow
    would eventually report; the mock only ever writes PENDING on insert."""

    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class Currency(str, Enum):
    """Collection currency for an order."""

    USD = "USD"
    LBP = "LBP"
