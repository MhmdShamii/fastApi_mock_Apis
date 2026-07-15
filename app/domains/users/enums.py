from enum import Enum

class Role(str, Enum):
    """User role. Drives RBAC in the service layer."""
    INTERNAL = "internal"
    CUSTOMER = "customer"
    GUEST = "guest"
