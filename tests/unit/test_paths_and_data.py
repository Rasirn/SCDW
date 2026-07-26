"""不依赖 TIA Portal 的基础路径与数据测试。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scdw.common.paths import PROJECT_ROOT, RAG_TEMPLATES_DIR, XLSX_DATA_DIR
from scdw.rag.retriever import TemplateLibrary
from scdw.xlsx.reader import read_plc_project_xlsx


class PathAndDataTests(unittest.TestCase):
    """验证不依赖当前工作目录的数据定位。"""

    def test_project_root_is_repository_root(self):
        self.assertEqual(PROJECT_ROOT, ROOT)

    def test_rag_templates_are_available(self):
        self.assertTrue(RAG_TEMPLATES_DIR.is_dir())
        TemplateLibrary.reset()
        self.assertGreaterEqual(len(TemplateLibrary.instance().list_all()), 16)

    def test_xlsx_data_directory_is_available(self):
        self.assertTrue(XLSX_DATA_DIR.is_dir())
        sample = XLSX_DATA_DIR / "PLC程序模板.xlsx"
        self.assertTrue(sample.is_file())
        spec = read_plc_project_xlsx(str(sample))
        self.assertGreater(len(spec.hardware), 0)


if __name__ == "__main__":
    unittest.main()
