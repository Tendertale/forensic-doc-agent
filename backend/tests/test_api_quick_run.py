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


# ==========================================================================
# 四、照片栏位兜底（R-56 / N-8 / N-11 / N-12）：模板没有 `{{ photo_* }}` 锚点时
#
# **设计选择（语义冻结，二选一中的“自动追加为默认行为”）：**
# `docgen.render_document` 在**工作副本**里没有任何 `{{ photo_` 锚点、而上下文里确有
# “已指派位置”的照片时，自动向工作副本追加标准「部位照片」区（18 个栏位）后再渲染，
# 模板原件一个字节都不改（N-8）。追加成功后照片必然进入文稿，因此 `checker` 的
# `PHOTO_SLOT_MISSING` **不再产出**；只有当照片确实无处安放（生成失败 / 追加异常 /
# 模板与输出同一路径 / 模板只声明了别的栏位）时，该 code 才会如实产出 warning。
# ==========================================================================


def template_without_photo_anchors_bytes() -> bytes:
    """现场生成一份**完全没有**照片栏位锚点的自建模板（只有两个标量锚点）。"""

    from docx import Document

    document = Document()
    document.add_paragraph("案件编号：{{ case_no }}")
    document.add_paragraph("被检验对象：{{ subject_name }}")
    document.add_paragraph("（本模板由用例程序化生成：没有任何部位照片栏位声明）")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _photo_materials_of(db, case_id: int) -> List[Dict[str, Any]]:
    return [item for item in db.list_materials(case_id) if item["kind"] == "photo"]


def test_quick_run_appends_standard_photo_section_when_template_has_no_anchor(
        client, db, storage):
    """R-56 / N-8 / N-12：无锚点自建模板 + 1 张已指派照片 → 照片不再被静默丢弃。

    断言（含两种设计中的“自动追加为默认行为”这一支的语义）：

    * 200，生成的 `.docx` 恰有 1 张图片（修复前 `inline_shapes == 0`）；
    * 正文含 18 个照片栏位标题行与 17 处「（未提供照片）」占位（有照片的栏位是图片）；
    * `notes` 里有明确的追加说明；模板原件**逐字节未改动**；
    * 校核清单里**不再**出现 `PHOTO_SLOT_MISSING`（追加后照片确实进了文稿）；
    * 照片 `ocr_blocks == 0`（N-11）。
    """

    import hashlib

    from backend.app.constants import PHOTO_POSITION_KEYS, PHOTO_PLACEHOLDER_TEXT

    case_no = "FY-2026-9101"
    template_bytes = template_without_photo_anchors_bytes()
    body = {
        "files": [
            {"filename": "自建模板.docx", "media_type": "docx",
             "content_base64": b64(template_bytes)},
            {"filename": "左小腿.jpg", "media_type": "jpeg",
             "content_base64": b64(jpeg_bytes(color=(198, 188, 176)))},
        ],
        "formats": ["docx"],
        "case_no": case_no,
    }
    response = client.post("/api/quick-run", json=body)
    assert response.status_code == 200, response.text
    data = response.json()
    case_id = int(data["case_id"])

    # ① 归类：第一份 Word 文档按模板处理；照片按文件名推断到 leg_left
    assert [item["kind"] for item in data["assignments"]] == ["template", "photo"]
    assert data["assignments"][1]["position_key"] == "leg_left"

    # ② 生成结果：1 张照片进了文稿（修复前这里是 0），且标准照片区被追加
    entry = data["documents"][0]
    document = db.get_document(int(entry["id"]))
    docx_path = storage.absolute(document["docx_path"])
    assert docx_path.is_file()
    assert inline_shape_count(docx_path) == 1, "照片必须按栏位插入文稿（R-56）"

    text = docgen.extract_document_text(str(docx_path))
    assert "{{" not in text and "}}" not in text and "{%" not in text
    assert "未见异常" not in text  # N-5/N-12：空白绝不写成“未见异常”
    assert text.count("照片栏位：") == len(PHOTO_POSITION_KEYS) == 18
    assert text.count(PHOTO_PLACEHOLDER_TEXT) == len(PHOTO_POSITION_KEYS) - 1
    assert "部位照片" in text
    assert "照片栏位：左下肢" in text  # 有照片的栏位：图片插在该标题之后

    # ③ notes 必须明确说明“系统追加了标准照片栏位”（R-59～R-63：绝不静默）
    assert any("模板未声明照片栏位" in note and "追加" in note for note in data["notes"]), \
        data["notes"]
    assert any("N-8" in note for note in data["notes"]), data["notes"]

    # ④ N-8：模板原件逐字节未改动（sha256 与字节双重比对）
    template = [item for item in db.list_materials(case_id) if item["kind"] == "template"][0]
    template_path = storage.absolute(template["original_path"])
    assert template_path.read_bytes() == template_bytes
    assert hashlib.sha256(template_path.read_bytes()).hexdigest() == template["sha256"]
    assert hashlib.sha256(template_path.read_bytes()).hexdigest() == \
        hashlib.sha256(template_bytes).hexdigest()

    # ⑤ 追加成功后校核清单不再报 `PHOTO_SLOT_MISSING`（语义见模块级注释与本用例 docstring）
    codes = {item["code"] for item in db.list_checklist(case_id)}
    assert "PHOTO_SLOT_MISSING" not in codes, sorted(codes)

    # ⑥ N-11：照片不产生任何识别块，仍被记录为“已跳过”
    photos = _photo_materials_of(db, case_id)
    assert len(photos) == 1
    assert db.list_ocr_blocks(case_id, int(photos[0]["id"]))["items"] == []


