"""全链路与产物用例（R-5 / R-6 / R-8 / R-18 / R-19 / R-21 / R-24 / R-25 / R-26 / R-27 / R-28 /
R-33 / R-42 / R-46 / R-50 / R-51 / R-52、N-1 / N-2 / N-5 / N-8 / N-9 / N-11、INV-5）。

链路：材料 → OCR（sidecar）→ 提取 → 整理 → 生成 → 校核。

缺陷用例（`xfail`，详见交付报告「发现的实现缺陷」）：
* `POST /api/cases/{id}/materials` 恒 422（`routes.add_material` 先 pop `expected_version`，
  再用**已声明该字段**的 `MaterialCreate` 校验）；
* `POST /api/cases/{id}/extract` 恒 409（`workflow.run_extraction` 用同一个
  `expected_version` 做了两次 `_claim`）；
* `engine="paddle"` 不可用时静默回退 sidecar（200），未按 TODO-DL-2 返回 409；
* 生成 `pending_issue` 后正文仍含 `【待核对】`，与 §3.2 / R-46 冲突。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from backend.app.constants import DRAFT_MARKER
from backend.app.docgen import detect_libreoffice, docx_engine_status
from backend.tests.helpers import (
    case_version,
    create_case,
    create_document,
    db_add_material,
    db_add_photo,
    docx_document,
    docx_text,
    http_add_material,
    inline_shape_count,
    pipeline_case,
    record_book_blocks,
    run_check,
    run_normalize,
    run_ocr,
    solid_png_bytes,
    upload_scan,
    workflow_extract,
)


def _sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --------------------------------------------------------------------------
# 缺陷用例（保留断言，不改实现；见报告）
# --------------------------------------------------------------------------


@pytest.mark.xfail(
    reason="实现缺陷：routes.add_material 先 pop expected_version 再交给已声明该字段的 "
           "MaterialCreate 校验 → POST /materials 恒 422（可复现命令见报告）",
    strict=False,
)
def test_post_materials_accepts_expected_version_from_request_body(client):
    """§4 / R-12：`POST /materials` 的 `expected_version` 放在请求体，登记成功返回材料。"""

    case = create_case(client)
    response = http_add_material(client, case["id"], "commission", "c.png",
                                content=solid_png_bytes((120, 90)), page_count=1)
    assert response.status_code == 200, response.text
    material = response.json()
    assert material["kind"] == "commission"
    assert material["filename"] == "c.png"
    assert material["original_path"]


@pytest.mark.xfail(
    reason="实现缺陷：同上，MaterialCreate 已把 expected_version 声明为必填，"
           "查询参数形式同样被模型校验挡住 → 恒 422",
    strict=False,
)
def test_post_materials_accepts_expected_version_as_query_parameter(client):
    """§4 / R-12：`POST /materials` 亦可通过查询参数传 `expected_version`。"""

    case = create_case(client)
    response = client.post(
        "/api/cases/{0}/materials".format(case["id"]),
        params={"expected_version": case_version(client, case["id"])},
        json={"kind": "record_book", "filename": "r.png", "media_type": "png",
              "page_count": 1},
    )
    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "record_book"


@pytest.mark.xfail(
    reason="实现缺陷：workflow.run_extraction 先 claim(case_id, expected_version) 再 "
           "upsert_fields(..., expected_version) 二次占位 → POST /extract 恒 409",
    strict=False,
)
def test_post_extract_returns_stage_result(client, db, storage):
    """§4 / §5.10：`POST /cases/{id}/extract` 应返回 `StageResult`（提取字段与损伤）。"""

    case = pipeline_case(client, db, storage)
    response = client.post("/api/cases/{0}/extract".format(case["id"]), json={
        "expected_version": case_version(client, case["id"]),
    })
    assert response.status_code == 200, response.text
    payload = response.json()["payload"]
    assert payload["fields"] > 0
    assert payload["injuries"] == 2


@pytest.mark.xfail(
    reason="实现缺陷/契约冲突：ocr.resolve_engine 对不可用引擎回退 sidecar，"
           "engine=paddle 返回 200 且 payload.engine=sidecar，未按 TODO-DL-2 返回 409",
    strict=False,
)
def test_paddle_unavailable_returns_409_with_todo_dl_2(client, db, storage):
    """R-20 / TODO-DL-2：paddle 不可用时显式 409，detail 含 `TODO-DL-2`（不静默降级）。"""

    from backend.app.ocr import get_engine

    if get_engine("paddle").available():  # pragma: no cover - 本机未安装 paddleocr
        pytest.skip("本机已安装 paddleocr，跳过“不可用”分支")

    case = pipeline_case(client, db, storage)
    response = client.post("/api/cases/{0}/ocr".format(case["id"]), json={
        "expected_version": case_version(client, case["id"]), "engine": "paddle",
    })
    assert response.status_code == 409, response.text
    assert "TODO-DL-2" in response.json()["detail"]


@pytest.mark.xfail(
    reason="实现缺陷：docgen.build_context 只对 approved 清空 draft_marker，"
           "阻断项处理完毕后生成的 pending_issue 正文仍含【待核对】，与 §3.2/R-46 冲突",
    strict=False,
)
def test_r46_pending_issue_body_has_no_draft_marker(client, db, storage):
    """R-46 / §3.2：阻断项处理完毕后生成的待签发稿，正文不得再含草稿标记。"""

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    db.import_seed_rules()
    workflow_extract(db, storage, case_id)
    run_normalize(client, case_id)
    created = create_document(client, case_id)
    run_check(client, case_id, document_id=created["payload"]["document_id"])

    for item in client.get("/api/cases/{0}/checklist".format(case_id),
                           params={"status": "open"}).json():
        if item["severity"] == "blocking":
            response = client.patch("/api/checklist/{0}".format(item["id"]), json={
                "expected_version": case_version(client, case_id),
                "status": "waived", "note": "测试豁免：不阻断待签发稿验证",
            })
            assert response.status_code == 200, response.text

    pending = client.post("/api/cases/{0}/documents".format(case_id), json={
        "expected_version": case_version(client, case_id),
        "doc_status": "pending_issue", "formats": ["docx"],
    })
    assert pending.status_code == 200, pending.text
    text = docx_text(pending.json()["payload"]["docx_absolute"])
    assert DRAFT_MARKER not in text


# --------------------------------------------------------------------------
# 全链路（正常路径）
# --------------------------------------------------------------------------


def test_r5_r6_full_pipeline_from_materials_to_docx_and_checklist(client, db, storage):
    """R-5 / R-6 / R-8 / R-26 / R-52：材料 → OCR → 提取 → 整理 → 生成 → 校核全链路跑通。"""

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    case_no = case["case_no"]
    db.import_seed_rules()

    # OCR：委托书 4 块 + 记录本 9 块（sidecar 离线引擎）
    blocks = client.get("/api/cases/{0}/ocr-blocks".format(case_id)).json()
    assert blocks["total"] == 13

    extracted = workflow_extract(db, storage, case_id)
    assert extracted["ok"] is True
    assert extracted["payload"]["fields"] >= 8
    assert extracted["payload"]["injuries"] == 2
    assert extracted["payload"]["blocks"] == 13

    normalized = run_normalize(client, case_id)
    entries = {item["field_key"]: item for item in normalized["payload"]["results"]}
    assert entries["body_length"]["normalized_text"] == "尸长172 cm。"
    assert entries["body_length"]["applied_rule_codes"] == ["body_length_unit"]

    created = create_document(client, case_id)
    docx_path = Path(created["payload"]["docx_absolute"])
    assert docx_path.is_file()
    assert created["payload"]["doc_status"] == "internal_draft"
    assert created["payload"]["unresolved"] == []
    assert created["payload"]["engine"] in ("builtin", "docxtpl")

    text = docx_text(docx_path)
    assert "{{" not in text and "}}" not in text and "{%" not in text
    assert case_no in text
    assert "某测试单位" in text  # 案件基础信息栏
    assert "尸长172 cm" in text
    assert "左前臂见擦伤" in text and "右小腿见挫伤" in text
    assert "未记录" in text

    # R-2：委托书识别结果本身也保留在字段行里（原文与来源）
    fields = {row["field_key"]: row for row in
              client.get("/api/cases/{0}/fields".format(case_id)).json()}
    assert "某市公安局某分局" in fields["commission_unit"]["raw_text"]
    assert fields["commission_unit"]["source_material_id"]

    checked = run_check(client, case_id, document_id=created["payload"]["document_id"])
    codes = set(checked["payload"]["codes"])
    assert "FIELD_UNRESOLVED_CRITICAL" in codes  # 关键事实仍需人工核对（INV-2/R-28）
    assert "DOCUMENT_UNRESOLVED_PLACEHOLDER" not in codes
    assert "MATERIAL_MISSING" not in codes
    assert "FIELD_SOURCE_MISSING" not in codes  # 提取只写有来源的字段（R-22/§5.5）
    assert checked["payload"]["item_count"] > 0

    # R-22 / R-15：每个“明确记载”字段都必须能回溯到来源材料
    recorded = [row for row in
                client.get("/api/cases/{0}/fields".format(case_id)).json()
                if row["status"] == "recorded"]
    assert recorded
    assert all(row["source_material_id"] for row in recorded)

    listed = client.get("/api/cases/{0}/checklist".format(case_id)).json()
    assert len(listed) == checked["payload"]["item_count"]
    assert all(item["status"] == "open" for item in listed)
    stored = client.get("/api/cases/{0}".format(case_id)).json()
    assert stored["summary"]["injury_count"] == 2
    assert stored["summary"]["document_count"] == 1


def test_r5_generated_docx_opens_with_python_docx_and_has_no_placeholders(client, db, storage):
    """R-5 / R-8 / R-42：`.docx` 真实存在、可被 python-docx 打开、保留模板版式、无占位符残留。"""

    case = pipeline_case(client, db, storage)
    created = create_document(client, int(case["id"]))
    path = Path(created["payload"]["docx_absolute"])

    document = docx_document(path)
    assert document.paragraphs[0].text == "尸表检验记录"
    assert document.paragraphs[0].runs[0].font.size.pt == 18.0
    assert len(document.tables) == 1
    assert inline_shape_count(path) == 0

    text = docx_text(path)
    for marker in ("{{", "}}", "{%", "%}", "TODO", "XXXX"):
        assert marker not in text, marker


def test_n5_blank_clothing_column_is_written_as_not_recorded(client, db, storage):
    """N-5 / R-35 / INV-3：空白栏（衣着 `/`）在正文写“未记录”，绝不写“未见异常”。"""

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    workflow_extract(db, storage, case_id)
    fields = {row["field_key"]: row for row in
              client.get("/api/cases/{0}/fields".format(case_id)).json()}
    assert fields["clothing"]["status"] == "not_recorded"
    assert fields["clothing"]["normalized_text"] == "未记录"

    created = create_document(client, case_id)
    text = docx_text(created["payload"]["docx_absolute"])
    assert "衣着：未记录" in text
    assert "未见异常" not in text


def test_r33_missing_unit_keeps_value_through_the_api(client, db, storage):
    """R-33 / §5.6：`尸长172` 缺单位时经接口整理后仍保留原数值并要求确认。"""

    case = create_case(client)
    case_id = int(case["id"])
    upload_scan(client, db, storage, case_id, case["case_no"], kind="record_book",
                filename="rb.png", blocks=[{"text": "尸长172", "confidence": 0.9,
                                            "column_hint": "尸长"}])
    db.import_seed_rules()
    run_ocr(client, case_id)
    workflow_extract(db, storage, case_id)
    result = run_normalize(client, case_id)
    entry = [item for item in result["payload"]["results"]
             if item["field_key"] == "body_length"][0]
    assert entry["normalized_text"] == "尸长172"
    assert "cm" not in entry["normalized_text"]
    assert entry["requires_confirmation"] is True


# --------------------------------------------------------------------------
# 原件只读（R-19 / R-24 / N-8）
# --------------------------------------------------------------------------


def test_r19_r24_n8_render_and_preprocess_never_touch_originals(client, db, storage):
    """R-19 / R-24 / N-8：预处理与渲染只写副本，扫描原件与模板原件字节不变。"""

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    detail = client.get("/api/cases/{0}".format(case_id)).json()
    originals = {}
    for material in detail["materials"]:
        if material["original_path"]:
            originals[material["id"]] = _sha256(storage.absolute(material["original_path"]))
    assert len(originals) >= 3

    created = create_document(client, case_id)
    run_check(client, case_id, document_id=created["payload"]["document_id"])

    after = client.get("/api/cases/{0}".format(case_id)).json()
    for material in after["materials"]:
        if material["id"] in originals:
            assert _sha256(storage.absolute(material["original_path"])) == originals[material["id"]]

    docx_path = Path(created["payload"]["docx_absolute"])
    assert docx_path.parent == storage.documents_dir(case["case_no"])
    assert case["case_no"] in docx_path.name


# --------------------------------------------------------------------------
# 草稿 / 阻断（R-27 / R-28 / INV-5）
# --------------------------------------------------------------------------


def test_r27_internal_draft_is_allowed_and_marked_with_draft_marker(client, db, storage):
    """R-27 / R-46：存在疑点时仍可生成内部草稿，且正文带 `【待核对】` 标记。"""

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    workflow_extract(db, storage, case_id)
    created = create_document(client, case_id)
    assert created["payload"]["doc_status"] == "internal_draft"
    text = docx_text(created["payload"]["docx_absolute"])
    assert DRAFT_MARKER in text


def test_inv5_r28_pending_issue_is_blocked_by_open_blocking_items(client, db, storage):
    """INV-5 / R-28：存在 open 的 blocking 校核项时生成 `pending_issue` → 409。"""

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    workflow_extract(db, storage, case_id)
    checked = run_check(client, case_id)
    assert checked["payload"]["blocking_count"] > 0

    response = client.post("/api/cases/{0}/documents".format(case_id), json={
        "expected_version": case_version(client, case_id),
        "doc_status": "pending_issue", "formats": ["docx"],
    })
    assert response.status_code == 409, response.text
    assert "存在未解决的阻断项" in response.json()["detail"]
    assert not any(item["doc_status"] == "pending_issue"
                   for item in client.get("/api/cases/{0}/documents".format(case_id)).json())


def test_waived_blocking_items_unblock_pending_issue_generation(client, db, storage):
    """R-28 / R-46：把阻断项豁免后即可生成待签发稿（阻断判断与清单状态联动）。"""

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    workflow_extract(db, storage, case_id)
    run_check(client, case_id)
    for item in client.get("/api/cases/{0}/checklist".format(case_id),
                           params={"status": "open"}).json():
        if item["severity"] == "blocking":
            response = client.patch("/api/checklist/{0}".format(item["id"]), json={
                "expected_version": case_version(client, case_id),
                "status": "waived", "note": "测试豁免理由",
            })
            assert response.status_code == 200, response.text

    assert client.get("/api/cases/{0}/checklist".format(case_id),
                      params={"status": "open", "category": "source"}).status_code == 200
    blocked = [item for item in client.get("/api/cases/{0}/checklist".format(case_id),
                                           params={"status": "open"}).json()
               if item["severity"] == "blocking"]
    assert blocked == []
    response = client.post("/api/cases/{0}/documents".format(case_id), json={
        "expected_version": case_version(client, case_id),
        "doc_status": "pending_issue", "formats": ["docx"],
    })
    assert response.status_code == 200, response.text
    assert response.json()["payload"]["doc_status"] == "pending_issue"


# --------------------------------------------------------------------------
# 识别质量（R-21 / N-11）
# --------------------------------------------------------------------------


def test_r21_low_confidence_field_enters_the_checklist_as_warning(client, db, storage):
    """R-21 / R-45：识别置信度低的内容进入待核对清单（warning），且不作为结论。"""

    case = create_case(client)
    case_id = int(case["id"])
    upload_scan(client, db, storage, case_id, case["case_no"], kind="record_book",
                filename="rb.png", blocks=[
                    {"text": "颈部压退", "confidence": 0.48, "is_handwritten": True,
                     "column_hint": "尸斑"},
                    {"text": "尸长172cm", "confidence": 0.93, "is_handwritten": True,
                     "column_hint": "尸长"},
                ])
    run_ocr(client, case_id)
    workflow_extract(db, storage, case_id)
    fields = {row["field_key"]: row for row in
              client.get("/api/cases/{0}/fields".format(case_id)).json()}
    assert fields["livor_mortis"]["confidence"] == 0.48
    assert fields["livor_mortis"]["status"] == "unclear"

    checked = run_check(client, case_id)
    items = {item["code"]: item for item in checked["payload"]["items"]}
    assert items["OCR_LOW_CONFIDENCE"]["severity"] == "warning"
    assert items["OCR_LOW_CONFIDENCE"]["category"] == "recognition"
    rows = [row for row in client.get("/api/cases/{0}/checklist".format(case_id)).json()
            if row["code"] == "OCR_LOW_CONFIDENCE"]
    assert rows and str(0.48) in rows[0]["detail"]


def test_n11_run_ocr_skips_photos_and_photos_never_get_ocr_blocks(client, db, storage):
    """N-11 / R-55 / INV-11：`run_ocr` 跳过部位照片并报告 `skipped_photo_ids`。"""

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    db_add_photo(db, storage, case_id, "head.png", position_key="head", caption="头部",
                 size=(300, 200))
    db_add_photo(db, storage, case_id, "unassigned.png", size=(320, 240))

    result = run_ocr(client, case_id, force=True)
    payload = result["payload"]
    photos = [item for item in client.get("/api/cases/{0}/materials".format(case_id)).json()
              if item["kind"] == "photo"]
    assert len(payload["skipped_photo_ids"]) == len(photos) == 2
    for material in photos:
        blocks = client.get("/api/cases/{0}/ocr-blocks".format(case_id),
                            params={"material_id": material["id"]}).json()
        assert blocks["total"] == 0
        assert material["pages"][0]["ocr_status"] == "skipped"


def test_n11_photo_only_case_yields_no_fields_or_injuries(client, db, storage):
    """N-11 / INV-11：只有部位照片的案件不产生任何字段、损伤或识别结果。"""

    case = create_case(client)
    case_id = int(case["id"])
    db_add_photo(db, storage, case_id, "head.png", position_key="head")
    ocr = run_ocr(client, case_id)
    assert ocr["payload"]["material_ids"] == []
    assert len(ocr["payload"]["skipped_photo_ids"]) == 1

    extracted = workflow_extract(db, storage, case_id)
    assert extracted["payload"]["blocks"] == 0
    assert extracted["payload"]["injuries"] == 0
    # 建案同步的基础信息字段仍存在，但不得新增任何来自照片的字段
    keys = {row["field_key"] for row in client.get("/api/cases/{0}/fields".format(case_id)).json()}
    assert "body_length" not in keys


def test_n9_material_bytes_are_stored_verbatim_and_only_as_data(client, db, storage):
    """N-9：材料只作为数据保存（字节原样落盘、sha256 一致），不执行其中的任何内容。"""

    payload = "=CMD|'/c calc'!A1\nSub AutoOpen()\nEnd Sub".encode("utf-8")
    case = create_case(client)
    case_id = int(case["id"])
    material = db_add_material(db, storage, case_id, "supplement", "payload.txt",
                               content=payload, media_type="other")
    stored = storage.absolute(material["original_path"])
    assert stored.read_bytes() == payload
    assert material["sha256"] == hashlib.sha256(payload).hexdigest()

    # 该材料不会凭空产生字段：supplement 属于 OCR 类型，但没有 sidecar JSON → 识别 0 块
    run_ocr(client, case_id)
    blocks = client.get("/api/cases/{0}/ocr-blocks".format(case_id)).json()
    assert blocks["total"] == 0
    extracted = workflow_extract(db, storage, case_id)
    assert extracted["payload"]["blocks"] == 0


# --------------------------------------------------------------------------
# 下载 / PDF（R-50 / R-51 / R-25）
# --------------------------------------------------------------------------


def test_r51_download_filename_follows_case_no_doc_type_version(client, db, storage):
    """R-51：文件命名为 `案件编号-文档类型-版本`，草稿状态在文稿记录中可见。"""

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    created = create_document(client, case_id)
    document_id = created["payload"]["document_id"]

    response = client.get("/api/documents/{0}/download".format(document_id),
                          params={"fmt": "docx"})
    assert response.status_code == 200
    disposition = response.headers.get("content-disposition", "")
    assert "{0}-report-v1.docx".format(case["case_no"]) in disposition
    assert response.content[:2] == b"PK"

    missing_pdf = client.get("/api/documents/{0}/download".format(document_id),
                             params={"fmt": "pdf"})
    assert missing_pdf.status_code == 404
    assert client.get("/api/documents/{0}/download".format(document_id),
                      params={"fmt": "exe"}).status_code == 400


@pytest.mark.skipif(detect_libreoffice() is None, reason="本机未安装 LibreOffice，PDF 导出跳过")
def test_r25_pdf_export_produces_a_real_file(client, db, storage):
    """R-25 / R-50：LibreOffice 可用时导出 PDF 并记录到文稿（同版本）。"""

    case = pipeline_case(client, db, storage)
    case_id = int(case["id"])
    created = create_document(client, case_id, formats=["docx", "pdf"])
    pdf_relative = created["payload"]["pdf_path"]
    assert pdf_relative, created["detail"]
    pdf_path = Path(created["payload"]["pdf_absolute"])
    assert pdf_path.is_file()
    assert pdf_path.read_bytes()[:5] == b"%PDF-"
    assert pdf_path.parent == storage.documents_dir(case["case_no"])

    document = [item for item in client.get("/api/cases/{0}/documents".format(case_id)).json()][0]
    assert document["pdf_path"] == pdf_relative

    checked = run_check(client, case_id, document_id=created["payload"]["document_id"])
    assert "DOCUMENT_PDF_MISSING" not in set(checked["payload"]["codes"])


def test_docx_engine_status_reports_builtin_fallback_and_todo_dl_1(client):
    """TODO-DL-1 / R-24：`docxtpl` 未安装时如实报告 `builtin` 回退与 TODO 编号。"""

    status = docx_engine_status()
    if not status["docxtpl_available"]:  # pragma: no cover - 取决于本机依赖
        assert status["preferred"] == "builtin"
    assert status["todo"] == "TODO-DL-1"
    config = client.get("/api/config").json()
    assert config["engines"]["docx"]["todo"] == "TODO-DL-1"


# --------------------------------------------------------------------------
# 图像 / PDF 探测（R-18）
# --------------------------------------------------------------------------


def test_r18_imaging_probe_distinguishes_text_pdf_from_scanned_pdf(tmp_path):
    """R-18：区分原生文字 PDF 与扫描 PDF（有文字层 / 无文字层）。"""

    fitz = pytest.importorskip("fitz")
    from backend.app import imaging

    picture = tmp_path / "scan.png"
    picture.write_bytes(solid_png_bytes((240, 320)))

    image_only = tmp_path / "image_only.pdf"
    document = fitz.open()
    page = document.new_page(width=240, height=320)
    page.insert_image(fitz.Rect(0, 0, 240, 320), filename=str(picture))
    document.save(str(image_only))
    document.close()

    native_text = tmp_path / "native_text.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "委托书 尸长172cm")
    document.save(str(native_text))
    document.close()

    scanned = imaging.probe(str(image_only))[0]
    assert scanned.has_text_layer is False
    assert scanned.text_layer_chars == 0
    assert scanned.is_scanned is True

    native = imaging.probe(str(native_text))[0]
    assert native.has_text_layer is True
    assert native.text_layer_chars > 0
    assert native.is_scanned is False

    png = imaging.probe(str(picture))[0]
    assert png.has_text_layer is False
    assert png.is_scanned is True
    assert imaging.extract_pdf_text(str(picture)) == ""
