# Orders API for the MCP integration test. Simplified single-endpoint contract:
# the mock accepts flattened order input and stores one row. The real Wakilni
# 3-step bulk flow (start_bulk -> add_delivery -> end_bulk) is NOT simulated
# here — the MCP's service layer will own that orchestration when we swap in the
# real API.
#
#   Auth: Bearer OAuth access token (reuses the shared get_current_user dep).
#   401 = not authenticated. 404 = order missing OR not visible to the caller
#   (existence is never leaked). Visibility: a customer sees only their own
#   orders; an INTERNAL user sees all.
#
# Manual test checklist (replace $BASE, $CUST (customer token), $INT (internal
# token), and $TRACK with a tracking_id from step 2):
#
#   # 1. Unauthenticated POST /orders -> 401
#   curl -i -X POST $BASE/orders -H 'Content-Type: application/json' \
#        -d '{"receiver_name":"Sam","receiver_phone":"12345",
#             "receiver_address":"Beirut","package_quantity":1}'
#   #   => 401, header: WWW-Authenticate: Bearer
#
#   # 2. Authenticated POST /orders with a valid body -> 201 with tracking_id
#   curl -i -X POST $BASE/orders -H "Authorization: Bearer $CUST" \
#        -H 'Content-Type: application/json' \
#        -d '{"receiver_name":"Sam","receiver_phone":"12345",
#             "receiver_address":"Beirut, Hamra St","receiver_area":"Hamra",
#             "collection_amount":"100.00","currency":"USD",
#             "package_quantity":2,"note":"call on arrival"}'
#   #   => 201, JSON with "status":"PENDING" and a 12-char "tracking_id"
#
#   # 3. POST /orders with missing receiver_phone -> 422
#   curl -i -X POST $BASE/orders -H "Authorization: Bearer $CUST" \
#        -H 'Content-Type: application/json' \
#        -d '{"receiver_name":"Sam","receiver_address":"Beirut",
#             "package_quantity":1}'
#   #   => 422
#
#   # 4. GET /orders/$TRACK -> 200 with the created order
#   curl -i $BASE/orders/$TRACK -H "Authorization: Bearer $CUST"
#   #   => 200, the order JSON
#
#   # 5. GET /orders as a customer -> only that customer's orders
#   curl -i $BASE/orders -H "Authorization: Bearer $CUST"
#   #   => 200, {"items":[...only mine...],"total":N,"limit":20,"offset":0}
#
#   # 6. GET /orders as INTERNAL -> all orders
#   curl -i $BASE/orders -H "Authorization: Bearer $INT"
#   #   => 200, {"items":[...everyone's...],"total":M,...}
#
# ---------------------------------------------------------------------------
# Extended endpoints (search, cancel, comments, bulk). Verification checklist
# ($SELF = a tracking_id the customer owns; $OTHER = one they don't):
#
#   # 7. GET /orders/search?status=PENDING -> only the caller's PENDING orders
#   curl -i "$BASE/orders/search?status=PENDING" -H "Authorization: Bearer $CUST"
#   #   => 200, items all status=PENDING and all owned by the caller
#
#   # 8. Customer search never leaks another user's orders
#   curl -i "$BASE/orders/search?receiver_name_contains=a" \
#        -H "Authorization: Bearer $CUST"
#   #   => 200, every item has the caller's user_id (server-enforced)
#
#   # 9. INTERNAL search returns all owners' orders
#   curl -i "$BASE/orders/search" -H "Authorization: Bearer $INT"
#   #   => 200, items across every owner
#
#   # 10. Unauthenticated GET /orders/search -> 401 (Bearer required, enforced
#   #     via the caller: CurrentUser dependency)
#   curl -i "$BASE/orders/search"
#   #   => 401, header: WWW-Authenticate: Bearer
#
#   # 11. Cancel a PENDING order -> 200 with status=CANCELED
#   curl -i -X POST $BASE/orders/$SELF/cancel -H "Authorization: Bearer $CUST"
#   #   => 200, {"status":"CANCELED",...}
#
#   # 12. Cancel an already-CANCELED order -> 409
#   curl -i -X POST $BASE/orders/$SELF/cancel -H "Authorization: Bearer $CUST"
#   #   => 409, {"detail":"Order in status CANCELED cannot be canceled; ..."}
#
#   # 13. Customer cancels another user's order -> 403 (NOT 404)
#   curl -i -X POST $BASE/orders/$OTHER/cancel -H "Authorization: Bearer $CUST"
#   #   => 403
#
#   # 14. Add a comment -> 201
#   curl -i -X POST $BASE/orders/$SELF/comments -H "Authorization: Bearer $CUST" \
#        -H 'Content-Type: application/json' -d '{"content":"packed and ready"}'
#   #   => 201, {"id":..,"order_id":..,"user_id":..,"content":"...","created_at":...}
#
#   # 15. List comments -> chronological (oldest first)
#   curl -i $BASE/orders/$SELF/comments -H "Authorization: Bearer $CUST"
#   #   => 200, items ordered by created_at ASC
#
#   # 16. Unauthenticated GET /orders/{tracking_id}/comments -> 401 (Bearer
#   #     required, enforced via the caller: CurrentUser dependency)
#   curl -i $BASE/orders/$SELF/comments
#   #   => 401, header: WWW-Authenticate: Bearer
#
#   # 17. Bulk create 3 valid orders -> 201, count=3, 3 tracking_ids
#   curl -i -X POST $BASE/orders/bulk -H "Authorization: Bearer $CUST" \
#        -H 'Content-Type: application/json' \
#        -d '{"orders":[{"receiver_name":"A","receiver_phone":"12345",
#             "receiver_address":"x","package_quantity":1},
#             {"receiver_name":"B","receiver_phone":"12345",
#             "receiver_address":"y","package_quantity":1},
#             {"receiver_name":"C","receiver_phone":"12345",
#             "receiver_address":"z","package_quantity":1}]}'
#   #   => 201, {"created":[...3...],"count":3}
#
#   # 18. Bulk with one invalid order (missing receiver_phone) -> 422, nothing created
#   curl -i -X POST $BASE/orders/bulk -H "Authorization: Bearer $CUST" \
#        -H 'Content-Type: application/json' \
#        -d '{"orders":[{"receiver_name":"A","receiver_phone":"12345",
#             "receiver_address":"x","package_quantity":1},
#             {"receiver_name":"B","receiver_address":"y","package_quantity":1},
#             {"receiver_name":"C","receiver_phone":"12345",
#             "receiver_address":"z","package_quantity":1}]}'
#   #   => 422, loc points at orders[1].receiver_phone; 0 rows inserted

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser, get_db
from app.domains.orders.enums import OrderStatus
from app.domains.orders.schemas import (
    BulkOrderCreate,
    BulkOrderCreateResult,
    OrderCancelRequest,
    OrderCommentCreate,
    OrderCommentList,
    OrderCommentRead,
    OrderCreate,
    OrderList,
    OrderRead,
    OrderSearchFilters,
)
from app.domains.orders.service import OrderService

