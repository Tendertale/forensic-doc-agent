"""W5 测试公共装置（`docs/IMPLEMENTATION_PLAN.md` §3.2 P2-F）。

隔离要求（任务书硬性约束）：

* 数据库与存储根**一律落在 `tmp_path`**：`create_app(db_path=..., storage_root=...,
  seed_data=False)`，绝不写仓库的 `data/`、`storage/`、`storage/smoke`；
* `seed_data=False` 后由用例显式 `seed_demo_case` 建演示案件，避免真实种子数据污染断言；
* 用例全部离线：OCR 只用 `sidecar` 引擎，不访问真实网络。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.main import create_app  # noqa: E402
from backend.app.seed import seed_demo_case  # noqa: E402


@pytest.fixture()
def app(tmp_path):
    """tmp_path 隔离的应用实例（不注入演示数据）。"""

    return create_app(
        db_path=str(tmp_path / "cases.db"),
        storage_root=str(tmp_path / "storage"),
        seed_data=False,
    )


@pytest.fixture()
def client(app):
    """`fastapi.testclient.TestClient`（离线，不监听端口）。"""

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def db(app):
    """`Database` 实例（落库断言用）。"""

    return app.state.db


@pytest.fixture()
def storage(app):
    """`CaseStorage` 实例（存储根指向 tmp_path）。"""

    return app.state.storage


@pytest.fixture()
def storage_root(app) -> Path:
    return Path(app.state.storage_root)


@pytest.fixture()
def seeded_case(db, storage) -> int:
    """演示案件：委托书 + 记录本 + sidecar OCR + 模板 + 4 张纯色占位照片。"""

    return seed_demo_case(db, storage, index=1)
