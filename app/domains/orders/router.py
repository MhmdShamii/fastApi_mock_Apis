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

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser, get_db
from app.domains.orders.enums import OrderStatus
from app.domains.orders.schemas import OrderCreate, OrderList, OrderRead
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


@router.get("/{tracking_id}", response_model=OrderRead)
async def get_order(
    tracking_id: str, caller: CurrentUser, service: ServiceDep
) -> OrderRead:
    return await service.get_by_tracking(caller, tracking_id)
