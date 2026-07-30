"""Create workflow_analysis.json from one SCDW run directory."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from scdw.common.paths import LOGS_DIR
from scdw.common.workflow_analysis import write_workflow_analysis


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", nargs="?", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir or max((path for path in LOGS_DIR.iterdir() if path.is_dir()), key=lambda path: path.stat().st_mtime)
    print(write_workflow_analysis(run_dir, args.output))


if __name__ == "__main__":
    main()