def test_photo_section_append_is_idempotent_and_never_touches_the_template(tmp_path):
    """N-8 / 幂等：同一模板连续渲染两次都只插 1 张照片、只追加一次标准照片区。

    直接走 `docgen.render_document`：`RenderResult.notes` 每次都说明“已追加”，而模板
    原件字节两次渲染后完全一致（追加只发生在渲染用的工作副本上）。
    """

    template = tmp_path / "自建模板.docx"
    template.write_bytes(template_without_photo_anchors_bytes())
    before = template.read_bytes()

    photo = tmp_path / "左小腿.jpg"
    photo.write_bytes(jpeg_bytes(color=(201, 191, 179)))

    context = {
        "case_no": "FY-2026-9199",
        "subject_name": "测试对象",
        "photo_slots": {"leg_left": {"position_key": "leg_left", "label": "左下肢",
                                     "photos": [{"path": str(photo), "material_id": 1}]}},
    }

    first = docgen.render_document(str(template), context, tmp_path / "out1.docx")
    second = docgen.render_document(str(template), context, tmp_path / "out2.docx")

    assert template.read_bytes() == before, "模板原件绝不能被改动（N-8）"
    for result in (first, second):
        assert result.notes and any("追加" in note for note in result.notes), result.notes
        assert result.unresolved == []
        assert inline_shape_count(result.output_path) == 1
        text = docgen.extract_document_text(result.output_path)
        # 只追加一次：18 个栏位标题，而不是 36 个（幂等）
        assert text.count("照片栏位：") == 18
        # 标准照片区标题行只出现一次（模板正文里也可能出现「部位照片」字样，故按整行计数）
        assert [line.strip() for line in text.splitlines()].count("部位照片") == 1


def test_template_with_declared_slot_is_not_appended_again(client, db, storage):
    """幂等 / N-12：模板自己声明了照片栏位时，绝不追加标准照片区、也不重复插照片。"""

    case_no = "FY-2026-9102"
    response = client.post("/api/quick-run", json={
        "files": [
            {"filename": "有栏位模板.docx", "media_type": "docx",
             "content_base64": b64(minimal_template_bytes())},
            {"filename": "左小腿.jpg", "media_type": "jpeg",
             "content_base64": b64(jpeg_bytes(color=(205, 195, 185)))},
        ],
        "formats": ["docx"],
        "case_no": case_no,
    })
    assert response.status_code == 200, response.text
    data = response.json()

    document = db.get_document(int(data["documents"][0]["id"]))
    docx_path = storage.absolute(document["docx_path"])
    assert inline_shape_count(docx_path) == 1
    text = docgen.extract_document_text(str(docx_path))
    # 模板已有 `{{ photo_leg_left }}`：没有追加任何标准照片区
    assert "照片栏位：" not in text
    assert "部位照片" not in text
    assert not any("追加" in note for note in data["notes"]), data["notes"]
    codes = {item["code"] for item in db.list_checklist(int(data["case_id"]))}
    assert "PHOTO_SLOT_MISSING" not in codes, sorted(codes)


