"""PLC 工程 XLSX 解析与模板生成。"""

from .reader import PLCProjectSpec, read_plc_project_xlsx

__all__ = ["PLCProjectSpec", "read_plc_project_xlsx"]
