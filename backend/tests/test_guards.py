"""静态与护栏用例（R-1 / R-58 / N-5 / N-9 / N-11 / N-12、密钥不落库）。

这些用例检查**可离线验证的静态产物**：无构建依赖的工作台（`frontend/static/`，
见 TODO-DL-3）与后端源码中的红线护栏。前端浏览器主流程本身属于 TODO-DL-3，
不做构建验证。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.app.constants import (
    MATERIAL_KINDS,
    OCR_MATERIAL_KINDS,
    PHOTO_MATERIAL_KIND,
    PHOTO_PLACEHOLDER_TEXT,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = PROJECT_ROOT / "backend" / "app"
STATIC_DIR = PROJECT_ROOT / "frontend" / "static"


# --------------------------------------------------------------------------
# R-1 / R-58：四步主流程与照片位置指派入口
# --------------------------------------------------------------------------


def test_r1_frontend_exposes_primary_flow_and_four_step_entries(client):
    """R-1（v2 修订）+ R-59：主界面＝「上传 → 结果」，四步入口保留在次级「案件记录」。

    修订原因：用户要求主流程 Transparent（只看到上传与结果），四步核对退为次级界面。
    因此这里不再要求「四步标签出现在 `GET /` 的 HTML 里」（那取决于挂载的是构建产物还是
    无构建工作台），而是要求**随包发布的前端源码**里两种入口都存在。
    """

    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")

    sources = [STATIC_DIR / "index.html", STATIC_DIR / "workbench.js"]
    sources.extend(sorted((PROJECT_ROOT / "frontend" / "src").rglob("*.vue")))
    joined = "\n".join(path.read_text(encoding="utf-8") for path in sources if path.is_file())

    for label in ("选择材料", "核对疑点", "查看文档", "确认修改"):
        assert label in joined, "四步入口缺失：" + label
    for label in ("整理", "上传", "结果"):
        assert label in joined, "主流程入口缺失：" + label
    assert "/api/quick-run" in joined or "quickRun" in joined


def test_built_frontend_is_not_stale():
    """若存在 `frontend/dist`，它必须不早于 `frontend/src/**` 与 `frontend/static/**`。

    背景：`main.py` 在 `frontend/dist` 存在时优先在 `/` 提供该构建产物。曾真实发生
    「改了源码没重新构建 → 用户看到的是旧界面」。这里让过期构建**显式失败**，
    而不是静默服务旧界面。
    """

    dist_index = PROJECT_ROOT / "frontend" / "dist" / "index.html"
    if not dist_index.is_file():
        pytest.skip("未构建：/ 由 frontend/static 无构建工作台提供（当前 VPS 部署形态）")

    newest = 0.0
    for folder in (PROJECT_ROOT / "frontend" / "src", STATIC_DIR):
        for path in folder.rglob("*"):
            if path.is_file():
                newest = max(newest, path.stat().st_mtime)

    assert dist_index.stat().st_mtime >= newest - 1.0, (
        "frontend/dist 早于前端源码：请重新执行 `cd frontend; npm run build`"
        "（否则 / 会提供过期界面）"
    )


def test_r58_workbench_declares_photo_position_entry_and_no_reading_policy():
    """R-58 / N-11：工作台提供照片位置指派入口，并明确标注“系统不读取照片内容”。"""

    source = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")
    assert "不读取照片内容" in source
    assert "/placement" in source            # PATCH .../placement
    assert "photo-slots" in source           # GET .../photo-slots
    assert "photo_positions" in source       # 位置只能取自 /api/config 的目录
    assert "未提供照片" in source


def test_n5_workbench_never_presents_a_blank_column_as_normal():
    """N-5：工作台只在“禁止/不得”语境中提及“未见异常”，空白一律显示“未记录”。"""

    source = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")
    assert "未记录" in source
    hits = [line for line in source.splitlines() if "未见异常" in line]
    assert hits
    for line in hits:
        assert re.search(r"(不|绝不|不会|不得)[^，。]*未见异常", line), line


# --------------------------------------------------------------------------
# N-9：材料只作为数据
# --------------------------------------------------------------------------


def test_n9_backend_never_dynamically_executes_content():
    """N-9：材料内容只作为数据；后端不出现 `eval(`/`exec(`/`os.system(` 等动态执行。"""

    offenders = []
    for path in sorted(APP_DIR.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for pattern in ("exec(", "eval(", "os.system(", "__import__("):
            if pattern in source:
                offenders.append("{0}: {1}".format(path.name, pattern))
    assert offenders == []


def test_n9_subprocess_is_confined_to_libreoffice_export():
    """N-9 / R-25：唯一的子进程调用是 LibreOffice PDF 导出（`docgen.py`），不执行材料内容。"""

    users = sorted(path.name for path in APP_DIR.rglob("*.py")
                   if "subprocess" in path.read_text(encoding="utf-8"))
    assert users == ["docgen.py"]


# --------------------------------------------------------------------------
# 密钥只走请求头（AGENTS.md 硬性约束 9）
# --------------------------------------------------------------------------


def test_secret_header_is_never_persisted_and_required_by_the_api(client, app):
    """约束 9：`X-DeepSeek-API-Key` 只从请求头读取，不落库、不写源码常量。"""

    database_source = (APP_DIR / "database.py").read_text(encoding="utf-8")
    assert "X-DeepSeek-API-Key" not in database_source
    assert "api_key" not in database_source

    rejected = client.post("/api/ai/verify", json={})
    assert rejected.status_code == 400
    assert "X-DeepSeek-API-Key" in rejected.json()["detail"]

    database_bytes = Path(app.state.db_path).read_bytes()
    assert b"X-DeepSeek-API-Key" not in database_bytes
    assert b"api_key" not in database_bytes
    assert b"sk-" not in database_bytes


# --------------------------------------------------------------------------
# 常量级红线（N-11 / N-12）
# --------------------------------------------------------------------------


def test_n11_constants_keep_photos_out_of_every_ocr_path():
    """N-11：`photo` 是材料类型但不是 OCR 材料类型（识别流程的常量级护栏）。"""

    assert PHOTO_MATERIAL_KIND == "photo"
    assert "photo" in MATERIAL_KINDS
    assert "photo" not in OCR_MATERIAL_KINDS
    assert set(OCR_MATERIAL_KINDS) == {"commission", "record_book", "supplement"}


def test_n12_placeholder_text_is_explicit_and_not_a_normal_finding():
    """N-12：空照片栏位写“（未提供照片）”，不得写“未见异常”。"""

    assert PHOTO_PLACEHOLDER_TEXT == "（未提供照片）"
    assert "未见异常" not in PHOTO_PLACEHOLDER_TEXT
    assert "未提供照片" in PHOTO_PLACEHOLDER_TEXT


def test_r52_acceptance_notes_are_documented_as_todo_downloads():
    """R-52 / R-53 / TODO-DL-6/7：演示数据明确标注，真实样本与识别率不作承诺。"""

    todo = (PROJECT_ROOT / "docs" / "TODO_DOWNLOADS.md").read_text(encoding="utf-8")
    for code in ("TODO-DL-1", "TODO-DL-2", "TODO-DL-3", "TODO-DL-6", "TODO-DL-7"):
        assert code in todo
    assert "不承诺识别率" in todo or "不承诺" in todo
