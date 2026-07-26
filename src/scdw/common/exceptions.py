"""项目异常类型。"""


class ScdwError(Exception):
    """项目基础异常。"""


class LlmError(ScdwError):
    """大模型调用基础异常。"""


class LlmAuthenticationError(LlmError):
    """大模型身份认证失败。"""


class LlmRateLimitError(LlmError):
    """大模型请求受限流。"""


class LlmTimeoutError(LlmError):
    """大模型请求超时。"""


class LlmResponseError(LlmError):
    """大模型返回内容无效。"""


class LlmOutputTruncatedError(LlmResponseError):
    """大模型输出因长度限制被截断。"""


class LlmToolCallError(LlmError):
    """大模型工具调用失败或超出轮数。"""


class TiaEnvironmentError(ScdwError):
    """TIA Portal、PublicAPI 或 pythonnet 环境不可用。"""


class TiaSessionError(ScdwError):
    """TIA Portal 会话未初始化或会话操作失败。"""


class ProjectOperationError(ScdwError):
    """TIA 工程创建、保存或关闭失败。"""


class BlockImportError(ScdwError):
    """程序块导入失败。"""


class CompileOperationError(ScdwError):
    """PLC 编译操作失败。"""
