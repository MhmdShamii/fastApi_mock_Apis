import secrets
import string

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.domains.orders.enums import Currency, OrderStatus
from app.domains.orders.models import Order
from app.domains.orders.schemas import OrderCreate
from app.domains.users.enums import Role
from app.domains.users.models import User

_TRACKING_ALPHABET = string.ascii_uppercase + string.digits
_TRACKING_LENGTH = 12
_MAX_TRACKING_ATTEMPTS = 5


def _generate_tracking_id() -> str:
    return "".join(
        secrets.choice(_TRACKING_ALPHABET) for _ in range(_TRACKING_LENGTH)
    )


class OrderService:
    """Data-access and business logic for orders."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, owner: User, data: OrderCreate) -> Order:
        """Insert a new PENDING order owned by ``owner`` with a unique
        tracking_id. Retries on the (rare) tracking_id collision."""
        for _ in range(_MAX_TRACKING_ATTEMPTS):
            order = Order(
                user_id=owner.id,
                tracking_id=_generate_tracking_id(),
                status=OrderStatus.PENDING,
                receiver_name=data.receiver_name,
                receiver_phone=data.receiver_phone,
                receiver_address=data.receiver_address,
                receiver_area=data.receiver_area,
                collection_amount=data.collection_amount,
                currency=Currency(data.currency),
                package_quantity=data.package_quantity,
                note=data.note,
            )
            self.db.add(order)
            try:
                await self.db.commit()
            except IntegrityError:
                # Almost certainly a tracking_id collision — roll back and
                # retry with a freshly generated id.
                await self.db.rollback()
                continue
            await self.db.refresh(order)
            return order

        raise ConflictError("Could not allocate a unique tracking_id")

    async def get_by_tracking(self, caller: User, tracking_id: str) -> Order:
        """Return the order if the caller owns it or is INTERNAL.

        Raises 404 both when the order does not exist and when it exists but
        the caller is not permitted to see it, so ownership can't be probed.
        """
        result = await self.db.execute(
            select(Order).where(Order.tracking_id == tracking_id)
        )
        order = result.scalar_one_or_none()
        if order is None:
            raise NotFoundError("Order not found")
        if caller.role != Role.INTERNAL and order.user_id != caller.id:
            raise NotFoundError("Order not found")
        return order

    async def list(
        self,
        caller: User,
        *,
        limit: int = 20,
        offset: int = 0,
        status: OrderStatus | None = None,
    ) -> tuple[list[Order], int]:
        """List orders visible to the caller, newest first.

        A CUSTOMER (or any non-INTERNAL role) sees only their own orders;
        INTERNAL sees everything. Returns ``(items, total)`` where ``total`` is
        the unpaginated count under the same visibility/status filter.
        """
        conditions = []
        if caller.role != Role.INTERNAL:
            conditions.append(Order.user_id == caller.id)
        if status is not None:
            conditions.append(Order.status == status)

        count_stmt = select(func.count()).select_from(Order)
        items_stmt = select(Order)
        if conditions:
            count_stmt = count_stmt.where(*conditions)
            items_stmt = items_stmt.where(*conditions)

        total = (await self.db.execute(count_stmt)).scalar_one()
        items_stmt = (
            items_stmt.order_by(Order.created_at.desc()).limit(limit).offset(offset)
        )
        result = await self.db.execute(items_stmt)
        return list(result.scalars().all()), total
