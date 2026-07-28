"""项目内统一路径定义，避免业务模块依赖启动工作目录。"""
import os
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PACKAGE_DIR.parent
PROJECT_ROOT = SRC_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = DATA_DIR / "logs"
RAG_DATA_DIR = DATA_DIR / "rag"
RAG_TEMPLATES_DIR = RAG_DATA_DIR / "templates"
GENERATED_DIR = DATA_DIR / "generated"
RAG_GENERATED_DIR = GENERATED_DIR / "rag"
XML_ARTIFACTS_DIR = GENERATED_DIR / "xml_artifacts"
XML_ARTIFACT_TTL_HOURS = int(os.getenv("SCDW_XML_ARTIFACT_TTL_HOURS", "48"))
XLSX_DATA_DIR = DATA_DIR / "xlsx"
TIA_PROJECTS_DIR = PROJECT_ROOT / "assets" / "tia_projects"
TEST_PROJECTS_DIR = GENERATED_DIR / "test_projects"


def ensure_generated_dir() -> Path:
    """创建并返回 RAG 运行产物目录。"""
    RAG_GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    return RAG_GENERATED_DIR


def ensure_xml_artifacts_dir() -> Path:
    """Create and return the isolated XML Artifact workspace."""
    XML_ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    return XML_ARTIFACTS_DIR
