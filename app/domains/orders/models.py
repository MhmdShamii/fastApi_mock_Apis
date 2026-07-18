from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.domains.orders.enums import Currency, OrderStatus


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        index=True,
        nullable=False,
    )
    tracking_id: Mapped[str] = mapped_column(
        String(12),
        unique=True,
        index=True,
        nullable=False,
    )
    status: Mapped[OrderStatus] = mapped_column(
        SqlEnum(OrderStatus, name="order_status"),
        nullable=False,
        server_default=OrderStatus.PENDING.value,
    )
    receiver_name: Mapped[str] = mapped_column(nullable=False)
    receiver_phone: Mapped[str] = mapped_column(nullable=False)
    receiver_address: Mapped[str] = mapped_column(nullable=False)
    receiver_area: Mapped[str | None] = mapped_column(nullable=True)
    collection_amount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        server_default="0",
    )
    currency: Mapped[Currency] = mapped_column(
        SqlEnum(Currency, name="order_currency"),
        nullable=False,
        server_default=Currency.USD.value,
    )
    package_quantity: Mapped[int] = mapped_column(nullable=False)
    note: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
