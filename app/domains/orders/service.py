# Annotations are lazy (PEP 563) so type hints like ``list[Order]`` in methods
# defined after the ``list`` method don't resolve to that method at class-body
# evaluation time.
from __future__ import annotations

import secrets
import string

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.domains.orders.enums import Currency, OrderStatus
from app.domains.orders.models import Order, OrderComment
from app.domains.orders.schemas import (
    BulkOrderItem,
    OrderCommentCreate,
    OrderCreate,
    OrderSearchFilters,
)
from app.domains.users.enums import Role
from app.domains.users.models import User

_TRACKING_ALPHABET = string.ascii_uppercase + string.digits
_TRACKING_LENGTH = 12
_MAX_TRACKING_ATTEMPTS = 5

# Only these statuses can be canceled; anything else is terminal or in-flight.
_CANCELABLE_STATUSES = (OrderStatus.PENDING, OrderStatus.CONFIRMED)

# Bulk request size bounds (inclusive).
_BULK_MIN = 1
_BULK_MAX = 50


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

    async def search(
        self, caller: User, filters: OrderSearchFilters
    ) -> tuple[list[Order], int]:
        """Filtered/searched order listing, newest first.

        Same visibility rule as ``list`` (customer -> own only, INTERNAL ->
        all); every supplied filter is ANDed on top. Returns ``(items, total)``
        where ``total`` is the unpaginated count under the same filters.
        """
        conditions = []
        if caller.role != Role.INTERNAL:
            conditions.append(Order.user_id == caller.id)
        if filters.status is not None:
            conditions.append(Order.status == filters.status)
        if filters.tracking_id_prefix:
            conditions.append(
                Order.tracking_id.startswith(
                    filters.tracking_id_prefix, autoescape=True
                )
            )
        if filters.receiver_name_contains:
            conditions.append(
                Order.receiver_name.icontains(
                    filters.receiver_name_contains, autoescape=True
                )
            )
        if filters.created_after is not None:
            conditions.append(Order.created_at >= filters.created_after)
        if filters.created_before is not None:
            conditions.append(Order.created_at < filters.created_before)

        count_stmt = select(func.count()).select_from(Order)
        items_stmt = select(Order)
        if conditions:
            count_stmt = count_stmt.where(*conditions)
            items_stmt = items_stmt.where(*conditions)

        total = (await self.db.execute(count_stmt)).scalar_one()
        items_stmt = (
            items_stmt.order_by(Order.created_at.desc())
            .limit(filters.limit)
            .offset(filters.offset)
        )
        result = await self.db.execute(items_stmt)
        return list(result.scalars().all()), total

    async def cancel(
        self, caller: User, tracking_id: str, *, reason: str | None = None
    ) -> Order:
        """Cancel a PENDING/CONFIRMED order the caller is allowed to act on.

        404 if the order genuinely doesn't exist, 403 if it exists but the
        caller is neither its owner nor INTERNAL (deliberately NOT 404 here —
        the cancel contract distinguishes the two), 409 if the order is in a
        status that can't be canceled. ``reason`` is accepted but not stored.
        """
        order = await self._get_order_or_404(tracking_id)
        self._require_owner_or_internal(caller, order)

        if order.status not in _CANCELABLE_STATUSES:
            raise ConflictError(
                f"Order in status {order.status.value} cannot be canceled; "
                "only PENDING or CONFIRMED orders can be canceled"
            )

        order.status = OrderStatus.CANCELED
        await self.db.commit()
        await self.db.refresh(order)
        return order

    async def add_comment(
        self, caller: User, tracking_id: str, data: OrderCommentCreate
    ) -> OrderComment:
        """Append a comment authored by the caller. 404 if the order is
        missing, 403 if the caller neither owns it nor is INTERNAL."""
        order = await self._get_order_or_404(tracking_id)
        self._require_owner_or_internal(caller, order)

        comment = OrderComment(
            order_id=order.id,
            user_id=caller.id,
            content=data.content,
        )
        self.db.add(comment)
        await self.db.commit()
        await self.db.refresh(comment)
        return comment

    async def list_comments(
        self, caller: User, tracking_id: str, *, limit: int = 20, offset: int = 0
    ) -> tuple[list[OrderComment], int]:
        """List an order's comments oldest-first (chronological). Same 404/403
        rules as ``add_comment``."""
        order = await self._get_order_or_404(tracking_id)
        self._require_owner_or_internal(caller, order)

        count_stmt = (
            select(func.count())
            .select_from(OrderComment)
            .where(OrderComment.order_id == order.id)
        )
        total = (await self.db.execute(count_stmt)).scalar_one()

        items_stmt = (
            select(OrderComment)
            .where(OrderComment.order_id == order.id)
            .order_by(OrderComment.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(items_stmt)
        return list(result.scalars().all()), total

    async def bulk_create(
        self, caller: User, items: list[BulkOrderItem]
    ) -> list[Order]:
        """Create a whole batch atomically. Validation (Pydantic) has already
        run, so a bad item never reaches here; we only enforce the batch-size
        bounds (400) and per-row tracking_id uniqueness.

        All rows commit together in one transaction. A tracking_id collision on
        a single row is retried in isolation via a SAVEPOINT so it never aborts
        the rest of the batch. Ownership: customers always own their rows;
        INTERNAL callers may set ``owner_user_id`` per row (defaults to caller).
        """
        if not (_BULK_MIN <= len(items) <= _BULK_MAX):
            raise BadRequestError(
                f"orders must contain between {_BULK_MIN} and {_BULK_MAX} "
                f"items (got {len(items)})"
            )

        created: list[Order] = []
        for item in items:
            if caller.role == Role.INTERNAL and item.owner_user_id is not None:
                owner_id = item.owner_user_id
            else:
                owner_id = caller.id

            order = None
            for _ in range(_MAX_TRACKING_ATTEMPTS):
                candidate = Order(
                    user_id=owner_id,
                    tracking_id=_generate_tracking_id(),
                    status=OrderStatus.PENDING,
                    receiver_name=item.receiver_name,
                    receiver_phone=item.receiver_phone,
                    receiver_address=item.receiver_address,
                    receiver_area=item.receiver_area,
                    collection_amount=item.collection_amount,
                    currency=Currency(item.currency),
                    package_quantity=item.package_quantity,
                    note=item.note,
                )
                try:
                    # SAVEPOINT: a unique-collision rolls back just this row and
                    # leaves the outer batch transaction intact for a retry.
                    async with self.db.begin_nested():
                        self.db.add(candidate)
                        await self.db.flush()
                except IntegrityError:
                    continue
                order = candidate
                break

            if order is None:
                await self.db.rollback()
                raise ConflictError("Could not allocate a unique tracking_id")
            created.append(order)

        await self.db.commit()
        for order in created:
            await self.db.refresh(order)
        return created

    async def _get_order_or_404(self, tracking_id: str) -> Order:
        result = await self.db.execute(
            select(Order).where(Order.tracking_id == tracking_id)
        )
        order = result.scalar_one_or_none()
        if order is None:
            raise NotFoundError("Order not found")
        return order

    @staticmethod
    def _require_owner_or_internal(caller: User, order: Order) -> None:
        """Raise 403 (not 404) unless the caller owns the order or is INTERNAL.

        Used by cancel/comments, which — unlike get_by_tracking — surface a
        distinct 403 for a real order the caller may not touch.
        """
        if caller.role != Role.INTERNAL and order.user_id != caller.id:
            raise ForbiddenError("Not permitted")
