"""向后兼容外壳：所有主题 token 已迁移到 src.ui.theme

新代码请直接 `from src.ui.theme import ...`。这里只是为了不让旧的 import
立刻断掉。这一层在所有 widget 切到 theme 后会被删除。
"""
from src.ui.theme import *  # noqa: F401,F403
from src.ui.theme import apply_theme as apply_dark_theme  # noqa: F401
