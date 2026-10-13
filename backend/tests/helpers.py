"""W5 测试辅助函数（完全离线）。

硬性约束（与任务书一致）：

* 自测用的图片一律是 Pillow 程序化生成的**纯色占位图**，只含几何尺寸元数据；
  测试代码与测试人员**不查看、不理解任何照片像素内容**（N-11）；
* 不访问真实网络（OCR 用离线 `sidecar` 引擎，`llm` 相关只在用例内用
  `httpx.MockTransport`）；
* 所有落盘路径都来自 `tmp_path` 存储根（由 `conftest.py` 注入），
  绝不写仓库的 `data/`、`storage/`、`storage/smoke`。

材料登记的说明：`backend/app/routes.py::add_material` 目前**恒返回 422**
（`schemas.MaterialCreate` 已声明 `expected_version`，但端点先把它从请求体里 pop
掉再做模型校验）。缺陷用例见 `test_api_pipeline.py::test_post_materials_endpoint_*`；
其余用例的装置走契约里同样冻结的 `Database.add_material(...)`（第 5.2 节的模块接口），
从而让下游链路仍然可测。
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

# --------------------------------------------------------------------------
# 图像与 base64（只有几何尺寸，没有内容）
# --------------------------------------------------------------------------


def solid_png_bytes(size: tuple = (320, 240), color: tuple = (214, 232, 248)) -> bytes:
    """程序化生成一张**纯色**占位 PNG：只含宽高，不含任何照片内容（N-11）。"""

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


def b64(data: bytes) -> str:
    return base64.b64encode(bytes(data)).decode("ascii")


def png_size(path: str | Path) -> tuple:
    """只读图片头部记录的像素宽高（几何元数据，不解码像素内容）。"""

    from PIL import Image

    with Image.open(str(path)) as image:
        return int(image.width), int(image.height)


# --------------------------------------------------------------------------
# 案件
# --------------------------------------------------------------------------


def create_case(client, **overrides: Any) -> Dict[str, Any]:
    """建案；默认填好测试用基础信息。返回案件详情 dict。"""

    payload: Dict[str, Any] = {
        "commission_unit": "某测试单位",
        "commission_matter": "对测试对象进行尸表检验",
        "commission_date": "2026-01-01",
        "case_summary": "测试案情：材料仅用于离线测试。",
        "subject_name": "测试对象",
    }
    payload.update(overrides)
    response = client.post("/api/cases", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def case_version(client, case_id: int) -> int:
    """当前 `cases.version`（所有写操作的 `expected_version` 来源）。"""

    response = client.get("/api/cases/{0}".format(case_id))
    assert response.status_code == 200, response.text
    return int(response.json()["version"])


def get_case(client, case_id: int) -> Dict[str, Any]:
    response = client.get("/api/cases/{0}".format(case_id))
    assert response.status_code == 200, response.text
    return response.json()


def fields_by_key(client, case_id: int) -> Dict[str, Dict[str, Any]]:
    response = client.get("/api/cases/{0}/fields".format(case_id))
    assert response.status_code == 200, response.text
    return {row["field_key"]: row for row in response.json()}


# --------------------------------------------------------------------------
# 材料（HTTP 端点有缺陷 → 装置走冻结的 Database 接口）
# --------------------------------------------------------------------------


def http_add_material(client, case_id: int, kind: str, filename: str, *, content: bytes = b"",
                      media_type: str = "png", **extra: Any):
    """`POST /api/cases/{id}/materials`（保留原始响应，供缺陷用例断言）。"""

    body: Dict[str, Any] = {
        "expected_version": case_version(client, case_id),
        "kind": kind,
        "filename": filename,
        "media_type": media_type,
        "content_base64": b64(content) if content else "",
    }
    body.update(extra)
    return client.post("/api/cases/{0}/materials".format(case_id), json=body)


def db_add_material(db, storage, case_id: int, kind: str, filename: str, *,
                    content: bytes = b"", media_type: str = "png",
                    **extra: Any) -> Dict[str, Any]:
    """直接调用冻结的 `Database.add_material(case_id, payload, expected_version, storage)`。"""

    version = int((db.get_case(case_id) or {}).get("version") or 1)
    payload: Dict[str, Any] = {
        "kind": kind,
        "filename": filename,
        "media_type": media_type,
        "content_base64": b64(content) if content else "",
    }
    payload.update(extra)
    return db.add_material(case_id, payload, version, storage, actor="测试")


def db_add_photo(db, storage, case_id: int, filename: str, *, position_key: str = "",
                 caption: str = "", sort_order: int = 0, size: tuple = (320, 240)) -> Dict[str, Any]:
    """登记一张纯色占位部位照片（只写文件与位置元数据，不读内容：N-11）。"""

    return db_add_material(
        db, storage, case_id, "photo", filename,
        content=solid_png_bytes(size),
        position_key=position_key,
        photo_caption=caption,
        sort_order=sort_order,
    )


# --------------------------------------------------------------------------
# sidecar OCR
# --------------------------------------------------------------------------


def sidecar_payload(blocks: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """把简写块定义展开为 sidecar JSON（架构 5.4）。"""

    prepared: List[Dict[str, Any]] = []
    for index, item in enumerate(blocks):
        prepared.append(
            {
                "text": str(item.get("text") or ""),
                "confidence": float(item.get("confidence", 0.95)),
                "bbox": list(item.get("bbox") or [0.1, 0.10 + 0.06 * index, 0.7, 0.15 + 0.06 * index]),
                "is_handwritten": bool(item.get("is_handwritten", False)),
                "column_hint": str(item.get("column_hint") or ""),
            }
        )
    return {"engine": "sidecar", "blocks": prepared}


def upload_scan(client, db, storage, case_id: int, case_no: str, *, kind: str, filename: str,
                blocks: Sequence[Mapping[str, Any]], media_type: str = "png",
                page_count: int = 1, size: tuple = (620, 880)) -> Dict[str, Any]:
    """登记一份“扫描件”，并落下辨认副本与 sidecar OCR JSON（同目录同主名）。

    步骤：登记材料（纯色占位图像）→ `POST .../preprocess` 生成
    `derived/<kind>/<stem>_p1.png` → 写入 `derived/<kind>/<stem>_p1.ocr.json`。
    全程不读取图像内容。
    """

    material = db_add_material(
        db, storage, case_id, kind, filename,
        content=solid_png_bytes(size), media_type=media_type, page_count=page_count,
    )
    response = client.post(
        "/api/cases/{0}/materials/{1}/preprocess".format(case_id, material["id"]),
        json={"expected_version": case_version(client, case_id),
              "operations": ["autocontrast"], "page_number": 0},
    )
    assert response.status_code == 200, response.text

    stem = Path(filename).stem
    derived = storage.derived_dir(case_no, kind)
    derived.mkdir(parents=True, exist_ok=True)
    (derived / "{0}_p1.ocr.json".format(stem)).write_text(
        json.dumps(sidecar_payload(blocks), ensure_ascii=False), encoding="utf-8")
    return material


# --------------------------------------------------------------------------
# 阶段调用（都返回 StageResult.as_dict()）
# --------------------------------------------------------------------------


def run_ocr(client, case_id: int, **payload: Any) -> Dict[str, Any]:
    body = {"expected_version": case_version(client, case_id), "engine": "sidecar"}
    body.update(payload)
    response = client.post("/api/cases/{0}/ocr".format(case_id), json=body)
    assert response.status_code == 200, response.text
    return response.json()


def run_extract(client, case_id: int, **payload: Any) -> Dict[str, Any]:
    body = {"expected_version": case_version(client, case_id)}
    body.update(payload)
    response = client.post("/api/cases/{0}/extract".format(case_id), json=body)
    assert response.status_code == 200, response.text
    return response.json()


def run_normalize(client, case_id: int, **payload: Any) -> Dict[str, Any]:
    body = {"expected_version": case_version(client, case_id)}
    body.update(payload)
    response = client.post("/api/cases/{0}/normalize".format(case_id), json=body)
    assert response.status_code == 200, response.text
    return response.json()


def create_document(client, case_id: int, **payload: Any) -> Dict[str, Any]:
    body = {"expected_version": case_version(client, case_id), "formats": ["docx"]}
    body.update(payload)
    response = client.post("/api/cases/{0}/documents".format(case_id), json=body)
    assert response.status_code == 200, response.text
    return response.json()


def run_check(client, case_id: int, **payload: Any) -> Dict[str, Any]:
    body = {"expected_version": case_version(client, case_id)}
    body.update(payload)
    response = client.post("/api/cases/{0}/check".format(case_id), json=body)
    assert response.status_code == 200, response.text
    return response.json()


def workflow_extract(db, storage, case_id: int, **kwargs: Any) -> Dict[str, Any]:
    """走冻结的 `workflow.run_extraction`。

    `POST /api/cases/{id}/extract` 目前恒 409（`run_extraction` 在同一流程里对
    `expected_version` 做了两次 `_claim`，见缺陷报告），因此只能按下标接口调用；
    系统内部写用 `expected_version=0`（与 `backend/scripts/smoke.py` 相同口径）。
    """

    from backend.app.workflow import run_extraction

    kwargs.setdefault("expected_version", 0)
    return run_extraction(db, storage, case_id, **kwargs).as_dict()


def pipeline_case(client, db, storage, *, name: str = "李某某") -> Dict[str, Any]:
    """全链路装置：建案 → 材料（委托书/记录本/模板）→ 预处理 → OCR（sidecar）。"""

    case = prepare_full_case(client, db, storage, name=name)
    case_id = int(case["id"])
    run_ocr(client, case_id)
    return get_case(client, case_id)


# --------------------------------------------------------------------------
# 生成的 .docx（只做结构/文本级检查，不查看图片内容）
# --------------------------------------------------------------------------


def docx_document(path: str | Path):
    from docx import Document

    return Document(str(path))


def docx_text(path: str | Path) -> str:
    from backend.app.docgen import extract_document_text

    return extract_document_text(path) or ""


def docx_paragraph_texts(path: str | Path) -> List[str]:
    return [paragraph.text for paragraph in docx_document(path).paragraphs]


def inline_shape_count(path: str | Path) -> int:
    return len(docx_document(path).inline_shapes)


# --------------------------------------------------------------------------
# 常用 OCR 块集合（与 seed 演示数据同构，便于 API 级链路测试）
# --------------------------------------------------------------------------


def commission_blocks(name: str = "李某某") -> List[Dict[str, Any]]:
    return [
        {"text": "委托单位：某市公安局某分局", "confidence": 0.96, "column_hint": "委托单位"},
        {"text": "姓名：{0}".format(name), "confidence": 0.95, "column_hint": "姓名"},
        {"text": "委托事项：对{0}进行尸表检验".format(name), "confidence": 0.94, "column_hint": "委托事项"},
        {"text": "委托日期：2026-10-11", "confidence": 0.93, "column_hint": "委托日期"},
    ]


def record_book_blocks(name: str = "李某某") -> List[Dict[str, Any]]:
    return [
        {"text": "姓名：{0}".format(name), "confidence": 0.97, "column_hint": "姓名"},
        {"text": "检验时间：2026-10-12 09:30", "confidence": 0.95, "column_hint": "检验时间"},
        {"text": "尸长172cm", "confidence": 0.93, "is_handwritten": True, "column_hint": "尸长"},
        {"text": "背侧，紫红，指压褪色", "confidence": 0.90, "is_handwritten": True, "column_hint": "尸斑"},
        {"text": "尸僵全身大关节", "confidence": 0.92, "is_handwritten": True, "column_hint": "尸僵"},
        {"text": "左前臂擦伤3×2cm", "confidence": 0.88, "is_handwritten": True, "column_hint": "损伤"},
        {"text": "右小腿挫伤4×3cm", "confidence": 0.86, "is_handwritten": True, "column_hint": "损伤"},
        {"text": "其他一般所见：未见皮下出血", "confidence": 0.90, "is_handwritten": True,
         "column_hint": "其他一般所见"},
        {"text": "/", "confidence": 0.70, "is_handwritten": True, "column_hint": "衣着"},
    ]


def register_demo_template(db, storage, case_id: int) -> Dict[str, Any]:
    """把演示模板登记为案件的 `template` 材料（模板原件只读使用）。"""

    from backend.app.constants import DEFAULT_TEMPLATE_VERSION
    from backend.app.seed import ensure_demo_template

    path = ensure_demo_template(storage, DEFAULT_TEMPLATE_VERSION)
    return db_add_material(
        db, storage, case_id, "template", path.name,
        content=Path(path).read_bytes(), media_type="docx", page_count=0,
        clarity_note="程序化生成的演示模板（TODO-DL-6）",
    )


def prepare_full_case(client, db, storage, *, name: str = "李某某") -> Dict[str, Any]:
    """建一个含委托书 + 记录本（sidecar）+ 演示模板材料的完整可跑案件。"""

    case = create_case(client, subject_name=name)
    case_id = int(case["id"])
    case_no = str(case["case_no"])
    upload_scan(client, db, storage, case_id, case_no, kind="commission",
                filename="commission_scan.png", blocks=commission_blocks(name))
    upload_scan(client, db, storage, case_id, case_no, kind="record_book",
                filename="record_book_scan.png", blocks=record_book_blocks(name))
    register_demo_template(db, storage, case_id)
    return get_case(client, case_id)
