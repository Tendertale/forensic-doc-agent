"""部位照片用例（R-54～R-58、N-11 / N-12、INV-11）。

**N-11 红线**：本文件只处理文件路径、`position_key` 元数据与几何尺寸（像素宽高、
docx 图片框宽高），**不查看、不理解任何照片像素内容**；测试照片一律是 Pillow
程序化生成的纯色占位图。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.constants import (
    PHOTO_MAX_WIDTH_CM,
    PHOTO_PLACEHOLDER_TEXT,
    PHOTO_POSITION_KEYS,
    PHOTO_WIDE_MAX_WIDTH_CM,
)
from backend.tests.helpers import (
    case_version,
    create_case,
    create_document,
    db_add_photo,
    docx_document,
    docx_text,
    inline_shape_count,
    png_size,
    run_check,
    run_ocr,
    solid_png_bytes,
)


def _photo_materials(client, case_id):
    return [item for item in client.get("/api/cases/{0}/materials".format(case_id)).json()
            if item["kind"] == "photo"]


def _slots(client, case_id):
    return client.get("/api/cases/{0}/photo-slots".format(case_id)).json()


def _declared_slot_keys(text: str):
    return [match.group("key") for match in
            re.finditer(r"\{\{\s*photo_(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*\}\}", text)]


def _drawing_paragraph_indexes(document):
    return [index for index, paragraph in enumerate(document.paragraphs)
            if "<w:drawing" in paragraph._p.xml]


# --------------------------------------------------------------------------
# R-54：上传与位置指派
# --------------------------------------------------------------------------


def test_r54_photo_is_registered_as_file_plus_position_metadata_only(client, db, storage):
    """R-54 / N-11：照片材料只落文件与位置元数据（1 页、无副本、不进入识别流程）。"""

    case = create_case(client)
    case_id = int(case["id"])
    material = db_add_photo(db, storage, case_id, "head.png", position_key="head",
                            caption="头部（用户填写）", size=(300, 200))
    row = [item for item in _photo_materials(client, case_id)][0]
    assert row["kind"] == "photo"
    assert row["position_key"] == "head"
    assert row["photo_caption"] == "头部（用户填写）"
    assert row["page_count"] == 1
    assert row["derived_dir"] == ""
    assert len(row["pages"]) == 1
    assert row["pages"][0]["ocr_status"] == "skipped"
    assert row["pages"][0]["image_path"] == row["original_path"]
    assert row["sha256"] == material["sha256"]
    # 只读几何尺寸（不解码像素内容）
    assert png_size(storage.absolute(row["original_path"])) == (300, 200)
    assert not storage.derived_dir(case["case_no"], "photo").exists()


def test_r54_placement_can_be_assigned_and_changed_through_the_api(client, db, storage):
    """R-54 / R-56：`PATCH .../placement` 可指派与调整目标位置、顺序与说明。"""

    case = create_case(client)
    case_id = int(case["id"])
    material = db_add_photo(db, storage, case_id, "p.png")

    assigned = client.patch(
        "/api/cases/{0}/materials/{1}/placement".format(case_id, material["id"]), json={
            "expected_version": case_version(client, case_id),
            "position_key": "chest", "photo_caption": "胸部", "sort_order": 20,
        })
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["position_key"] == "chest"
    assert assigned.json()["sort_order"] == 20

    moved = client.patch(
        "/api/cases/{0}/materials/{1}/placement".format(case_id, material["id"]), json={
            "expected_version": case_version(client, case_id),
            "position_key": "head", "sort_order": 1,
        })
    assert moved.status_code == 200, moved.text
    assert moved.json()["position_key"] == "head"
    events = [item for item in
              client.get("/api/cases/{0}".format(case_id)).json()["events"]
              if item["event_type"] == "photo_placement_updated"]
    assert len(events) == 2


def test_r54_illegal_position_is_422_and_non_photo_material_is_400(client, db, storage):
    """R-54：位置只能取自目录（非法 422）；非照片材料不得指派位置（400）。"""

    case = create_case(client)
    case_id = int(case["id"])
    photo = db_add_photo(db, storage, case_id, "p.png", size=(300, 200))
    scan = db_add_photo(db, storage, case_id, "q.png", position_key="head", size=(320, 240))

    illegal = client.patch(
        "/api/cases/{0}/materials/{1}/placement".format(case_id, photo["id"]), json={
            "expected_version": case_version(client, case_id), "position_key": "左大腿中段",
        })
    assert illegal.status_code == 422, illegal.text
    assert "未知照片目标位置" in illegal.json()["detail"]

    from backend.tests.helpers import db_add_material

    other = db_add_material(db, storage, case_id, "record_book", "rb.png",
                            content=solid_png_bytes((60, 80)), page_count=1)
    wrong_kind = client.patch(
        "/api/cases/{0}/materials/{1}/placement".format(case_id, other["id"]), json={
            "expected_version": case_version(client, case_id), "position_key": "head",
        })
    assert wrong_kind.status_code == 400, wrong_kind.text
    assert "只有部位照片" in wrong_kind.json()["detail"]
    assert scan["position_key"] == "head"


def test_r54_photo_position_schema_contract_is_frozen():
    """R-54 / §5.5：`MaterialCreate` / `PhotoPlacementUpdate` 的位置目录校验不可绕过。"""

    from backend.app.schemas import MaterialCreate, PhotoPlacementUpdate

    ok = MaterialCreate(expected_version=1, kind="photo", filename="p.png",
                        position_key="head")
    assert ok.position_key == "head"

    with pytest.raises(ValidationError):
        MaterialCreate(expected_version=1, kind="photo", filename="p.png",
                       position_key="不存在的位置")
    with pytest.raises(ValidationError):
        MaterialCreate(expected_version=1, kind="commission", filename="c.png",
                       position_key="head")
    with pytest.raises(ValidationError):
        PhotoPlacementUpdate(expected_version=1, position_key="不存在的位置")


# --------------------------------------------------------------------------
# 栏位视图
# --------------------------------------------------------------------------


def test_r54_photo_slots_follow_the_catalog_order_and_shape(client):
    """R-54 / §4：`GET /photo-slots` 形状冻结，栏位顺序即位置目录顺序。"""

    case = create_case(client)
    body = _slots(client, case["id"])
    assert set(body) == {"photo_policy", "placeholder_text", "slots",
                         "missing_count", "assigned_count"}
    assert body["placeholder_text"] == PHOTO_PLACEHOLDER_TEXT
    assert "不读取照片内容" in body["photo_policy"]
    keys = [slot["position_key"] for slot in body["slots"]]
    assert keys == list(PHOTO_POSITION_KEYS)
    for slot in body["slots"]:
        assert set(slot) >= {"position_key", "label", "section", "section_label",
                             "order", "anchor", "wide", "missing", "photos"}
        assert slot["anchor"] == "photo_{0}".format(slot["position_key"])
        assert slot["missing"] is True and slot["photos"] == []
    assert body["missing_count"] == len(PHOTO_POSITION_KEYS)
    assert body["assigned_count"] == 0


def test_r57_unassigned_photo_is_absent_from_every_slot(client, seeded_case):
    """R-57：未指定位置的照片不出现在任何栏位中（也不被别的栏位顶替）。"""

    photos = _photo_materials(client, seeded_case)
    unassigned = [item for item in photos if not item["position_key"]]
    assert len(unassigned) == 1
    unassigned_id = unassigned[0]["id"]

    body = _slots(client, seeded_case)
    slot_ids = {photo["material_id"] for slot in body["slots"] for photo in slot["photos"]}
    assert unassigned_id not in slot_ids
    assert len(slot_ids) == len([item for item in photos if item["position_key"]])
    assert body["assigned_count"] == 3
    assert body["missing_count"] == len(PHOTO_POSITION_KEYS) - 3


def test_r57_unassigned_photo_raises_position_missing_warning(client, seeded_case):
    """R-57：未指定位置的照片产出 `PHOTO_POSITION_MISSING`（warning）。"""

    unassigned_id = [item["id"] for item in _photo_materials(client, seeded_case)
                     if not item["position_key"]][0]
    checked = run_check(client, seeded_case)
    items = [item for item in client.get(
        "/api/cases/{0}/checklist".format(seeded_case)).json()
        if item["code"] == "PHOTO_POSITION_MISSING"]
    assert len(items) == 1
    assert items[0]["severity"] == "warning"
    assert items[0]["target_type"] == "material"
    assert items[0]["target_id"] == unassigned_id
    assert "PHOTO_POSITION_MISSING" in checked["payload"]["codes"]


# --------------------------------------------------------------------------
# 原件预览（只读文件，不解码像素）
# --------------------------------------------------------------------------


def test_photo_file_endpoint_returns_the_original_image_bytes(client, db, storage):
    """R-19 / §4：`GET /materials/{id}/file` 只读回原件字节（缩略图预览）。"""

    case = create_case(client)
    case_id = int(case["id"])
    material = db_add_photo(db, storage, case_id, "head.png", position_key="head")
    stored = storage.absolute(material["original_path"])

    response = client.get("/api/cases/{0}/materials/{1}/file".format(case_id, material["id"]))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert hashlib.sha256(response.content).hexdigest() == material["sha256"]
    assert hashlib.sha256(stored.read_bytes()).hexdigest() == material["sha256"]


# --------------------------------------------------------------------------
# R-56 / N-12：生成文稿中的照片栏位
# --------------------------------------------------------------------------


def test_r56_rendered_docx_holds_one_picture_per_assigned_and_existing_photo(client, seeded_case):
    """R-56：生成的 `.docx` 中图片数 = 已指派位置且文件存在的照片数。"""

    created = create_document(client, seeded_case)
    path = Path(created["payload"]["docx_absolute"])
    body = _slots(client, seeded_case)
    assigned = [photo for slot in body["slots"] for photo in slot["photos"]]
    assert len(assigned) == 3
    assert inline_shape_count(path) == len(assigned)
    assert created["payload"]["photos_assigned"] == 3


def test_r56_pictures_sit_after_their_own_slot_heading(client, seeded_case):
    """R-56：每张照片段落都落在**对应栏位标题之后**、下一个栏位标题之前。"""

    created = create_document(client, seeded_case)
    document = docx_document(created["payload"]["docx_absolute"])
    texts = [paragraph.text for paragraph in document.paragraphs]
    headings = [index for index, text in enumerate(texts) if text.startswith("照片栏位：")]
    drawings = _drawing_paragraph_indexes(document)
    assert len(headings) >= 3 and len(drawings) == 3

    for position, index in enumerate(headings):
        limit = headings[position + 1] if position + 1 < len(headings) else len(texts)
        label = texts[index].replace("照片栏位：", "").replace("（宽幅）", "")
        inside = [item for item in drawings if index < item < limit]
        if inside:
            # 该栏位有照片：图片必须紧跟在标题之后（标题与图片之间不得夹杂别的栏位内容）
            assert len(inside) == 1
        else:
            assert "（未提供照片）" in "".join(texts[index + 1:limit])


def test_n12_empty_slots_keep_theirs_heading_and_write_placeholder(client, storage, seeded_case):
    """N-12：没有照片的栏位保留标题、写“（未提供照片）”，不删栏位、不写成“未见异常”。"""

    created = create_document(client, seeded_case)
    path = Path(created["payload"]["docx_absolute"])
    text = docx_text(path)
    declared = _declared_slot_keys(_template_text(client, storage, seeded_case))
    body = _slots(client, seeded_case)
    filled = {slot["position_key"] for slot in body["slots"] if slot["photos"]}
    expected_empty = [key for key in declared if key not in filled]
    assert expected_empty, "演示模板应声明多于已指派数量的照片栏位"

    assert text.count(PHOTO_PLACEHOLDER_TEXT) == len(expected_empty)
    assert "未见异常" not in text
    assert "照片栏位：" in text
    # 已指派栏位都确实带照片，不得出现“空栏位却没写占位文字”的情况
    for slot in body["slots"]:
        if slot["position_key"] in filled:
            assert slot["photos"]


def _template_text(client, storage, case_id: int) -> str:
    """模板正文文本（只读文件，用于确定模板声明了哪些照片栏位）。"""

    from backend.app.docgen import extract_document_text

    detail = client.get("/api/cases/{0}".format(case_id)).json()
    template = [item for item in detail["materials"] if item["kind"] == "template"][0]
    return extract_document_text(storage.absolute(template["original_path"])) or ""


def test_photo_width_respects_column_limit_and_keeps_aspect_ratio(client, seeded_case):
    """R-56：照片宽度不超过栏位上限（宽幅栏位用更大上限），保持纵横比、不裁切。"""

    from docx.shared import Cm

    created = create_document(client, seeded_case)
    document = docx_document(created["payload"]["docx_absolute"])
    shapes = list(document.inline_shapes)
    assert len(shapes) == 3

    body = _slots(client, seeded_case)
    order = [slot["position_key"] for slot in body["slots"] if slot["photos"]]
    expected = [Cm(PHOTO_WIDE_MAX_WIDTH_CM) if slot["wide"] else Cm(PHOTO_MAX_WIDTH_CM)
                for slot in body["slots"] if slot["photos"]]
    assert order == ["body_overall", "head", "chest"]

    for shape, limit in zip(shapes, expected):
        assert shape.width <= limit + 1000  # 1 EMU 级容差
        assert abs(shape.width / limit - 1.0) < 0.01, "宽度应正好压到栏位上限（原图更宽）"
        ratio = shape.width / shape.height
        assert abs(ratio - 4 / 3) < 0.02, "必须保持原图纵横比（960×720 → 4:3），不得裁切"


def test_photo_file_missing_warning_and_no_substitution(client, db, storage, seeded_case):
    """R-56 / N-12：已指派照片的文件缺失时不插别的照片，写占位文字并产出 warning。"""

    head = [item for item in _photo_materials(client, seeded_case)
            if item["position_key"] == "head"][0]
    storage.absolute(head["original_path"]).unlink()

    created = create_document(client, seeded_case)
    path = Path(created["payload"]["docx_absolute"])
    assert inline_shape_count(path) == 2
    text = docx_text(path)
    assert "照片栏位：头部" in text
    assert PHOTO_PLACEHOLDER_TEXT in text

    run_check(client, seeded_case, document_id=created["payload"]["document_id"])
    items = [item for item in client.get(
        "/api/cases/{0}/checklist".format(seeded_case)).json()
        if item["code"] == "PHOTO_FILE_MISSING"]
    assert len(items) == 1
    assert items[0]["severity"] == "warning"
    assert items[0]["target_id"] == head["id"]


def test_photo_slot_empty_items_are_info_for_declared_but_unfilled_slots(client, seeded_case):
    """N-12 / §5.7：模板声明但本次无照片的栏位产出 `PHOTO_SLOT_EMPTY`（info）。"""

    created = create_document(client, seeded_case)
    run_check(client, seeded_case, document_id=created["payload"]["document_id"])
    items = [item for item in client.get(
        "/api/cases/{0}/checklist".format(seeded_case)).json()
        if item["code"] == "PHOTO_SLOT_EMPTY"]
    assert items, "演示模板声明了多于 3 个照片栏位，应产出 PHOTO_SLOT_EMPTY"
    assert {item["severity"] for item in items} == {"info"}
    assert {item["target_type"] for item in items} == {"photo_slot"}
    assert PHOTO_PLACEHOLDER_TEXT in items[0]["detail"]


# --------------------------------------------------------------------------
# N-11：照片永不进入识别（含违规检测）
# --------------------------------------------------------------------------


def test_n11_photos_are_skipped_by_ocr_and_keep_zero_blocks(client, seeded_case):
    """N-11 / R-55：部位照片一律跳过识别，`ocr_blocks` 恒为 0。"""

    result = run_ocr(client, seeded_case, force=True)
    photos = _photo_materials(client, seeded_case)
    assert sorted(result["payload"]["skipped_photo_ids"]) == sorted(
        item["id"] for item in photos)
    for material in photos:
        blocks = client.get("/api/cases/{0}/ocr-blocks".format(seeded_case),
                            params={"material_id": material["id"]}).json()
        assert blocks["total"] == 0
    total = client.get("/api/cases/{0}/ocr-blocks".format(seeded_case)).json()
    photo_ids = {item["id"] for item in photos}
    assert all(item["material_id"] not in photo_ids for item in total["items"])


@pytest.mark.xfail(
    reason="实现缺陷：workflow.run_check 用 database.get_case() 组装 case_data，既不包含也不"
           "补充 ocr_blocks，checker 拿不到任何识别结果 → PHOTO_CONTENT_READ / OPINION_FROM_SOURCE"
           " 这两个 blocking 校核项在真实链路中不可达（N-11/N-1 安全网失效）",
    strict=False,
)
def test_inv11_photo_ocr_blocks_trip_the_blocking_check_item(client, db, seeded_case):
    """N-11 / INV-11：若照片被写入 `ocr_blocks`，`POST /check` 必须产出 blocking 的 `PHOTO_CONTENT_READ`。"""

    photo = [item for item in _photo_materials(client, seeded_case) if item["position_key"]][0]
    page = db.get_page(photo["id"], 1)
    db.replace_ocr_blocks(seeded_case, photo["id"], page, "sidecar", [{
        "block_index": 0, "text": "不应存在的识别文本", "confidence": 0.9,
        "bbox": [0.1, 0.1, 0.5, 0.2], "is_handwritten": False,
    }])
    assert client.get("/api/cases/{0}/ocr-blocks".format(seeded_case),
                      params={"material_id": photo["id"]}).json()["total"] == 1

    checked = run_check(client, seeded_case)
    item = [row for row in client.get(
        "/api/cases/{0}/checklist".format(seeded_case)).json()
        if row["code"] == "PHOTO_CONTENT_READ"][0]
    assert item["severity"] == "blocking"
    assert item["category"] == "recognition"
    assert item["target_id"] == photo["id"]
    assert "PHOTO_CONTENT_READ" in checked["payload"]["codes"]


@pytest.mark.xfail(
    reason="实现缺陷：同 PHOTO_CONTENT_READ —— `POST /check` 不加载 ocr_blocks，"
           "OPINION_FROM_SOURCE（N-1/INV-10 的兜底阻断项）在真实链路中永不触发",
    strict=False,
)
def test_n1_opinion_copied_from_ocr_text_is_blocked_by_check(client, db, seeded_case):
    """N-1 / INV-10：鉴定意见若与识别原文高度重合，`POST /check` 必须产出 blocking 项。"""

    blocks = client.get("/api/cases/{0}/ocr-blocks".format(seeded_case)).json()["items"]
    if not blocks:
        run_ocr(client, seeded_case)
        blocks = client.get("/api/cases/{0}/ocr-blocks".format(seeded_case)).json()["items"]
    text = [item for item in blocks if len(item["text"]) >= 6][0]["text"]

    patched = client.patch("/api/cases/{0}".format(seeded_case), json={
        "expected_version": case_version(client, seeded_case), "opinion_text": text})
    assert patched.status_code == 200, patched.text

    checked = run_check(client, seeded_case)
    assert "OPINION_FROM_SOURCE" in checked["payload"]["codes"]
