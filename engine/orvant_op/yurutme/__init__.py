"""S3 hedef yürütücüsü."""

from .akis import goal_oku, hedef_istemi
from .zamanlayici import Yurutme

__all__ = ["Yurutme", "goal_oku", "hedef_istemi"]
