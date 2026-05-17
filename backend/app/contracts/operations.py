from enum import Enum

class Action(str, Enum):
    create = "create"
    modify = "modify"
    delete = "delete"
