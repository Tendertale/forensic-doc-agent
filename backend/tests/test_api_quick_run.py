"""快速通道端点 `POST /api/quick-run` 的用例（完全离线）。

需求依据：

* **R-59～R-63**（主流程透明化）：主流程一次调用跑完整条链路；材料类型与照片位置
  只按文件名与扩展名推断，推断结果可在结果里看到并在「案件记录」中修改；
  **任何单步失败不得丢弃已完成的结果**，PDF 导出失败只作为说明。
* **R-54～R-57 / N-11 / N-12**：部位照片只按用户指定位置放置，永不进入 OCR、
  提取或任何模型调用（照片 `ocr_blocks` 恒为 0）；未指定位置的照片不得进入文稿。
* **N-5**：空白一律「未记录」，本用例不生成「未见异常」。

硬性约束（与 `backend/tests/helpers.py` 一致）：

* 测试图片全部是 Pillow 程序化生成的**纯色占位图**（只有尺寸与颜色），
  **不查看、不理解任何照片像素内容**（N-11）；
* 不访问真实网络：OCR 使用离线 `sidecar` 引擎（结果由同目录同名 `.ocr.json` 提供）；
* 所有落盘路径都来自 `tmp_path` 存储根（`conftest.py` 注入），不写仓库的 `data/`、`storage/`。
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from backend.app import docgen
from backend.app.constants import DEFAULT_TEMPLATE_VERSION
from backend.app.quick import infer_material_kind, infer_position_key
from backend.tests.helpers import b64, inline_shape_count, solid_png_bytes

#: 用例里写死的案件编号（便于预置 derived/ 下的离线 sidecar OCR 结果）。
CASE_NO = "FY-2026-9001"

QUICK_RUN_KEYS = {"case_id", "case_no", "stage", "steps", "assignments", "summary",
                  "documents", "notes", "case_url"}
STEP_NAMES = ["materials", "preprocess", "ocr", "extract", "normalize", "render", "check"]
SUMMARY_KEYS = {"material_count", "field_count", "injury_count", "document_count",
                "blocking_count", "warning_count", "info_count", "unresolved_count"}


# --------------------------------------------------------------------------
# 程序化生成的占位素材（只有几何与颜色，没有任何真实内容：N-11）
# --------------------------------------------------------------------------


def jpeg_bytes(size: tuple = (320, 240), color: tuple = (180, 200, 220)) -> bytes:
    """生成一张**纯色**占位 JPEG（只含尺寸与颜色）。"""

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "JPEG")
    return buffer.getvalue()


def minimal_template_bytes() -> bytes:
    """用 `python-docx` 现场生成一份最小模板：两个标量锚点 + 一个照片栏位。"""

    from docx import Document

    document = Document()
    document.add_paragraph("案件编号：{{ case_no }}")
    document.add_paragraph("被检验对象：{{ subject_name }}")
    document.add_paragraph("{{ photo_leg_left }}")
    document.add_paragraph("（本模板由用例程序化生成，不代表任何真实案件）")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def record_book_blocks() -> Dict[str, Any]:
    """离线 sidecar 识别结果（与 `seed.demo_sidecar_blocks` 同构，全部为测试文本）。"""

    return {
        "engine": "sidecar",
        "blocks": [
            {"text": "姓名：测试对象", "confidence": 0.96, "column_hint": "姓名"},
            {"text": "检验时间：2026-01-02 09:30", "confidence": 0.95, "column_hint": "检验时间"},
            {"text": "尸长172cm", "confidence": 0.93, "is_handwritten": True, "column_hint": "尸长"},
            {"text": "左前臂擦伤3×2cm", "confidence": 0.90, "is_handwritten": True,
             "column_hint": "损伤"},
        ],
    }


def write_sidecar(storage, case_no: str, kind: str, stem: str, payload: Dict[str, Any]) -> Path:
    """把离线 OCR 结果写到 `derived/<kind>/<stem>_p1.ocr.json`（架构 §5.4 的约定路径）。"""

    target = storage.derived_dir(case_no, kind) / "{0}_p1.ocr.json".format(stem)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return target


def quick_files(*, with_template: bool = True) -> List[Dict[str, str]]:
    """一组上传文件：模板 docx + 记录本扫描页 + 两张纯色占位部位照片。"""

    files: List[Dict[str, str]] = []
    if with_template:
        files.append({"filename": "测试模板.docx", "media_type": "docx",
                      "content_base64": b64(minimal_template_bytes())})
    files.extend([
        {"filename": "记录本扫描页.png", "media_type": "png",
         "content_base64": b64(solid_png_bytes(size=(620, 880)))},
        {"filename": "左小腿.jpg", "media_type": "jpeg",
         "content_base64": b64(jpeg_bytes(color=(200, 190, 180)))},
        {"filename": "右前臂.jpg", "media_type": "jpeg",
         "content_base64": b64(jpeg_bytes(color=(190, 200, 210)))},
    ])
    return files


# --------------------------------------------------------------------------
# 一、材料类型推断（R-60：只看文件名与扩展名）
# --------------------------------------------------------------------------


def test_infer_material_kind_word_documents():
    """R-60：第一份 Word 文档按模板处理，其余按已审核报告处理。"""

    first = infer_material_kind("空白模板.docx", "docx", index=0, has_template=False)
    assert first == {
        "kind": "template",
        "kind_label": "报告模板",
        "reason": first["reason"],
        "inferred": True,
    }
    assert first["inferred"] is True and first["reason"]

    second = infer_material_kind("去年报告.docx", "docx", index=1, has_template=False)
    assert second["kind"] == "approved_report"
    assert second["kind_label"] == "已审核报告"

    legacy = infer_material_kind("旧格式模板.doc", "other", index=0, has_template=False)
    assert legacy["kind"] == "template"

    after_template = infer_material_kind("补充模板.docx", "docx", index=1, has_template=True)
    assert after_template["kind"] == "approved_report"


def test_infer_material_kind_pdf_by_filename_then_extension():
    """R-60：PDF 先看文件名关键词，关键词不命中时按扩展名默认记录本并注明理由。"""

    commission = infer_material_kind("委托书.pdf", "pdf")
    assert commission["kind"] == "commission"
    assert commission["kind_label"] == "委托书"

    commission_en = infer_material_kind("commission_scan.pdf", "pdf")
    assert commission_en["kind"] == "commission"

    record = infer_material_kind("尸表检验记录.pdf", "pdf")
    assert record["kind"] == "record_book"
    assert record["kind_label"] == "记录本"

    default = infer_material_kind("扫描件001.pdf", "pdf")
    assert default["kind"] == "record_book"
    assert "按扩展名默认按记录本处理" in default["reason"]


def test_infer_material_kind_images_and_others():
    """R-60 / R-55：图片命中部位关键词才是照片，否则先按扫描页；json/其它按补充资料。"""

    photo = infer_material_kind("左小腿.jpg", "jpeg")
    assert photo["kind"] == "photo"
    assert photo["kind_label"] == "部位照片"

    scan = infer_material_kind("扫描页001.png", "png")
    assert scan["kind"] == "record_book"
    assert "未匹配部位关键词" in scan["reason"]
    assert "请在案件记录里改类型" in scan["reason"]

    text_scan = infer_material_kind("记录本.png", "png")
    assert text_scan["kind"] == "record_book"

    supplement = infer_material_kind("案件补充说明.json", "json")
    assert supplement["kind"] == "supplement"

    other = infer_material_kind("未知材料.bin", "other")
    assert other["kind"] == "supplement"
    assert other["kind_label"] == "补充资料"


# --------------------------------------------------------------------------
# 二、照片位置推断（R-60：只看文件名；不臆断左右，N-4）
# --------------------------------------------------------------------------


@pytest.mark.parametrize("filename, expected", [
    ("左小腿.jpg", "leg_left"),
    ("右小腿.jpg", "leg_right"),
    ("左下肢.jpg", "leg_left"),
    ("右手掌.jpg", "hand_right"),
    ("右足踝.jpg", "foot_right"),
    ("左脚.jpg", "foot_left"),
    ("左上臂.jpg", "arm_left"),
    ("右前臂.jpg", "arm_right"),
    ("头部.jpg", "head"),
    ("面部.jpg", "face"),
    ("颈项部.jpg", "neck"),
    ("胸部.jpg", "chest"),
    ("腹部.jpg", "abdomen"),
    ("腰背部.jpg", "back"),
    ("腰臀部.jpg", "waist_hip"),
    ("会阴部.jpg", "perineum"),
    ("全身照.jpg", "body_overall"),
])
def test_infer_position_key_matches_catalog(filename, expected):
    """R-60：部位关键词 → `PHOTO_POSITION_KEYS` 中的栏位；左右由「左/右」决定。"""

    result = infer_position_key(filename)
    assert result["matched"] is True
    assert result["inferred"] is True
    assert result["position_key"] == expected
    assert result["position_label"]
    assert result["reason"]


def test_infer_position_key_specific_before_broad():
    """R-60：由具体到宽泛——「腰背部」归背部，「小腿」归下肢，不被更宽泛的字抢走。"""

    back = infer_position_key("腰背部.jpg")
    assert back["position_key"] == "back"

    leg = infer_position_key("左小腿.jpg")
    assert leg["position_key"] == "leg_left"

    hand = infer_position_key("右手掌.jpg")
    assert hand["position_key"] == "hand_right"
    assert "右" in hand["reason"]

    # 未注明左右侧：**不推测左右**（N-4），位置留空交给 R-57 的阻断提示，绝不占位到某一侧
    guess = infer_position_key("小腿.jpg")
    assert guess["position_key"] == ""
    assert guess["position_label"] == ""
    assert guess["matched"] is False
    assert "未注明左右侧" in guess["reason"]
    assert "不推测左右" in guess["reason"]


def test_infer_position_key_unmatched_returns_other():
    """R-60 / R-57：无命中 → other + matched False（调用方据此留空位置）。"""

    result = infer_position_key("影像001.jpg")
    assert result["position_key"] == "other"
    assert result["position_label"] == "其他部位"
    assert result["matched"] is False
    assert result["inferred"] is True


# --------------------------------------------------------------------------
# 三、端到端：一次调用跑完整条链路（R-59～R-63）
# --------------------------------------------------------------------------


def test_quick_run_end_to_end(client, db, storage):
    """R-63 / R-54～R-57 / N-11：上传 → 结果，响应键齐全且数字自洽，照片零识别块。"""

    # 预置记录本扫描页的离线 sidecar 识别结果（与预处理副本同名同主名，架构 §5.4）
    write_sidecar(storage, CASE_NO, "record_book", "记录本扫描页", record_book_blocks())

    body = {
        "files": quick_files(),
        "formats": ["docx"],
        "case_no": CASE_NO,
        "commission_unit": "某测试单位",
        "subject_name": "测试对象",
        "note": "快速通道离线用例",
    }
    response = client.post("/api/quick-run", json=body)
    assert response.status_code == 200, response.text
    data = response.json()

    # ① 响应键固定（架构 §4）
    assert set(data) == QUICK_RUN_KEYS
    assert data["case_no"] == CASE_NO
    assert data["case_url"] == "/api/cases/{0}".format(data["case_id"])
    assert data["stage"] == "check_document"
    assert [step["step"] for step in data["steps"]] == STEP_NAMES
    assert all(set(step) == {"step", "ok", "detail"} for step in data["steps"])
    assert all(step["ok"] is True for step in data["steps"]), data["steps"]
    assert data["steps"][0]["detail"] == "登记 4 份材料（推断 4）"
    assert set(data["summary"]) == SUMMARY_KEYS

    case_id = int(data["case_id"])

    # ② 自动归类：模板 / 记录本 / 两张部位照片，且都标注为「按文件名推断」
    assignments = data["assignments"]
    assert [item["filename"] for item in assignments] == [
        "测试模板.docx", "记录本扫描页.png", "左小腿.jpg", "右前臂.jpg"]
    assert [item["kind"] for item in assignments] == [
        "template", "record_book", "photo", "photo"]
    assert [item["kind_label"] for item in assignments] == [
        "报告模板", "记录本", "部位照片", "部位照片"]
    assert all(item["kind_inferred"] is True for item in assignments)
    assert all(item["kind_reason"] for item in assignments)

    photos = assignments[2:]
    assert [item["position_key"] for item in photos] == ["leg_left", "arm_right"]
    assert [item["position_label"] for item in photos] == ["左下肢", "右上肢"]
    assert all(item["position_inferred"] is True for item in photos)
    assert assignments[0]["position_key"] == "" and assignments[0]["position_inferred"] is False

    # ③ N-11 / R-55：照片不产生任何识别块，页状态为 skipped
    materials = db.list_materials(case_id)
    photo_materials = [item for item in materials if item["kind"] == "photo"]
    assert len(photo_materials) == 2
    for material in photo_materials:
        assert db.list_ocr_blocks(case_id, int(material["id"]))["items"] == []
        assert material["pages"][0]["ocr_status"] == "skipped"

    # ④ 记录本扫描页确实进了识别流程（离线 sidecar）
    assert db.list_ocr_blocks(case_id)["total"] == len(record_book_blocks()["blocks"])

    # ⑤ 生成的 Word 真实存在，且正文没有残留占位符（R-5）
    assert len(data["documents"]) == 1
    entry = data["documents"][0]
    assert entry["doc_status"] == "internal_draft"
    assert entry["has_docx"] is True
    assert entry["filename_docx"] == "{0}-report-v1.docx".format(CASE_NO)
    assert entry["download_urls"]["docx"] == "/api/documents/{0}/download?fmt=docx".format(entry["id"])

    document = db.get_document(int(entry["id"]))
    docx_path = storage.absolute(document["docx_path"])
    assert docx_path.is_file()
    text = docgen.extract_document_text(str(docx_path))
    assert "{{" not in text and "}}" not in text
    assert "{%" not in text and "%}" not in text
    assert CASE_NO in text and "测试对象" in text  # 模板确实被渲染
    assert "未见异常" not in text  # N-5：不得把空白写成“未见异常”
    assert inline_shape_count(docx_path) == 1  # R-56：照片按栏位插入了一张

    # ⑥ formats=["docx"] 时不要求 PDF，也不产生 PDF 失败说明
    assert entry["has_pdf"] is False
    assert entry["filename_pdf"] == ""
    assert entry["download_urls"]["pdf"] == ""
    assert not any("PDF" in note for note in data["notes"])

    # ⑦ 摘要数字与实际库内数据自洽
    summary = data["summary"]
    assert summary["material_count"] == len(materials) == 4
    assert summary["field_count"] == len(db.list_fields(case_id))
    assert summary["injury_count"] == len(db.list_injuries(case_id))
    assert summary["document_count"] == len(db.list_documents(case_id)) == 1
    assert summary["unresolved_count"] >= 1
    checklist = db.list_checklist(case_id)
    assert summary["blocking_count"] == sum(
        1 for item in checklist if item["severity"] == "blocking" and item["status"] == "open")
    assert summary["warning_count"] == sum(
        1 for item in checklist if item["severity"] == "warning")
    assert summary["info_count"] == sum(1 for item in checklist if item["severity"] == "info")

    # ⑧ 说明文案：自动归类只依据文件名（N-11），且已提供模板时不再提示内置模板
    assert any("自动归类仅依据文件名" in note for note in data["notes"])
    assert not any("未提供模板" in note for note in data["notes"])


def test_quick_run_without_template_uses_builtin_template(client, db, storage):
    """R-63：未提供模板时使用内置演示模板，并在 `notes` 里说明（材料类型仍按文件名推断）。"""

    response = client.post("/api/quick-run", json={
        "files": quick_files(with_template=False),
        "formats": ["docx"],
        "case_no": "FY-2026-9002",
    })
    assert response.status_code == 200, response.text
    data = response.json()

    assert any("未提供模板" in note and DEFAULT_TEMPLATE_VERSION in note
               for note in data["notes"])
    # 没有 Word 文档 → 图片按扩展名默认当作记录本（不臆断为照片）
    assert [item["kind"] for item in data["assignments"]] == ["record_book", "photo", "photo"]

    # 内置模板已落到 templates/ 目录（`docgen.resolve_template` 的兜底文件名）
    templates_dir = Path(storage.templates_dir())
    assert (templates_dir / "report_template.docx").is_file() or \
        (templates_dir / "{0}.docx".format(DEFAULT_TEMPLATE_VERSION)).is_file()

    document = db.get_document(int(data["documents"][0]["id"]))
    text = docgen.extract_document_text(str(storage.absolute(document["docx_path"])))
    assert "{{" not in text and "{%" not in text
    assert "未提供照片" in text  # N-12：没有照片的栏位保留并写入占位提示


def test_quick_run_render_failure_is_reported_without_losing_results(client, db):
    """R-63：单步失败写进 `steps[].ok=false` + `detail`，已完成的结果（案件、材料）仍返回。

    这里故意上传一份**损坏的 .docx** 当模板：生成阶段必然失败，但接口仍返回 200，
    材料、识别与校核结果都不丢，且案件可以在「案件记录」里继续处理。
    """

    response = client.post("/api/quick-run", json={
        "files": [
            {"filename": "损坏模板.docx", "media_type": "docx",
             "content_base64": b64(b"this-is-not-a-real-docx")},
            {"filename": "左小腿.jpg", "media_type": "jpeg",
             "content_base64": b64(jpeg_bytes(color=(210, 205, 195)))},
        ],
        "formats": ["docx"],
        "case_no": "FY-2026-9004",
    })
    assert response.status_code == 200, response.text
    data = response.json()

    steps = {step["step"]: step for step in data["steps"]}
    assert list(steps) == STEP_NAMES
    # 材料登记、预处理（没有可预处理的材料）、识别、提取、整理、校核都照常完成
    for name in ("materials", "preprocess", "ocr", "extract", "normalize", "check"):
        assert steps[name]["ok"] is True, (name, steps[name])
    # 生成失败：如实报告，不抛 500，也不丢弃前面已完成的结果
    assert steps["render"]["ok"] is False
    assert "生成文稿失败" in steps["render"]["detail"]
    assert data["documents"] == []
    assert data["summary"]["material_count"] == 2
    assert db.get_case(int(data["case_id"])) is not None
    assert client.get(data["case_url"]).status_code == 200


def test_quick_run_empty_files_is_422_and_empty_content_is_400(client):
    """契约：`files` 为空 → 422（Pydantic）；`content_base64` 全空 → 400 + 中文 detail。"""

    empty = client.post("/api/quick-run", json={"files": []})
    assert empty.status_code == 422, empty.text
    assert "detail" in empty.json()

    no_content = client.post("/api/quick-run", json={
        "files": [{"filename": "左小腿.jpg", "media_type": "jpeg", "content_base64": ""}],
    })
    assert no_content.status_code == 400, no_content.text
    assert no_content.json()["detail"]


def test_quick_run_duplicate_case_no_is_409(client):
    """既有错误映射不变：重复案件编号 → 409（`DuplicateCase`）。"""

    body = {"files": quick_files(with_template=False), "formats": ["docx"],
            "case_no": "FY-2026-9003"}
    assert client.post("/api/quick-run", json=body).status_code == 200
    again = client.post("/api/quick-run", json=body)
    assert again.status_code == 409, again.text
    assert "已存在" in again.json()["detail"]
