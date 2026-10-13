"""启动无构建前端工作台所需的后端服务（`docs/architecture.md` 第 9 节第 5 条）。

用法::

    python -m backend.scripts.serve --port 9765
    # 浏览器打开 http://127.0.0.1:9765/  → 返回工作台页面（含“部位照片”区块）

可选参数：`--host`（默认 127.0.0.1）、`--db`（数据库路径）、`--storage`（存储根目录）、
`--no-seed`（不写入演示案件）、`--reload`（开发热重载，需 uvicorn[standard]）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="法医尸表检验文书整理 Agent 后端服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=9765, help="监听端口（默认 9765）")
    parser.add_argument("--db", default="", help="数据库路径（默认 FORENSIC_DB_PATH 或 data/cases.db）")
    parser.add_argument("--storage", default="", help="存储根目录（默认 storage）")
    parser.add_argument("--no-seed", action="store_true", help="不写入演示案件")
    parser.add_argument("--reload", action="store_true", help="开发热重载")
    return parser


def main(argv: list | None = None) -> int:
    args = build_parser().parse_args(argv)

    import uvicorn

    from backend.app.main import create_app

    app = create_app(args.db or None, args.storage or None, seed_data=not args.no_seed)
    print("法医尸表检验文书整理 Agent 已启动：http://{0}:{1}/".format(args.host, args.port))
    print("接口自检：http://{0}:{1}/api/health".format(args.host, args.port))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
