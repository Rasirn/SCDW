"""DeepSeek 模型配置。"""
import os

from dotenv import load_dotenv
from scdw.common.paths import PROJECT_ROOT

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-pro"
DEEPSEEK_FAST_MODEL = "deepseek-v4-flash"


def get_deepseek_model() -> str:
    """返回默认 V4 模型，保留历史模型变量名作为选择入口。"""
    requested = os.getenv("DEEPSEEK_MODEL") or os.getenv("DEEPEEK_MODEL")
    if requested in {None, "", "deepseek-chat", "deepseek-reasoner", "deepseek_v4", "deepseek-v4"}:
        return DEEPSEEK_DEFAULT_MODEL
    return requested


def get_deepseek_api_key() -> str:
    """返回当前进程加载的 Key，避免 GUI 持有旧的模块级缓存值。"""
    load_dotenv(PROJECT_ROOT / ".env")
    return os.getenv("DEEPSEEK_API_KEY", "").strip()


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def get_tool_budget() -> tuple[int, int]:
    """Return the per-turn soft/hard tool-call budget.

    The hard limit is intentionally bounded so configuration cannot turn a
    runaway model loop into an unbounded TIA mutation loop.
    """
    soft = min(_positive_int("SCDW_TOOL_SOFT_LIMIT", 20), 30)
    hard = min(_positive_int("SCDW_TOOL_HARD_LIMIT", 40), 50)
    if hard <= soft:
        hard = min(50, soft + 10)
    return soft, hard