def test_quick_run_reports_photo_not_inserted_when_append_fails(client, db, storage,
                                                                monkeypatch):
    """R-63 / N-8：自动追加失败时**不静默**——`notes` 写明“照片未插入文稿”，校核也如实报。

    这里把 `docgen.append_photo_section` 打成必然抛异常：渲染照常完成（不抛 500），
    生成的文稿里没有栏位、也没有照片，说明与校核项都必须如实反映这一点。
    """

    from backend.app.constants import PHOTO_POSITION_KEYS  # noqa: F401  （说明栏位口径）

    def _boom(document, keys=None):  # noqa: ANN001 - 模拟追加失败
        raise RuntimeError("模拟追加失败：磁盘不可写")

    monkeypatch.setattr(docgen, "append_photo_section", _boom)

    case_no = "FY-2026-9103"
    template_bytes = template_without_photo_anchors_bytes()
    response = client.post("/api/quick-run", json={
        "files": [
            {"filename": "自建模板.docx", "media_type": "docx",
             "content_base64": b64(template_bytes)},
            {"filename": "左小腿.jpg", "media_type": "jpeg",
             "content_base64": b64(jpeg_bytes(color=(190, 200, 205)))},
        ],
        "formats": ["docx"],
        "case_no": case_no,
    })
    assert response.status_code == 200, response.text
    data = response.json()
    case_id = int(data["case_id"])

    # 生成步骤本身仍然成功（追加失败不得让整条链路失败），但照片确实没进去
    steps = {step["step"]: step for step in data["steps"]}
    assert steps["render"]["ok"] is True, steps["render"]
    document = db.get_document(int(data["documents"][0]["id"]))
    docx_path = storage.absolute(document["docx_path"])
    assert inline_shape_count(docx_path) == 0

    # notes 如实写明，而不是静默丢弃
    assert any("照片未插入文稿" in note for note in data["notes"]), data["notes"]

    # 校核：照片无法进入文稿 → `PHOTO_SLOT_MISSING`（format/warning）
    items = [item for item in db.list_checklist(case_id)
             if item["code"] == "PHOTO_SLOT_MISSING"]
    assert items, "照片无处安放时必须产出 PHOTO_SLOT_MISSING"
    assert items[0]["category"] == "format" and items[0]["severity"] == "warning"
    assert items[0]["target_type"] == "photo_slot"

    # N-8：即便追加失败，模板原件也没有被改动
    template = [item for item in db.list_materials(case_id) if item["kind"] == "template"][0]
    assert storage.absolute(template["original_path"]).read_bytes() == template_bytes


def test_photo_slot_missing_code_is_distinct_from_photo_slot_empty(tmp_path):
    """§5.7：`PHOTO_SLOT_MISSING`（format/warning）与 `PHOTO_SLOT_EMPTY`（format/info）分界。

    * 有已指派位置的照片 + 模板/文稿里都没有该栏位 → `PHOTO_SLOT_MISSING`；
    * 模板声明了栏位（锚点或文稿标题行） → 不产出该 code（由 `PHOTO_SLOT_EMPTY` 负责空栏位）。
    """

    from backend.app.checker import check_case

    photo_path = tmp_path / "左小腿.jpg"
    photo_path.write_bytes(jpeg_bytes(color=(210, 200, 190)))
    base = {
        "materials": [{"id": 7, "kind": "photo", "filename": "左小腿.jpg",
                       "position_key": "leg_left"}],
        "photo_slots": [{"position_key": "leg_left", "label": "左下肢", "photos": [
            {"material_id": 7, "path": str(photo_path), "filename": "左小腿.jpg",
             "sort_order": 1}]}],
    }

    # ① 模板没有锚点、文稿里也没有该栏位 → 产出（warning，指向 photo_slot）
    items = check_case(dict(base), template_text="案件编号：{{ case_no }}",
                       stored_text="案件编号：FY-2026-9104")
    hits = [item for item in items if item.code == "PHOTO_SLOT_MISSING"]
    assert len(hits) == 1
    assert (hits[0].category, hits[0].severity) == ("format", "warning")
    assert hits[0].target_type == "photo_slot"
    assert "{{ photo_leg_left }}" in hits[0].detail
    assert "不会被插入文稿" in hits[0].detail
    assert hits[0].source["position_key"] == "leg_left"

    # ② 模板声明了该栏位锚点 → 不产出
    declared = check_case(dict(base), template_text="照片栏位：左下肢\n{{ photo_leg_left }}",
                          stored_text="照片栏位：左下肢\n（未提供照片）")
    assert "PHOTO_SLOT_MISSING" not in {item.code for item in declared}

    # ③ 模板没有锚点，但渲染产物里确实有该栏位标题行（系统自动追加标准照片区）
    #    → 不产出（照片已经进得去）；这也正是“追加为默认行为”的语义
    appended = check_case(dict(base), template_text="案件编号：{{ case_no }}",
                          stored_text="部位照片\n照片栏位：左下肢\n（图片）\n照片栏位：右上肢\n（未提供照片）")
    assert "PHOTO_SLOT_MISSING" not in {item.code for item in appended}

