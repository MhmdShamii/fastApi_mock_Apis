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