router = APIRouter(prefix="/orders", tags=["orders"])


def get_order_service(db: Annotated[AsyncSession, Depends(get_db)]) -> OrderService:
    return OrderService(db)


ServiceDep = Annotated[OrderService, Depends(get_order_service)]


@router.post("", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
async def create_order(
    body: OrderCreate, caller: CurrentUser, service: ServiceDep
) -> OrderRead:
    return await service.create(caller, body)


@router.get("", response_model=OrderList)
async def list_orders(
    caller: CurrentUser,
    service: ServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: Annotated[OrderStatus | None, Query()] = None,
) -> OrderList:
    items, total = await service.list(
        caller, limit=limit, offset=offset, status=status
    )
    return OrderList(items=items, total=total, limit=limit, offset=offset)


# NOTE: /search and /bulk are declared before /{tracking_id} so the literal
# paths win over the path-parameter route (otherwise "search" would be matched
# as a tracking_id).
@router.get("/search", response_model=OrderList)
async def search_orders(
    caller: CurrentUser,
    service: ServiceDep,
    filters: Annotated[OrderSearchFilters, Query()],
) -> OrderList:
    items, total = await service.search(caller, filters)
    return OrderList(
        items=items, total=total, limit=filters.limit, offset=filters.offset
    )


@router.post(
    "/bulk",
    response_model=BulkOrderCreateResult,
    status_code=status.HTTP_201_CREATED,
)
async def bulk_create_orders(
    body: BulkOrderCreate, caller: CurrentUser, service: ServiceDep
) -> BulkOrderCreateResult:
    created = await service.bulk_create(caller, body.orders)
    return BulkOrderCreateResult(created=created, count=len(created))


@router.get("/{tracking_id}", response_model=OrderRead)
async def get_order(
    tracking_id: str, caller: CurrentUser, service: ServiceDep
) -> OrderRead:
    return await service.get_by_tracking(caller, tracking_id)


@router.post("/{tracking_id}/cancel", response_model=OrderRead)
async def cancel_order(
    tracking_id: str,
    caller: CurrentUser,
    service: ServiceDep,
    body: OrderCancelRequest | None = None,
) -> OrderRead:
    reason = body.reason if body is not None else None
    return await service.cancel(caller, tracking_id, reason=reason)


@router.post(
    "/{tracking_id}/comments",
    response_model=OrderCommentRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_order_comment(
    tracking_id: str,
    body: OrderCommentCreate,
    caller: CurrentUser,
    service: ServiceDep,
) -> OrderCommentRead:
    return await service.add_comment(caller, tracking_id, body)


@router.get("/{tracking_id}/comments", response_model=OrderCommentList)
async def list_order_comments(
    tracking_id: str,
    caller: CurrentUser,
    service: ServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OrderCommentList:
    items, total = await service.list_comments(
        caller, tracking_id, limit=limit, offset=offset
    )
    return OrderCommentList(items=items, total=total, limit=limit, offset=offset)
