from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domains.orders.enums import Currency, OrderStatus


class OrderCreate(BaseModel):
    """Simplified single-step order input the MCP posts.

    The real Wakilni API needs a 3-step bulk flow; the mock deliberately does
    not. The MCP's service layer will own that orchestration later — here we
    only accept the flattened fields and prove the round trip.
    """

    receiver_name: str = Field(min_length=1, max_length=100)
    receiver_phone: str = Field(min_length=5, max_length=20)
    receiver_address: str = Field(min_length=1, max_length=500)
    receiver_area: str | None = None
    collection_amount: Decimal = Field(default=Decimal("0"), ge=0)
    currency: Literal["USD", "LBP"] = "USD"
    package_quantity: int = Field(ge=1)
    note: str | None = None


class OrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tracking_id: str
    status: OrderStatus
    receiver_name: str
    receiver_phone: str
    receiver_address: str
    receiver_area: str | None
    collection_amount: Decimal
    currency: Currency
    package_quantity: int
    note: str | None
    created_at: datetime


class OrderList(BaseModel):
    items: list[OrderRead]
    total: int
    limit: int
    offset: int


class OrderSearchFilters(BaseModel):
    """Query-parameter model for GET /orders/search. Every filter is optional
    and combined with AND on top of the caller's visibility scope."""

    status: OrderStatus | None = None
    tracking_id_prefix: str | None = Field(default=None, max_length=20)
    receiver_name_contains: str | None = Field(default=None, max_length=100)
    created_after: datetime | None = None
    created_before: datetime | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class OrderCancelRequest(BaseModel):
    """Optional body for POST /orders/{tracking_id}/cancel. ``reason`` is
    accepted for forward-compatibility but not persisted by the mock."""

    reason: str | None = Field(default=None, max_length=500)


class OrderCommentCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class OrderCommentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    user_id: int
    content: str
    created_at: datetime


class OrderCommentList(BaseModel):
    items: list[OrderCommentRead]
    total: int
    limit: int
    offset: int


class BulkOrderItem(OrderCreate):
    """One order inside a bulk request. ``owner_user_id`` is honored only for
    INTERNAL callers (a customer's rows are always owned by themselves)."""

    owner_user_id: int | None = None


class BulkOrderCreate(BaseModel):
    # Length is validated in the service layer so empty / oversized batches
    # return 400 (per the contract) rather than Pydantic's 422.
    orders: list[BulkOrderItem]


class BulkOrderCreateResult(BaseModel):
    created: list[OrderRead]
    count: int
