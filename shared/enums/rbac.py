from enum import Enum


class Role(str, Enum):
    OWNER = "owner"
    OPERATOR = "operator"
    VIEWER = "viewer"


ROLE_RANK = {
    Role.VIEWER.value: 1,
    Role.OPERATOR.value: 2,
    Role.OWNER.value: 3,
}
