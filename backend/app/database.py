"""SQLite 数据访问层：11 张表的 DDL、乐观锁、审计事件与聚合查询。

本模块是**唯一持久化入口**（`docs/architecture.md` 第 1 节）。其他模块不允许自行
打开数据库连接。

对应不变量（`docs/architecture.md` 第 2 节）：

* INV-1 `case_fields.field_key` 必须来自 `FIELD_INDEX`，`field_group` / `field_label` /
  `display_order` 由目录派生。
* INV-2 `unclear` 或 `review_state='pending'` 的关键字段构成阻断项（由 `checker` 判定，
  本模块提供 `unresolved` 口径与 `open_blocking_items`）。
* INV-3 `status='not_recorded'` 时 `normalized_text` 必须是“未记录”，且任何情况下
  不得包含“未见异常”。
* INV-4 损伤每条独立一行（本模块不做任何跨行合并）。
* INV-5 生成 `pending_issue` 文稿前检查 `open` 阻断项 → `BlockedByChecklist`。
* INV-6 事实字段被修正后，文稿 `doc_status` 变为 `superseded`，相关字段
  `review_state` 回到 `pending`。
* INV-7 `corrections.scope != 'case_only'` 时必须写入 `scope_value`，且不跨案件改写。
* INV-8 仅 `doc_status='approved'` 的文稿可被 `deliver` 引用。
* INV-9 任何写操作都先 `_claim`；`expected_version` 为 0 表示系统内部写（不校验但同样占位）。
* INV-10 `opinion_text` 只接受用户请求写入，本模块不从任何识别文本推导它。

字段取值优先级（**D11 决议**，由 `upsert_fields` 与 `_sync_case_fields` 共同实现）：

* `confirmed_value` 是**用户已确认的事实值**：来源只有三处——建案 / `PATCH /api/cases/{id}`
  的案件基础信息（`_sync_case_fields`，用户手填即视为已确认）、用户显式核对字段
  （`PATCH /fields/{key}`）、用户修正（`POST /corrections`）。
* 文稿与字段视图的取值顺序是 `confirmed_value → normalized_text → raw_text`
  （`docgen._field_value`）。因此**用户已确认值优先于识别结果**：材料识别不会静默改写
  用户手填的委托单位 / 姓名等关键事实（R-15 / R-45）。
* `upsert_fields`（**识别结果**路径）**绝不覆盖非空的 `confirmed_value`**：它只更新
  `raw_text`（识别原文）、`normalized_text`、来源列与状态列，把识别原文完整保留下来供
  人工比对；只有该字段行还没有确认值时，识别结果才允许写入 `confirmed_value`
  （即“没有用户值时由识别值顶替”）。
* 每一次“保留用户值、未采用识别值”都会留下痕迹：`upsert_fields` 写
  `case_events.event_type = 'field_value_priority'`（含字段键、用户值、识别原文），
  并在 `fields_extracted` 事件的 detail 中计数——**不静默丢弃识别结果**。

乐观锁归属（`docs/architecture.md` 第 4 节）：`/api/cases/**` 用 `cases.version`；
`/api/terminology-rules/**` 用 `terminology_rules.version`；文稿审核与清单处理用
**所属案件**的 `cases.version`。
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import fields as dataclass_fields, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from .constants import (
    CASE_STAGES,
    CASE_STAGE_LABELS,
    CHECK_ITEM_STATUSES,
    CHECK_SEVERITIES,
    CORRECTION_SCOPES,
    DEFAULT_SECTION_ORDER,
    DEFAULT_TEMPLATE_VERSION,
    DOC_STATUS_LABELS,
    DOC_STATUSES,
    FIELD_INDEX,
    FIELD_STATUSES,
    FIELD_STATUS_LABELS,
    MATERIAL_KINDS,
    MATERIAL_KIND_LABELS,
    PHOTO_MATERIAL_KIND,
    PHOTO_MAX_PER_POSITION,
    PHOTO_POSITION_DEFINITIONS,
    PHOTO_POSITION_INDEX,
    PHOTO_POSITION_KEYS,
    PHOTO_POSITION_LABELS,
    PHOTO_SECTION_LABELS,
    REVIEW_STATES,
    SEED_CASE_COUNT,
    STAGE_TRANSITIONS,
    TERMINOLOGY_SEED,
    TERM_RULE_DEFAULT_VERSION,
)

__all__ = [
    "VersionConflict",
    "DuplicateCase",
    "InvalidTransition",
    "BlockedByChecklist",
    "NotFound",
    "utc_now",
    "normalize_case_no",
    "Database",
]


# --------------------------------------------------------------------------
# 异常
# --------------------------------------------------------------------------


class VersionConflict(Exception):
    """乐观锁冲突：`expected_version` 与数据库中的版本不一致（HTTP 409）。"""


class DuplicateCase(Exception):
    """案件编号已存在（HTTP 409）。"""


class InvalidTransition(Exception):
    """状态流转或前置条件不满足（HTTP 409）。"""


class BlockedByChecklist(Exception):
    """存在未解决的阻断校核项：不能生成待签发稿，也不能把文稿置为已审核
    （HTTP 409，R-28/INV-5，D5 补强）。"""


class NotFound(Exception):
    """资源不存在（HTTP 404）。"""


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------


def utc_now() -> str:
    """统一时间戳：UTC、秒级精度、ISO 8601。"""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_case_no(value: str) -> str:
    """规范化案件编号：去首尾空白、内部空白折叠为单个连字符、字母转大写。

    只做形式规范化，不改变编号中的数字与顺序（R-48 追溯要求）。
    """

    text = " ".join(str(value or "").split())
    if not text:
        return ""
    return text.replace(" ", "-").upper()


def _json(value: Any) -> str:
    """把 Python 对象序列化为 UTF-8 JSON 文本（不转义中文）。"""

    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value: Any, default: Any) -> Any:
    """把 `*_json` 列解析为 Python 对象；解析失败时返回默认值。"""

    if value is None or value == "":
        return default() if callable(default) else default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default() if callable(default) else default


def _as_mapping(value: Any) -> Dict[str, Any]:
    """把 dataclass / Mapping / 普通对象统一转换为 dict。

    W2、W3 的模块返回 dataclass（`ExtractedField`、`CheckItem`、`NormalizationResult`），
    本模块只接受 dict，因此在此统一转换，避免各处重复 `asdict`。
    """

    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: getattr(value, item.name) for item in dataclass_fields(value)}
    data = getattr(value, "__dict__", None)
    if isinstance(data, dict):
        return {key: item for key, item in data.items() if not key.startswith("_")}
    raise TypeError("无法转换为字典：" + repr(type(value)))


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _float_or_none(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


# --------------------------------------------------------------------------
# 行转换：所有 `*_json` 列必须解析为 Python 对象（第 5.2 节注记）
# --------------------------------------------------------------------------


def _case_row(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    data["intake"] = _loads(data.pop("intake_json", "{}"), dict)
    data["section_order"] = _loads(data.pop("section_order_json", "[]"), list)
    data["stage_label"] = CASE_STAGE_LABELS.get(data.get("stage", ""), data.get("stage", ""))
    data["doc_status_label"] = DOC_STATUS_LABELS.get(data.get("doc_status", ""), data.get("doc_status", ""))
    return data


def _material_row(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    data["quality_flags"] = _loads(data.pop("quality_flags_json", "[]"), list)
    data["kind_label"] = MATERIAL_KIND_LABELS.get(data.get("kind", ""), data.get("kind", ""))
    return data


def _page_row(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    data["quality_flags"] = _loads(data.pop("quality_flags_json", "[]"), list)
    data["preprocessing"] = _loads(data.pop("preprocessing_json", "{}"), dict)
    data["is_scanned"] = _bool(data.get("is_scanned"))
    data["has_text_layer"] = _bool(data.get("has_text_layer"))
    data["text_layer_chars"] = int(data.get("text_layer_chars") or 0)
    return data


def _block_row(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    data["bbox"] = _loads(data.pop("bbox_json", "[]"), list)
    data["is_handwritten"] = _bool(data.get("is_handwritten"))
    data["confidence"] = float(data.get("confidence") or 0.0)
    return data


def _field_row(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    data["source_bbox"] = _loads(data.pop("source_bbox_json", "[]"), list)
    data["applied_rule_codes"] = _loads(data.pop("applied_rule_codes_json", "[]"), list)
    data["conflict"] = _loads(data.pop("conflict_json", "[]"), list)
    data["confidence"] = _float_or_none(data.get("confidence"))
    data["status_label"] = FIELD_STATUS_LABELS.get(data.get("status", ""), data.get("status", ""))
    return data


def _injury_row(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    data["source_bbox"] = _loads(data.pop("source_bbox_json", "[]"), list)
    data["applied_rule_codes"] = _loads(data.pop("applied_rule_codes_json", "[]"), list)
    data["confidence"] = _float_or_none(data.get("confidence"))
    data["status_label"] = FIELD_STATUS_LABELS.get(data.get("status", ""), data.get("status", ""))
    return data


def _rule_row(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    data["require_groups"] = _loads(data.pop("require_groups_json", "[]"), list)
    data["requires_confirmation"] = _bool(data.get("requires_confirmation"))
    data["active"] = _bool(data.get("active"))
    return data


def _document_row(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    data["unresolved"] = _loads(data.pop("unresolved_json", "[]"), list)
    data["doc_status_label"] = DOC_STATUS_LABELS.get(data.get("doc_status", ""), data.get("doc_status", ""))
    return data


def _checklist_row(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    data["source"] = _loads(data.pop("source_json", "{}"), dict)
    return data


def _correction_row(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    data["affected"] = _loads(data.pop("affected_json", "[]"), list)
    data["invalidated"] = _loads(data.pop("invalidated_json", "[]"), list)
    data["applied"] = _bool(data.get("applied"))
    return data


# --------------------------------------------------------------------------
# DDL
# --------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_no TEXT NOT NULL UNIQUE,
    stage TEXT NOT NULL DEFAULT 'intake',
    doc_status TEXT NOT NULL DEFAULT '',
    section_order_json TEXT NOT NULL DEFAULT '[]',
    opinion_text TEXT NOT NULL DEFAULT '',
    template_version TEXT NOT NULL DEFAULT '',
    term_rule_version TEXT NOT NULL DEFAULT '',
    commission_unit TEXT NOT NULL DEFAULT '',
    commission_matter TEXT NOT NULL DEFAULT '',
    commission_date TEXT NOT NULL DEFAULT '',
    case_summary TEXT NOT NULL DEFAULT '',
    subject_name TEXT NOT NULL DEFAULT '',
    intake_json TEXT NOT NULL DEFAULT '{}',
    note TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    filename TEXT NOT NULL,
    media_type TEXT NOT NULL DEFAULT 'other',
    original_path TEXT NOT NULL DEFAULT '',
    derived_dir TEXT NOT NULL DEFAULT '',
    sha256 TEXT NOT NULL DEFAULT '',
    page_count INTEGER NOT NULL DEFAULT 0,
    quality_flags_json TEXT NOT NULL DEFAULT '[]',
    clarity_note TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    position_key TEXT NOT NULL DEFAULT '',
    photo_caption TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS material_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
    case_id INTEGER NOT NULL,
    page_number INTEGER NOT NULL,
    image_path TEXT NOT NULL DEFAULT '',
    original_image_path TEXT NOT NULL DEFAULT '',
    is_scanned INTEGER NOT NULL DEFAULT 0,
    has_text_layer INTEGER NOT NULL DEFAULT 0,
    text_layer_chars INTEGER NOT NULL DEFAULT 0,
    quality_flags_json TEXT NOT NULL DEFAULT '[]',
    preprocessing_json TEXT NOT NULL DEFAULT '{}',
    ocr_status TEXT NOT NULL DEFAULT 'pending',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(material_id, page_number)
);

CREATE TABLE IF NOT EXISTS ocr_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    material_id INTEGER NOT NULL,
    page_id INTEGER NOT NULL DEFAULT 0,
    page_number INTEGER NOT NULL DEFAULT 0,
    block_index INTEGER NOT NULL DEFAULT 0,
    text TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    bbox_json TEXT NOT NULL DEFAULT '[]',
    engine TEXT NOT NULL DEFAULT '',
    is_handwritten INTEGER NOT NULL DEFAULT 0,
    column_hint TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS case_fields (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    field_key TEXT NOT NULL,
    field_group TEXT NOT NULL DEFAULT '',
    field_label TEXT NOT NULL DEFAULT '',
    display_order INTEGER NOT NULL DEFAULT 0,
    raw_text TEXT NOT NULL DEFAULT '',
    confirmed_value TEXT NOT NULL DEFAULT '',
    normalized_text TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'recorded',
    review_state TEXT NOT NULL DEFAULT 'pending',
    confidence REAL,
    source_material_id INTEGER,
    source_page INTEGER NOT NULL DEFAULT 0,
    source_bbox_json TEXT NOT NULL DEFAULT '[]',
    source_block_id INTEGER,
    applied_rule_codes_json TEXT NOT NULL DEFAULT '[]',
    rule_note TEXT NOT NULL DEFAULT '',
    conflict_json TEXT NOT NULL DEFAULT '[]',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(case_id, field_key)
);

CREATE TABLE IF NOT EXISTS case_injuries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL DEFAULT 0,
    body_part TEXT NOT NULL DEFAULT '',
    side TEXT NOT NULL DEFAULT '',
    injury_type TEXT NOT NULL DEFAULT '',
    count TEXT NOT NULL DEFAULT '',
    length TEXT NOT NULL DEFAULT '',
    width TEXT NOT NULL DEFAULT '',
    size_unit TEXT NOT NULL DEFAULT '',
    morphology TEXT NOT NULL DEFAULT '',
    other_traits TEXT NOT NULL DEFAULT '',
    raw_text TEXT NOT NULL DEFAULT '',
    normalized_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'recorded',
    review_state TEXT NOT NULL DEFAULT 'pending',
    confidence REAL,
    source_material_id INTEGER,
    source_page INTEGER NOT NULL DEFAULT 0,
    source_bbox_json TEXT NOT NULL DEFAULT '[]',
    source_block_id INTEGER,
    applied_rule_codes_json TEXT NOT NULL DEFAULT '[]',
    rule_note TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS terminology_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    shorthand TEXT NOT NULL DEFAULT '',
    pattern TEXT NOT NULL DEFAULT '',
    match_type TEXT NOT NULL DEFAULT 'literal',
    template TEXT NOT NULL DEFAULT '',
    scope_field_key TEXT NOT NULL DEFAULT '',
    scope_column TEXT NOT NULL DEFAULT '',
    scope_writer TEXT NOT NULL DEFAULT '',
    boundary_note TEXT NOT NULL DEFAULT '',
    require_groups_json TEXT NOT NULL DEFAULT '[]',
    missing_group_note TEXT NOT NULL DEFAULT '',
    requires_confirmation INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 100,
    active INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL DEFAULT 0,
    before_text TEXT NOT NULL DEFAULT '',
    after_text TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    confirmed_by TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT 'case_only',
    scope_value TEXT NOT NULL DEFAULT '',
    affected_json TEXT NOT NULL DEFAULT '[]',
    invalidated_json TEXT NOT NULL DEFAULT '[]',
    applied INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    doc_type TEXT NOT NULL DEFAULT 'report',
    version_no INTEGER NOT NULL DEFAULT 1,
    doc_status TEXT NOT NULL DEFAULT 'internal_draft',
    template_version TEXT NOT NULL DEFAULT '',
    term_rule_version TEXT NOT NULL DEFAULT '',
    template_path TEXT NOT NULL DEFAULT '',
    docx_path TEXT NOT NULL DEFAULT '',
    pdf_path TEXT NOT NULL DEFAULT '',
    render_engine TEXT NOT NULL DEFAULT '',
    unresolved_json TEXT NOT NULL DEFAULT '[]',
    blocked_reason TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    approved_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(case_id, doc_type, version_no)
);

CREATE TABLE IF NOT EXISTS checklist_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    document_id INTEGER,
    category TEXT NOT NULL DEFAULT 'content',
    severity TEXT NOT NULL DEFAULT 'warning',
    code TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    target_type TEXT NOT NULL DEFAULT '',
    target_id INTEGER NOT NULL DEFAULT 0,
    source_json TEXT NOT NULL DEFAULT '{}',
    suggestion TEXT NOT NULL DEFAULT '',
    resolved_by TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    resolved_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS case_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL DEFAULT '系统',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_materials_case ON materials(case_id);
CREATE INDEX IF NOT EXISTS idx_material_pages_material ON material_pages(material_id);
CREATE INDEX IF NOT EXISTS idx_ocr_blocks_case ON ocr_blocks(case_id);
CREATE INDEX IF NOT EXISTS idx_ocr_blocks_material ON ocr_blocks(material_id, page_number, block_index);
CREATE INDEX IF NOT EXISTS idx_case_fields_case ON case_fields(case_id);
CREATE INDEX IF NOT EXISTS idx_case_injuries_case ON case_injuries(case_id, seq);
CREATE INDEX IF NOT EXISTS idx_documents_case ON documents(case_id);
CREATE INDEX IF NOT EXISTS idx_checklist_case ON checklist_items(case_id, status, severity);
CREATE INDEX IF NOT EXISTS idx_case_events_case ON case_events(case_id);
CREATE INDEX IF NOT EXISTS idx_corrections_case ON corrections(case_id);
"""

#: 部位照片的列级迁移（N-11 / R-54）：旧库用 `PRAGMA table_info` + `ALTER TABLE ADD COLUMN`
#: 幂等升级，绝不重建表、绝不动已有数据。
MATERIAL_COLUMN_MIGRATIONS: Dict[str, str] = {
    "position_key": "TEXT NOT NULL DEFAULT ''",
    "photo_caption": "TEXT NOT NULL DEFAULT ''",
}

_FIELD_COLUMNS: Tuple[str, ...] = (
    "field_key", "field_group", "field_label", "display_order",
    "raw_text", "confirmed_value", "normalized_text", "unit",
    "status", "review_state", "confidence",
    "source_material_id", "source_page", "source_bbox_json", "source_block_id",
    "applied_rule_codes_json", "rule_note", "conflict_json",
)

_INJURY_COLUMNS: Tuple[str, ...] = (
    "seq", "body_part", "side", "injury_type", "count", "length", "width", "size_unit",
    "morphology", "other_traits", "raw_text", "normalized_text",
    "status", "review_state", "confidence",
    "source_material_id", "source_page", "source_bbox_json", "source_block_id",
    "applied_rule_codes_json", "rule_note",
)

_RULE_COLUMNS: Tuple[str, ...] = (
    "code", "shorthand", "pattern", "match_type", "template",
    "scope_field_key", "scope_column", "scope_writer", "boundary_note",
    "require_groups_json", "missing_group_note", "requires_confirmation",
    "priority", "active",
)

_DOCUMENT_COLUMNS: Tuple[str, ...] = (
    "doc_type", "version_no", "doc_status", "template_version", "term_rule_version",
    "template_path", "docx_path", "pdf_path", "render_engine",
    "unresolved_json", "blocked_reason", "note",
)

#: `cases` 中与 `FIELD_INDEX` 同名、需要同步到 `case_fields` 的基础列。
_CASE_FIELD_SYNC: Tuple[str, ...] = (
    "case_no", "commission_unit", "commission_matter", "commission_date",
    "case_summary", "subject_name", "template_version", "term_rule_version",
)

_SEVERITY_ORDER_SQL = (
    "CASE severity WHEN 'blocking' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END"
)


class Database:
    """SQLite 数据访问与审计层。"""

    def __init__(self, path: str) -> None:
        self.path = str(Path(path).expanduser().resolve())
        #: 由 `create_app` 注入，供 `seed()` 使用（`CaseStorage` 实例）。
        self.storage: Any = None

    # ------------------------------------------------------------------
    # 连接与建表
    # ------------------------------------------------------------------
    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """打开一个连接；正常退出时提交，异常时回滚。"""

        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        """建立全部表与索引（幂等），并做列级幂等迁移。"""

        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA_SQL)
            self._migrate(connection)

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> List[str]:
        """列级幂等迁移：`PRAGMA table_info` 比对后 `ALTER TABLE ADD COLUMN`。

        只新增列、只带默认值，不重建表、不丢数据：旧库（无 `position_key` /
        `photo_caption`）可直接升级使用（N-11 / R-54）。
        """

        existing = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(materials)").fetchall()
        }
        added: List[str] = []
        for column, ddl in MATERIAL_COLUMN_MIGRATIONS.items():
            if column in existing:
                continue
            connection.execute(
                "ALTER TABLE materials ADD COLUMN {0} {1}".format(column, ddl)
            )
            added.append("materials." + column)
        return added

    def migrate(self) -> List[str]:
        """公开的迁移入口：返回本次新增的列名（已是新库时返回空列表）。"""

        with self.connect() as connection:
            return self._migrate(connection)

    # ------------------------------------------------------------------
    # 乐观锁与审计
    # ------------------------------------------------------------------
    @staticmethod
    def _claim(connection: sqlite3.Connection, case_id: int, expected_version: int,
               timestamp: str) -> None:
        """占用案件写锁并递增 `cases.version`（INV-9）。

        `expected_version` 为真值时做条件更新，`rowcount == 0` → `VersionConflict`；
        为 0 时表示系统内部写，不校验但同样递增版本。
        """

        if expected_version:
            cursor = connection.execute(
                "UPDATE cases SET version=version+1, updated_at=? WHERE id=? AND version=?",
                (timestamp, case_id, int(expected_version)),
            )
        else:
            cursor = connection.execute(
                "UPDATE cases SET version=version+1, updated_at=? WHERE id=?",
                (timestamp, case_id),
            )
        if cursor.rowcount == 0:
            if connection.execute("SELECT 1 FROM cases WHERE id=?", (case_id,)).fetchone() is None:
                raise NotFound("案件不存在：{0}".format(case_id))
            raise VersionConflict()

    @staticmethod
    def _claim_rule(connection: sqlite3.Connection, rule_id: int, expected_version: int,
                    timestamp: str) -> None:
        """占用术语规则写锁并递增 `terminology_rules.version`。"""

        if expected_version:
            cursor = connection.execute(
                "UPDATE terminology_rules SET version=version+1, updated_at=? WHERE id=? AND version=?",
                (timestamp, rule_id, int(expected_version)),
            )
        else:
            cursor = connection.execute(
                "UPDATE terminology_rules SET version=version+1, updated_at=? WHERE id=?",
                (timestamp, rule_id),
            )
        if cursor.rowcount == 0:
            if connection.execute("SELECT 1 FROM terminology_rules WHERE id=?", (rule_id,)).fetchone() is None:
                raise NotFound("术语规则不存在：{0}".format(rule_id))
            raise VersionConflict()

    @staticmethod
    def _event(connection: sqlite3.Connection, case_id: int, event_type: str, title: str,
               detail: str, actor: str, timestamp: str) -> None:
        connection.execute(
            "INSERT INTO case_events (case_id,event_type,title,detail,actor,created_at)"
            " VALUES (?,?,?,?,?,?)",
            (case_id, event_type, title, detail, actor, timestamp),
        )

    @staticmethod
    def _refresh_case_doc_status(connection: sqlite3.Connection, case_id: int) -> str:
        """把 `cases.doc_status` 同步为最新版本文稿的状态。"""

        row = connection.execute(
            "SELECT doc_status FROM documents WHERE case_id=?"
            " ORDER BY version_no DESC, id DESC LIMIT 1",
            (case_id,),
        ).fetchone()
        status = row["doc_status"] if row else ""
        connection.execute("UPDATE cases SET doc_status=? WHERE id=?", (status, case_id))
        return status

    @staticmethod
    def _supersede_documents(connection: sqlite3.Connection, case_id: int,
                             keep_document_id: Optional[int], timestamp: str) -> List[int]:
        """把案件内除 `keep_document_id` 之外的文稿全部置为 `superseded`（INV-6）。"""

        params: List[Any] = [timestamp, case_id]
        sql = "UPDATE documents SET doc_status='superseded', updated_at=? WHERE case_id=?"
        if keep_document_id:
            sql += " AND id<>?"
            params.append(int(keep_document_id))
        sql += " AND doc_status<>'superseded'"
        connection.execute(sql, params)
        rows = connection.execute(
            "SELECT id FROM documents WHERE case_id=? AND doc_status='superseded'", (case_id,)
        ).fetchall()
        return [int(row["id"]) for row in rows]

    def claim(self, case_id: int, expected_version: int = 0, actor: str = "系统") -> Dict[str, Any]:
        """公开的占位接口：供 `workflow` 在系统写之前显式占用案件版本。"""

        timestamp = utc_now()
        with self.connect() as connection:
            self._claim(connection, case_id, expected_version, timestamp)
        return self.get_case(case_id) or {}

    def add_event(self, case_id: int, event_type: str, title: str, detail: str = "",
                  actor: str = "系统") -> None:
        """追加一条审计事件（R-48）。"""

        with self.connect() as connection:
            self._event(connection, case_id, event_type, title, detail, actor, utc_now())

    def list_events(self, case_id: int, limit: int = 200) -> List[Dict[str, Any]]:
        """案件审计时间线（最新在前）。"""

        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM case_events WHERE case_id=? ORDER BY id DESC LIMIT ?",
                (case_id, max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # 案件
    # ------------------------------------------------------------------
    def _next_case_no(self, connection: sqlite3.Connection, year: int) -> str:
        prefix = "FY-{0}-".format(year)
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM cases WHERE case_no LIKE ?", (prefix + "%",)
        ).fetchone()
        counter = int(row["total"] or 0) + 1
        while True:
            candidate = "{0}{1:04d}".format(prefix, counter)
            if connection.execute("SELECT 1 FROM cases WHERE case_no=?", (candidate,)).fetchone() is None:
                return candidate
            counter += 1

    def create_case(self, payload: Mapping[str, Any], actor: str = "系统") -> Dict[str, Any]:
        """建案；`case_no` 留空时自动生成 `FY-<年份>-<序号>`。"""

        data = _as_mapping(payload)
        timestamp = utc_now()
        case_no = normalize_case_no(_text(data.get("case_no")))
        section_order = data.get("section_order") or list(DEFAULT_SECTION_ORDER)
        intake = data.get("intake") or {}
        if not isinstance(intake, Mapping):
            intake = _as_mapping(intake)
        try:
            with self.connect() as connection:
                if not case_no:
                    case_no = self._next_case_no(connection, datetime.now(timezone.utc).year)
                cursor = connection.execute(
                    "INSERT INTO cases (case_no,stage,doc_status,section_order_json,opinion_text,"
                    "template_version,term_rule_version,commission_unit,commission_matter,"
                    "commission_date,case_summary,subject_name,intake_json,note,version,"
                    "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        case_no,
                        "intake",
                        "",
                        _json(list(section_order)),
                        _text(data.get("opinion_text")),
                        _text(data.get("template_version")) or DEFAULT_TEMPLATE_VERSION,
                        _text(data.get("term_rule_version")) or TERM_RULE_DEFAULT_VERSION,
                        _text(data.get("commission_unit")),
                        _text(data.get("commission_matter")),
                        _text(data.get("commission_date")),
                        _text(data.get("case_summary")),
                        _text(data.get("subject_name")),
                        _json(dict(intake)),
                        _text(data.get("note")),
                        1,
                        timestamp,
                        timestamp,
                    ),
                )
                case_id = int(cursor.lastrowid)
                self._sync_case_fields(connection, case_id, {
                    "case_no": case_no,
                    "commission_unit": _text(data.get("commission_unit")),
                    "commission_matter": _text(data.get("commission_matter")),
                    "commission_date": _text(data.get("commission_date")),
                    "case_summary": _text(data.get("case_summary")),
                    "subject_name": _text(data.get("subject_name")),
                    "template_version": _text(data.get("template_version")) or DEFAULT_TEMPLATE_VERSION,
                    "term_rule_version": _text(data.get("term_rule_version")) or TERM_RULE_DEFAULT_VERSION,
                }, timestamp, only_non_empty=True)
                self._event(connection, case_id, "case_created", "案件已建立",
                            "案件编号 {0}；来源：{1}".format(case_no, actor), actor, timestamp)
                self._event(connection, case_id, "intake", "接收材料",
                            "登记案件编号、材料归属与模板版本", actor, timestamp)
        except sqlite3.IntegrityError as error:
            if "case_no" in str(error):
                raise DuplicateCase() from error
            raise
        return self.get_case(case_id) or {}

    @staticmethod
    def _sync_case_fields(connection: sqlite3.Connection, case_id: int,
                          values: Mapping[str, Any], timestamp: str,
                          only_non_empty: bool = False) -> None:
        """把案件基础信息同步到 `case_fields`（INV-1 由 `FIELD_INDEX` 派生目录列）。

        这是**用户输入路径**：建案 / `PATCH /api/cases/{id}` 中用户手填的基础信息
        视为“已确认值”，同时写入 `raw_text` / `confirmed_value` / `normalized_text`。
        按 D11 取值优先级，识别结果不会覆盖非空的 `confirmed_value`，
        只会把识别原文写进 `raw_text` 并在 `case_events` 留痕。
        """

        for key, value in values.items():
            if key not in FIELD_INDEX:
                continue
            text = _text(value)
            if only_non_empty and not text:
                continue
            definition = FIELD_INDEX[key]
            existing = connection.execute(
                "SELECT id FROM case_fields WHERE case_id=? AND field_key=?", (case_id, key)
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO case_fields (case_id,field_key,field_group,field_label,display_order,"
                    "raw_text,confirmed_value,normalized_text,unit,status,review_state,confidence,"
                    "source_material_id,source_page,source_bbox_json,source_block_id,"
                    "applied_rule_codes_json,rule_note,conflict_json,version,created_at,updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,'',?,'pending',NULL,NULL,0,'[]',NULL,'[]','','[]',1,?,?)",
                    (case_id, key, definition.group, definition.label, definition.order,
                     text, text, text, "recorded", timestamp, timestamp),
                )
            else:
                # D11：用户输入只写“确认值/规范化文本”，**不覆盖 raw_text**。
                # raw_text 是“原文 / 来源痕迹”列（R-15），只有识别结果会写它；
                # 建案时该行还没有原文，才用用户值兜底写入。
                connection.execute(
                    "UPDATE case_fields SET confirmed_value=?,normalized_text=?,"
                    "raw_text=CASE WHEN raw_text='' THEN ? ELSE raw_text END,"
                    "field_group=?,field_label=?,display_order=?,updated_at=?"
                    " WHERE case_id=? AND field_key=?",
                    (text, text, text, definition.group, definition.label, definition.order,
                     timestamp, case_id, key),
                )

    def get_case(self, case_id: int) -> Optional[Dict[str, Any]]:
        """案件聚合详情（第 5.2 节注记的结构，所有 `*_json` 已解析）。"""

        with self.connect() as connection:
            row = connection.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
            if row is None:
                return None
            material_rows = connection.execute(
                "SELECT * FROM materials WHERE case_id=? ORDER BY sort_order,id", (case_id,)
            ).fetchall()
            page_rows = connection.execute(
                "SELECT * FROM material_pages WHERE case_id=? ORDER BY material_id,page_number",
                (case_id,),
            ).fetchall()
            field_rows = connection.execute(
                "SELECT * FROM case_fields WHERE case_id=? ORDER BY display_order,field_key", (case_id,)
            ).fetchall()
            injury_rows = connection.execute(
                "SELECT * FROM case_injuries WHERE case_id=? ORDER BY seq,id", (case_id,)
            ).fetchall()
            document_rows = connection.execute(
                "SELECT * FROM documents WHERE case_id=? ORDER BY version_no DESC,id DESC", (case_id,)
            ).fetchall()
            checklist_rows = connection.execute(
                "SELECT * FROM checklist_items WHERE case_id=? ORDER BY " + _SEVERITY_ORDER_SQL + ",id",
                (case_id,),
            ).fetchall()
            event_rows = connection.execute(
                "SELECT * FROM case_events WHERE case_id=? ORDER BY id DESC LIMIT 200", (case_id,)
            ).fetchall()
            correction_rows = connection.execute(
                "SELECT * FROM corrections WHERE case_id=? ORDER BY id DESC", (case_id,)
            ).fetchall()

        pages_by_material: Dict[int, List[Dict[str, Any]]] = {}
        for page in page_rows:
            pages_by_material.setdefault(int(page["material_id"]), []).append(_page_row(page))

        materials: List[Dict[str, Any]] = []
        for material in material_rows:
            item = _material_row(material)
            item["pages"] = pages_by_material.get(int(material["id"]), [])
            materials.append(item)

        fields = [_field_row(item) for item in field_rows]
        injuries = [_injury_row(item) for item in injury_rows]
        documents = [_document_row(item) for item in document_rows]
        checklist = [_checklist_row(item) for item in checklist_rows]
        corrections = [_correction_row(item) for item in correction_rows]

        unresolved = sum(
            1 for item in fields if item["status"] == "unclear" or item["review_state"] == "pending"
        ) + sum(1 for item in injuries if item["review_state"] == "pending")
        blocking = sum(
            1 for item in checklist if item["severity"] == "blocking" and item["status"] == "open"
        )

        result = _case_row(row)
        result["materials"] = materials
        result["fields"] = fields
        result["injuries"] = injuries
        result["documents"] = documents
        result["checklist"] = checklist
        result["events"] = [dict(item) for item in event_rows]
        result["corrections"] = corrections
        result["summary"] = {
            "material_count": len(materials),
            "field_count": len(fields),
            "injury_count": len(injuries),
            "unresolved_count": unresolved,
            "blocking_count": blocking,
            "document_count": len(documents),
        }
        return result

    def list_cases(self, search: str = "", stage: str = "", doc_status: str = "",
                   material_kind: str = "", field_status: str = "", has_blocking: str = "",
                   page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        """服务端分页 / 搜索 / 筛选（第 4.1 节）。"""

        page = max(1, int(page or 1))
        page_size = max(1, min(100, int(page_size or 20)))

        clauses: List[str] = []
        params: List[Any] = []
        if search:
            like = "%" + str(search).strip() + "%"
            clauses.append(
                "(c.case_no LIKE ? OR c.commission_unit LIKE ? OR c.subject_name LIKE ?"
                " OR c.commission_matter LIKE ?)"
            )
            params.extend([like, like, like, like])
        if stage:
            clauses.append("c.stage=?")
            params.append(stage)
        if doc_status:
            clauses.append("c.doc_status=?")
            params.append(doc_status)
        if material_kind:
            clauses.append("EXISTS (SELECT 1 FROM materials m WHERE m.case_id=c.id AND m.kind=?)")
            params.append(material_kind)
        if field_status:
            clauses.append("EXISTS (SELECT 1 FROM case_fields f WHERE f.case_id=c.id AND f.status=?)")
            params.append(field_status)

        blocking_sql = (
            "(SELECT COUNT(*) FROM checklist_items k WHERE k.case_id=c.id"
            " AND k.severity='blocking' AND k.status='open')"
        )
        if has_blocking in ("true", "1", "True", True):
            clauses.append(blocking_sql + " > 0")
        elif has_blocking in ("false", "0", "False", False):
            clauses.append(blocking_sql + " = 0")

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        select_sql = """
            SELECT c.*,
                (SELECT COUNT(*) FROM materials m WHERE m.case_id=c.id) AS material_count,
                (SELECT COUNT(*) FROM case_fields f WHERE f.case_id=c.id) AS field_count,
                (SELECT COUNT(*) FROM case_injuries i WHERE i.case_id=c.id) AS injury_count,
                ((SELECT COUNT(*) FROM case_fields f WHERE f.case_id=c.id
                    AND (f.status='unclear' OR f.review_state='pending'))
                 + (SELECT COUNT(*) FROM case_injuries i WHERE i.case_id=c.id
                    AND i.review_state='pending')) AS unresolved_count,
        """ + blocking_sql + """ AS blocking_count,
                (SELECT COUNT(*) FROM documents d WHERE d.case_id=c.id) AS document_count
            FROM cases c""" + where + " ORDER BY c.created_at DESC, c.id DESC LIMIT ? OFFSET ?"

        with self.connect() as connection:
            total = int(connection.execute(
                "SELECT COUNT(*) AS total FROM cases c" + where, params
            ).fetchone()["total"])
            rows = connection.execute(
                select_sql, params + [page_size, (page - 1) * page_size]
            ).fetchall()

        items: List[Dict[str, Any]] = []
        for row in rows:
            item = _case_row(row)
            for key in ("material_count", "field_count", "injury_count",
                        "unresolved_count", "blocking_count", "document_count"):
                item[key] = int(item.get(key) or 0)
            items.append(item)
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def update_case(self, case_id: int, changes: Mapping[str, Any], expected_version: int,
                    actor: str = "复核人") -> Dict[str, Any]:
        """修改案件基础信息（含 `opinion_text`，INV-10：只接受显式传入的值）。"""

        data = _as_mapping(changes)
        timestamp = utc_now()
        allowed = {
            "case_no": "case_no",
            "commission_unit": "commission_unit",
            "commission_matter": "commission_matter",
            "commission_date": "commission_date",
            "case_summary": "case_summary",
            "subject_name": "subject_name",
            "template_version": "template_version",
            "term_rule_version": "term_rule_version",
            "opinion_text": "opinion_text",
            "note": "note",
        }
        assignments: List[str] = []
        params: List[Any] = []
        changed: List[str] = []
        sync_values: Dict[str, Any] = {}
        for key, column in allowed.items():
            if key not in data or data[key] is None:
                continue
            value = _text(data[key])
            if key == "case_no":
                value = normalize_case_no(value)
                if not value:
                    raise InvalidTransition("案件编号不能为空")
            assignments.append(column + "=?")
            params.append(value)
            changed.append(key)
            if key in _CASE_FIELD_SYNC:
                sync_values[key] = value
        if "section_order" in data and data["section_order"] is not None:
            order = list(data["section_order"])
            assignments.append("section_order_json=?")
            params.append(_json(order))
            changed.append("section_order")
        if not assignments:
            raise InvalidTransition("没有需要修改的案件字段")

        assignments.append("updated_at=?")
        params.append(timestamp)
        try:
            with self.connect() as connection:
                self._claim(connection, case_id, expected_version, timestamp)
                connection.execute(
                    "UPDATE cases SET " + ",".join(assignments) + " WHERE id=?", params + [case_id]
                )
                if sync_values:
                    self._sync_case_fields(connection, case_id, sync_values, timestamp)
                self._event(connection, case_id, "case_updated", "案件信息已修改",
                            "修改字段：" + "、".join(changed)
                            + ("；备注：" + _text(data.get("note")) if data.get("note") else ""),
                            actor, timestamp)
        except sqlite3.IntegrityError as error:
            if "case_no" in str(error):
                raise DuplicateCase() from error
            raise
        return self.get_case(case_id) or {}

    def update_stage(self, case_id: int, stage: str, expected_version: int, note: str = "",
                     actor: str = "复核人") -> Dict[str, Any]:
        """推进或回退一个阶段（只允许相邻一格，第 3.1 节）。"""

        if stage not in CASE_STAGES:
            raise InvalidTransition("未知案件阶段：{0}".format(stage))
        timestamp = utc_now()
        with self.connect() as connection:
            self._claim(connection, case_id, expected_version, timestamp)
            row = connection.execute("SELECT stage FROM cases WHERE id=?", (case_id,)).fetchone()
            if row is None:
                raise NotFound("案件不存在：{0}".format(case_id))
            current = row["stage"]
            allowed = STAGE_TRANSITIONS.get(current, ())
            if stage not in allowed:
                raise InvalidTransition(
                    "阶段只能前进或回退一格：{0}（{1}）允许到达 {2}".format(
                        CASE_STAGE_LABELS.get(current, current), current,
                        "、".join(CASE_STAGE_LABELS.get(item, item) for item in allowed) or "无",
                    )
                )
            connection.execute("UPDATE cases SET stage=?, updated_at=? WHERE id=?",
                               (stage, timestamp, case_id))
            self._event(connection, case_id, "stage_changed", "案件阶段已变更",
                        "{0} → {1}".format(CASE_STAGE_LABELS.get(current, current),
                                           CASE_STAGE_LABELS.get(stage, stage))
                        + ("；备注：" + note if note else ""), actor, timestamp)
        return self.get_case(case_id) or {}

    def delete_case(self, case_id: int, expected_version: int) -> None:
        """删除案件及其全部子记录（级联）。调用方负责清理材料目录。"""

        timestamp = utc_now()
        with self.connect() as connection:
            self._claim(connection, case_id, expected_version, timestamp)
            connection.execute("DELETE FROM cases WHERE id=?", (case_id,))

    def stats(self) -> Dict[str, Any]:
        """全局统计（第 4 节 `/api/stats`）。"""

        with self.connect() as connection:
            case_count = int(connection.execute(
                "SELECT COUNT(*) AS total FROM cases").fetchone()["total"])
            stage_rows = connection.execute(
                "SELECT stage, COUNT(*) AS total FROM cases GROUP BY stage").fetchall()
            material_rows = connection.execute(
                "SELECT kind, COUNT(*) AS total FROM materials GROUP BY kind").fetchall()
            status_rows = connection.execute(
                "SELECT status, COUNT(*) AS total FROM case_fields GROUP BY status").fetchall()
            unresolved_fields = int(connection.execute(
                "SELECT COUNT(*) AS total FROM case_fields"
                " WHERE status='unclear' OR review_state='pending'").fetchone()["total"])
            unresolved_injuries = int(connection.execute(
                "SELECT COUNT(*) AS total FROM case_injuries WHERE review_state='pending'"
            ).fetchone()["total"])
            blocking = int(connection.execute(
                "SELECT COUNT(*) AS total FROM checklist_items"
                " WHERE severity='blocking' AND status='open'").fetchone()["total"])
            document_count = int(connection.execute(
                "SELECT COUNT(*) AS total FROM documents").fetchone()["total"])
            approved = int(connection.execute(
                "SELECT COUNT(*) AS total FROM documents WHERE doc_status='approved'"
            ).fetchone()["total"])
            # 三项总数（前端统计卡直接消费；此前只有"未处理"口径，导致卡片恒为 0）
            field_count = int(connection.execute(
                "SELECT COUNT(*) AS total FROM case_fields").fetchone()["total"])
            injury_count = int(connection.execute(
                "SELECT COUNT(*) AS total FROM case_injuries").fetchone()["total"])
            material_count = int(connection.execute(
                "SELECT COUNT(*) AS total FROM materials").fetchone()["total"])

        stage_counts = {item: 0 for item in CASE_STAGES}
        for row in stage_rows:
            if row["stage"] in stage_counts:
                stage_counts[row["stage"]] = int(row["total"] or 0)
        material_counts = {item: 0 for item in MATERIAL_KINDS}
        for row in material_rows:
            material_counts[row["kind"]] = int(row["total"] or 0)
        field_status_counts = {item: 0 for item in FIELD_STATUSES}
        for row in status_rows:
            if row["status"] in field_status_counts:
                field_status_counts[row["status"]] = int(row["total"] or 0)

        return {
            "case_count": case_count,
            "total_cases": case_count,
            "stage_counts": stage_counts,
            "by_stage": [
                {"stage": key, "label": CASE_STAGE_LABELS.get(key, key), "count": stage_counts[key]}
                for key in CASE_STAGES
            ],
            "unresolved_field_count": unresolved_fields,
            "unresolved_injury_count": unresolved_injuries,
            "unresolved_count": unresolved_fields + unresolved_injuries,
            "blocking_count": blocking,
            "document_count": document_count,
            "approved_document_count": approved,
            # 总数口径（统计卡用）；与 *_count 的"未处理"口径并存，含义见 docs/architecture.md §4
            "material_count": material_count,
            "field_count": field_count,
            "injury_count": injury_count,
            "material_counts": material_counts,
            "by_material_kind": [
                {"kind": key, "label": MATERIAL_KIND_LABELS.get(key, key), "count": material_counts[key]}
                for key in MATERIAL_KINDS
            ],
            "field_status_counts": field_status_counts,
        }

    def seed(self, case_count: int = SEED_CASE_COUNT) -> List[int]:
        """建立内置演示案件（已存在案件时返回空列表）。"""

        with self.connect() as connection:
            if connection.execute("SELECT 1 FROM cases LIMIT 1").fetchone():
                return []
        from .seed import seed_demo_case  # 延迟导入：避免与 seed.py 形成循环
        from .storage import CaseStorage, DEFAULT_STORAGE_ROOT

        storage = self.storage or CaseStorage(DEFAULT_STORAGE_ROOT)
        storage.ensure()
        self.storage = storage
        ids: List[int] = []
        for index in range(1, max(1, int(case_count)) + 1):
            ids.append(int(seed_demo_case(self, storage, index=index)))
        return ids

    # ------------------------------------------------------------------
    # 材料与页面
    # ------------------------------------------------------------------
    def add_material(self, case_id: int, payload: Mapping[str, Any], expected_version: int,
                     storage: Any, actor: str = "复核人") -> Dict[str, Any]:
        """登记材料；`content_base64` 存在时落盘到 `originals/<kind>/`。

        `kind == "photo"`（部位照片，N-11 / R-54）：
        **只**登记 1 页、`image_path` 直接指向原件、`is_scanned=False`，
        **不生成识别用副本、不写 `ocr_blocks`**；`position_key` / `photo_caption`
        只是用户填写的元数据，本方法不读取、不解析任何照片像素内容。
        """

        data = _as_mapping(payload)
        timestamp = utc_now()
        kind = _text(data.get("kind")) or "other"
        filename = _text(data.get("filename")) or "material"
        is_photo = kind == PHOTO_MATERIAL_KIND
        position_key = _text(data.get("position_key")) if is_photo else ""
        photo_caption = _text(data.get("photo_caption")) if is_photo else ""
        if position_key and position_key not in PHOTO_POSITION_INDEX:
            raise ValueError("未知照片目标位置：{0}".format(position_key))
        content = _text(data.get("content_base64"))
        if content.startswith("data:") and "," in content:
            content = content.split(",", 1)[1]
        blob: Optional[bytes] = None
        if content.strip():
            try:
                blob = base64.b64decode(content, validate=False)
            except Exception as error:  # noqa: BLE001 - 统一转为 409
                raise InvalidTransition("材料内容不是有效的 base64 编码") from error

        with self.connect() as connection:
            self._claim(connection, case_id, expected_version, timestamp)
            row = connection.execute("SELECT case_no FROM cases WHERE id=?", (case_id,)).fetchone()
            if row is None:
                raise NotFound("案件不存在：{0}".format(case_id))
            case_no = row["case_no"]

            digest = ""
            if blob is not None:
                digest = hashlib.sha256(blob).hexdigest()
                duplicate = connection.execute(
                    "SELECT id,filename FROM materials WHERE case_id=? AND kind=? AND sha256=?"
                    " AND sha256<>''",
                    (case_id, kind, digest),
                ).fetchone()
                if duplicate is not None:
                    raise InvalidTransition(
                        "该材料与已登记的「{0}」内容完全相同（sha256 一致），不能重复上传".format(
                            duplicate["filename"]
                        )
                    )

            original_path = ""
            if blob is not None:
                original_path = storage.write_original(case_no, kind, filename, blob)
            else:
                original_path = storage.write_original(case_no, kind, filename, None)

            derived_dir = "" if is_photo else storage.relative(storage.derived_dir(case_no, kind))
            page_count = 1 if is_photo else int(data.get("page_count") or 0)
            cursor = connection.execute(
                "INSERT INTO materials (case_id,kind,filename,media_type,original_path,derived_dir,"
                "sha256,page_count,quality_flags_json,clarity_note,sort_order,position_key,"
                "photo_caption,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    case_id, kind, filename, _text(data.get("media_type")) or "other",
                    original_path, derived_dir, digest,
                    page_count,
                    _json(list(data.get("quality_flags") or [])),
                    _text(data.get("clarity_note")),
                    int(data.get("sort_order") or 0),
                    position_key, photo_caption,
                    timestamp, timestamp,
                ),
            )
            material_id = int(cursor.lastrowid)

            if is_photo:
                # N-11 / R-55：照片只有 1 页，image_path 直接指向原件；
                # 不生成 derived 副本、不写 ocr_blocks、不进入任何识别或模型流程。
                connection.execute(
                    "INSERT OR IGNORE INTO material_pages (material_id,case_id,page_number,"
                    "original_image_path,image_path,is_scanned,has_text_layer,text_layer_chars,"
                    "quality_flags_json,preprocessing_json,ocr_status,note,created_at,updated_at)"
                    " VALUES (?,?,1,?,?,0,0,0,'[]','{}','skipped',?,?,?)",
                    (
                        material_id, case_id, original_path, original_path,
                        "部位照片不进入识别流程（N-11/R-55）", timestamp, timestamp,
                    ),
                )
            else:
                for page_number in range(1, page_count + 1):
                    connection.execute(
                        "INSERT OR IGNORE INTO material_pages (material_id,case_id,page_number,"
                        "original_image_path,image_path,is_scanned,has_text_layer,text_layer_chars,"
                        "quality_flags_json,preprocessing_json,ocr_status,note,created_at,updated_at)"
                        " VALUES (?,?,?,?,'',0,0,0,'[]','{}','pending','',?,?)",
                        (material_id, case_id, page_number, original_path, timestamp, timestamp),
                    )
            self._event(connection, case_id, "material_added", "材料已登记",
                        "{0}（{1}），{2} 页，原件 {3}{4}".format(
                            filename, MATERIAL_KIND_LABELS.get(kind, kind), page_count, original_path,
                            "；目标位置：" + PHOTO_POSITION_LABELS.get(position_key, "未指定")
                            if is_photo else ""),
                        actor, timestamp)
        return self.get_material(material_id) or {}

    def list_materials(self, case_id: int) -> List[Dict[str, Any]]:
        """材料清单（含页级信息）。"""

        with self.connect() as connection:
            material_rows = connection.execute(
                "SELECT * FROM materials WHERE case_id=? ORDER BY sort_order,id", (case_id,)
            ).fetchall()
            page_rows = connection.execute(
                "SELECT * FROM material_pages WHERE case_id=? ORDER BY material_id,page_number",
                (case_id,),
            ).fetchall()
        pages_by_material: Dict[int, List[Dict[str, Any]]] = {}
        for page in page_rows:
            pages_by_material.setdefault(int(page["material_id"]), []).append(_page_row(page))
        result: List[Dict[str, Any]] = []
        for material in material_rows:
            item = _material_row(material)
            item["pages"] = pages_by_material.get(int(material["id"]), [])
            result.append(item)
        return result

    def get_material(self, material_id: int) -> Optional[Dict[str, Any]]:
        """单个材料（含页）。"""

        with self.connect() as connection:
            row = connection.execute("SELECT * FROM materials WHERE id=?", (material_id,)).fetchone()
            if row is None:
                return None
            page_rows = connection.execute(
                "SELECT * FROM material_pages WHERE material_id=? ORDER BY page_number", (material_id,)
            ).fetchall()
        item = _material_row(row)
        item["pages"] = [_page_row(page) for page in page_rows]
        return item

    def get_page(self, material_id: int, page_number: int) -> Optional[Dict[str, Any]]:
        """单页信息。"""

        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM material_pages WHERE material_id=? AND page_number=?",
                (material_id, page_number),
            ).fetchone()
        return _page_row(row) if row else None

    # ------------------------------------------------------------------
    # 部位照片（N-11：只写元数据，不读取照片内容）
    # ------------------------------------------------------------------
    def set_photo_placement(self, case_id: int, material_id: int, payload: Mapping[str, Any],
                            expected_version: int, actor: str = "复核人") -> Dict[str, Any]:
        """为部位照片指定 / 调整目标位置与顺序（R-54 / R-56）。

        * 先 `_claim(case_id, expected_version)` → `VersionConflict` → HTTP 409（INV-9）；
        * 材料不存在、不属于该案件或 `kind != "photo"` → `ValueError`；
        * 只写 `position_key` / `photo_caption` / `sort_order` 三个元数据列，
          **不读取、不解码、不推断任何照片内容**（N-11）；`position_key` 留空
          表示"尚未指定位置"，该照片不会进入任何栏位（R-57）。
        """

        data = _as_mapping(payload)
        timestamp = utc_now()
        with self.connect() as connection:
            self._claim(connection, case_id, expected_version, timestamp)
            row = connection.execute(
                "SELECT * FROM materials WHERE id=?", (material_id,)
            ).fetchone()
            if row is None:
                raise ValueError("材料不存在：{0}".format(material_id))
            if int(row["case_id"]) != int(case_id):
                raise ValueError("材料 {0} 不属于案件 {1}".format(material_id, case_id))
            if _text(row["kind"]) != PHOTO_MATERIAL_KIND:
                raise ValueError(
                    "只有部位照片（kind=photo）可以指定目标位置，当前类型：{0}".format(row["kind"])
                )
            position_key = _text(data.get("position_key"))
            if position_key and position_key not in PHOTO_POSITION_INDEX:
                raise ValueError("未知照片目标位置：{0}".format(position_key))
            caption = (row["photo_caption"] if data.get("photo_caption") is None
                       else _text(data["photo_caption"]))
            sort_order = (int(row["sort_order"] or 0) if data.get("sort_order") is None
                          else int(data["sort_order"] or 0))
            if position_key:
                used = int(connection.execute(
                    "SELECT COUNT(*) AS total FROM materials WHERE case_id=? AND kind=?"
                    " AND position_key=? AND id<>?",
                    (case_id, PHOTO_MATERIAL_KIND, position_key, material_id),
                ).fetchone()["total"])
                if used >= PHOTO_MAX_PER_POSITION:
                    raise ValueError(
                        "栏位「{0}」最多放 {1} 张照片，当前已有 {2} 张".format(
                            PHOTO_POSITION_LABELS.get(position_key, position_key),
                            PHOTO_MAX_PER_POSITION, used)
                    )
            previous = _text(row["position_key"])
            connection.execute(
                "UPDATE materials SET position_key=?,photo_caption=?,sort_order=?,updated_at=?"
                " WHERE id=?",
                (position_key, caption, sort_order, timestamp, material_id),
            )
            self._event(
                connection, case_id, "photo_placement_updated", "照片位置已更新",
                "{0}：{1} → {2}；顺序 {3}".format(
                    _text(row["filename"]),
                    PHOTO_POSITION_LABELS.get(previous, "未指定"),
                    PHOTO_POSITION_LABELS.get(position_key, "未指定"),
                    sort_order,
                ),
                actor, timestamp,
            )
        return self.get_material(material_id) or {}

    def list_photo_slots(self, case_id: int) -> List[Dict[str, Any]]:
        """照片栏位视图（`docs/architecture.md` 第 5.2 节末）。

        顺序按 `PHOTO_POSITION_DEFINITIONS.order`；**未指派位置的照片不出现在任何栏位**
        （R-57）。本方法只读元数据列，不打开任何图片文件（N-11）。
        """

        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM materials WHERE case_id=? AND kind=? ORDER BY sort_order,id",
                (case_id, PHOTO_MATERIAL_KIND),
            ).fetchall()

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            key = _text(row["position_key"])
            if key not in PHOTO_POSITION_INDEX:
                continue
            grouped.setdefault(key, []).append({
                "material_id": int(row["id"]),
                "filename": _text(row["filename"]),
                "caption": _text(row["photo_caption"]),
                "sort_order": int(row["sort_order"] or 0),
                "original_path": _text(row["original_path"]),
            })

        slots: List[Dict[str, Any]] = []
        for definition in PHOTO_POSITION_DEFINITIONS:
            photos = grouped.get(definition.key, [])
            slots.append({
                "position_key": definition.key,
                "label": definition.label,
                "section": definition.section,
                "section_label": PHOTO_SECTION_LABELS.get(
                    definition.section, definition.section),
                "order": definition.order,
                "anchor": definition.anchor,
                "wide": bool(definition.wide),
                "missing": not photos,
                "photos": photos,
            })
        return slots

    def register_pages(self, material_id: int, pages: Sequence[Mapping[str, Any]],
                       expected_version: int) -> List[Dict[str, Any]]:
        """写入/更新页级探测结果（`imaging.PageProbe` 的字段名）。"""

        timestamp = utc_now()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT case_id FROM materials WHERE id=?", (material_id,)
            ).fetchone()
            if row is None:
                raise NotFound("材料不存在：{0}".format(material_id))
            case_id = int(row["case_id"])
            self._claim(connection, case_id, expected_version, timestamp)

            for page in pages:
                data = _as_mapping(page)
                page_number = int(data.get("page_number") or 0)
                if page_number <= 0:
                    continue
                connection.execute(
                    "INSERT INTO material_pages (material_id,case_id,page_number,image_path,"
                    "original_image_path,is_scanned,has_text_layer,text_layer_chars,quality_flags_json,"
                    "preprocessing_json,ocr_status,note,created_at,updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,'[]','{}','pending','',?,?)"
                    " ON CONFLICT(material_id,page_number) DO UPDATE SET"
                    " image_path=excluded.image_path, original_image_path=excluded.original_image_path,"
                    " is_scanned=excluded.is_scanned, has_text_layer=excluded.has_text_layer,"
                    " text_layer_chars=excluded.text_layer_chars, updated_at=excluded.updated_at",
                    (
                        material_id, case_id, page_number,
                        _text(data.get("image_path")), _text(data.get("original_path")
                                                             or data.get("original_image_path")),
                        int(bool(data.get("is_scanned"))), int(bool(data.get("has_text_layer"))),
                        int(data.get("text_layer_chars") or 0), timestamp, timestamp,
                    ),
                )
            count_row = connection.execute(
                "SELECT MAX(page_number) AS total FROM material_pages WHERE material_id=?",
                (material_id,),
            ).fetchone()
            page_count = int(count_row["total"] or 0)
            connection.execute(
                "UPDATE materials SET page_count=?, updated_at=? WHERE id=?",
                (page_count, timestamp, material_id),
            )
            self._event(connection, case_id, "pages_registered", "页级信息已登记",
                        "材料 {0}：共 {1} 页".format(material_id, page_count), "系统", timestamp)
        return (self.get_material(material_id) or {}).get("pages", [])

    def update_material_page(self, material_id: int, page_number: int,
                             changes: Mapping[str, Any], expected_version: int,
                             actor: str = "复核人") -> Dict[str, Any]:
        """修订页级质量标记（R-18）。"""

        data = _as_mapping(changes)
        timestamp = utc_now()
        allowed = {
            "is_scanned": "is_scanned",
            "has_text_layer": "has_text_layer",
            "text_layer_chars": "text_layer_chars",
            "note": "note",
        }
        assignments: List[str] = []
        params: List[Any] = []
        changed: List[str] = []
        for key, column in allowed.items():
            if key not in data or data[key] is None:
                continue
            value = data[key]
            if key in ("is_scanned", "has_text_layer"):
                value = int(bool(value))
            elif key == "text_layer_chars":
                value = int(value)
            else:
                value = _text(value)
            assignments.append(column + "=?")
            params.append(value)
            changed.append(key)
        if "quality_flags" in data and data["quality_flags"] is not None:
            assignments.append("quality_flags_json=?")
            params.append(_json(list(data["quality_flags"])))
            changed.append("quality_flags")
        if not assignments:
            raise InvalidTransition("没有需要修改的页级属性")

        with self.connect() as connection:
            row = connection.execute(
                "SELECT case_id FROM materials WHERE id=?", (material_id,)
            ).fetchone()
            if row is None:
                raise NotFound("材料不存在：{0}".format(material_id))
            case_id = int(row["case_id"])
            self._claim(connection, case_id, expected_version, timestamp)
            assignments.append("updated_at=?")
            params.append(timestamp)
            cursor = connection.execute(
                "UPDATE material_pages SET " + ",".join(assignments)
                + " WHERE material_id=? AND page_number=?",
                params + [material_id, page_number],
            )
            if cursor.rowcount == 0:
                raise NotFound("页码不存在：{0} 第 {1} 页".format(material_id, page_number))
            self._event(connection, case_id, "page_updated", "页级质量标记已修订",
                        "材料 {0} 第 {1} 页：{2}".format(material_id, page_number, "、".join(changed)),
                        actor, timestamp)
        return self.get_page(material_id, page_number) or {}

    def save_preprocessing(self, material_id: int, page_number: int,
                           operations: Sequence[str], derived_path: str) -> Dict[str, Any]:
        """记录预处理操作与识别用副本路径（不改动原件，R-19）。"""

        timestamp = utc_now()
        payload = {
            "operations": list(operations),
            "derived_path": _text(derived_path),
            "at": timestamp,
        }
        with self.connect() as connection:
            connection.execute(
                "UPDATE material_pages SET image_path=?, preprocessing_json=?, updated_at=?"
                " WHERE material_id=? AND page_number=?",
                (_text(derived_path), _json(payload), timestamp, material_id, page_number),
            )
        return self.get_page(material_id, page_number) or {}

    def set_ocr_status(self, material_id: int, page_number: int, status: str) -> None:
        """更新页级识别状态。"""

        with self.connect() as connection:
            connection.execute(
                "UPDATE material_pages SET ocr_status=?, updated_at=?"
                " WHERE material_id=? AND page_number=?",
                (_text(status), utc_now(), material_id, page_number),
            )

    # ------------------------------------------------------------------
    # OCR
    # ------------------------------------------------------------------
    def replace_ocr_blocks(self, case_id: int, material_id: int, page: Mapping[str, Any],
                           engine: str, blocks: Sequence[Any]) -> int:
        """替换某页的识别结果（重跑识别时不累积旧块）。"""

        page_data = _as_mapping(page)
        page_id = int(page_data.get("id") or 0)
        page_number = int(page_data.get("page_number") or 0)
        timestamp = utc_now()
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM ocr_blocks WHERE case_id=? AND material_id=? AND page_number=?",
                (case_id, material_id, page_number),
            )
            count = 0
            for index, block in enumerate(blocks):
                data = _as_mapping(block)
                text = _text(data.get("text"))
                if not text.strip():
                    continue
                connection.execute(
                    "INSERT INTO ocr_blocks (case_id,material_id,page_id,page_number,block_index,"
                    "text,confidence,bbox_json,engine,is_handwritten,column_hint,created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        case_id, material_id, page_id, page_number,
                        int(data.get("block_index", index)),
                        text,
                        float(data.get("confidence") or 0.0),
                        _json(list(data.get("bbox") or [])),
                        _text(data.get("engine")) or _text(engine),
                        int(bool(data.get("is_handwritten"))),
                        _text(data.get("column_hint")),
                        timestamp,
                    ),
                )
                count += 1
        return count

    def list_ocr_blocks(self, case_id: int, material_id: Optional[int] = None,
                        page: int = 1, page_size: int = 200) -> Dict[str, Any]:
        """识别结果分页。"""

        page = max(1, int(page or 1))
        page_size = max(1, min(2000, int(page_size or 200)))
        clauses = ["case_id=?"]
        params: List[Any] = [case_id]
        if material_id:
            clauses.append("material_id=?")
            params.append(int(material_id))
        where = " WHERE " + " AND ".join(clauses)
        with self.connect() as connection:
            total = int(connection.execute(
                "SELECT COUNT(*) AS total FROM ocr_blocks" + where, params
            ).fetchone()["total"])
            rows = connection.execute(
                "SELECT * FROM ocr_blocks" + where
                + " ORDER BY material_id,page_number,block_index,id LIMIT ? OFFSET ?",
                params + [page_size, (page - 1) * page_size],
            ).fetchall()
        return {
            "items": [_block_row(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    # ------------------------------------------------------------------
    # 字段
    # ------------------------------------------------------------------
    def upsert_fields(self, case_id: int, fields: Sequence[Any], replace_existing: bool,
                      expected_version: int, actor: str = "系统") -> List[Dict[str, Any]]:
        """写入提取结果。

        * `field_key` 必须来自 `FIELD_INDEX`，否则丢弃（INV-1）。
        * `replace_existing=True` 时清理本次未命中且**未被用户确认**的旧字段行，
          已确认的字段一律保留（R-40）。
        * `status='not_recorded'` 强制 `normalized_text='未记录'`（INV-3）。
        * **D11 取值优先级**：非空的 `confirmed_value` 是用户已确认值，识别结果**不覆盖**
          它（只写 `raw_text` / `normalized_text` / 来源列）；仅当该行还没有确认值时，
          识别结果才允许写入 `confirmed_value`。每次“保留用户值”都写
          `case_events.event_type='field_value_priority'` 留痕，绝不静默丢弃识别结果。
        """

        timestamp = utc_now()
        prepared: List[Dict[str, Any]] = []
        for item in fields:
            data = _as_mapping(item)
            key = _text(data.get("field_key"))
            definition = FIELD_INDEX.get(key)
            if definition is None:
                continue
            status = _text(data.get("status")) or "recorded"
            if status not in FIELD_STATUSES:
                status = "recorded"
            review_state = _text(data.get("review_state")) or "pending"
            if review_state not in REVIEW_STATES:
                review_state = "pending"
            normalized = _text(data.get("normalized_text"))
            if status == "not_recorded":
                # INV-3 / N-5：空白一律写“未记录”；该分支已保证“未见异常”不可能出现，
                # 因此不再需要额外的“含未见异常”兜底（D13：原分支不可达，已删除）。
                normalized = "未记录"
            prepared.append({
                "field_key": key,
                "field_group": definition.group,
                "field_label": definition.label,
                "display_order": definition.order,
                "raw_text": _text(data.get("raw_text")),
                "confirmed_value": _text(data.get("confirmed_value")),
                "normalized_text": normalized,
                "unit": _text(data.get("unit")) or definition.unit_hint,
                "status": status,
                "review_state": review_state,
                "confidence": _float_or_none(data.get("confidence")),
                "source_material_id": _int_or_none(data.get("source_material_id")),
                "source_page": int(data.get("source_page") or 0),
                "source_bbox_json": _json(list(data.get("source_bbox") or [])),
                "source_block_id": _int_or_none(data.get("source_block_id")),
                "applied_rule_codes_json": _json(list(data.get("applied_rule_codes") or [])),
                "rule_note": _text(data.get("rule_note")),
                "conflict_json": _json(list(data.get("conflict") or [])),
            })

        keys = [item["field_key"] for item in prepared]
        with self.connect() as connection:
            self._claim(connection, case_id, expected_version, timestamp)
            if replace_existing and keys:
                placeholders = ",".join("?" for _ in keys)
                connection.execute(
                    "DELETE FROM case_fields WHERE case_id=? AND review_state<>'confirmed'"
                    " AND field_key NOT IN (" + placeholders + ")",
                    [case_id] + keys,
                )
            inserted = 0
            updated = 0
            kept: List[Dict[str, str]] = []
            for item in prepared:
                existing = connection.execute(
                    "SELECT id,confirmed_value FROM case_fields WHERE case_id=? AND field_key=?",
                    (case_id, item["field_key"]),
                ).fetchone()
                if existing is None:
                    columns = ",".join(_FIELD_COLUMNS)
                    placeholders = ",".join("?" for _ in _FIELD_COLUMNS)
                    connection.execute(
                        "INSERT INTO case_fields (case_id," + columns + ",version,created_at,updated_at)"
                        " VALUES (?," + placeholders + ",1,?,?)",
                        [case_id] + [item[column] for column in _FIELD_COLUMNS] + [timestamp, timestamp],
                    )
                    inserted += 1
                else:
                    if not replace_existing and item["status"] == "not_recorded":
                        continue
                    # D11 取值优先级：非空 confirmed_value 是用户已确认值，识别结果不覆盖它。
                    previous_confirmed = _text(existing["confirmed_value"])
                    protected = ("confirmed_value",) if previous_confirmed else ()
                    if previous_confirmed:
                        recognized = _text(item["raw_text"]) or _text(item["normalized_text"])
                        if recognized and recognized != previous_confirmed:
                            kept.append({
                                "field_key": item["field_key"],
                                "user_value": previous_confirmed,
                                "recognized": recognized,
                            })
                    assignments = ",".join(column + "=?" for column in _FIELD_COLUMNS
                                           if column not in protected)
                    values = [item[column] for column in _FIELD_COLUMNS
                              if column not in protected]
                    connection.execute(
                        "UPDATE case_fields SET " + assignments
                        + ",version=version+1,updated_at=? WHERE case_id=? AND field_key=?",
                        values + [timestamp, case_id, item["field_key"]],
                    )
                    updated += 1
            if inserted or updated:
                summary = "新增 {0} 项，更新 {1} 项".format(inserted, updated)
                if kept:
                    summary += "；保留用户已确认值 {0} 项（识别值不覆盖，D11/R-45）".format(len(kept))
                self._event(connection, case_id, "fields_extracted", "字段提取完成",
                            summary, actor, timestamp)
            if kept:
                # 留痕（D11）：让“识别值与用户已确认值不一致”可见，而不是静默丢识别结果
                detail = "；".join(
                    "{0}：保留“{1}”，识别原文“{2}”".format(
                        entry["field_key"], entry["user_value"][:40], entry["recognized"][:40])
                    for entry in kept[:8]
                )
                if len(kept) > 8:
                    detail += "；另有 {0} 项（见案件事件）".format(len(kept) - 8)
                self._event(connection, case_id, "field_value_priority",
                            "识别值未覆盖用户已确认值", detail, actor, timestamp)
        return self.list_fields(case_id)

    def list_fields(self, case_id: int) -> List[Dict[str, Any]]:
        """字段列表（按目录顺序）。"""

        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM case_fields WHERE case_id=? ORDER BY display_order,field_key",
                (case_id,),
            ).fetchall()
        return [_field_row(row) for row in rows]

    def get_field(self, case_id: int, field_key: str) -> Optional[Dict[str, Any]]:
        """单个字段。"""

        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM case_fields WHERE case_id=? AND field_key=?", (case_id, field_key)
            ).fetchone()
        return _field_row(row) if row else None

    def update_field(self, case_id: int, field_key: str, changes: Mapping[str, Any],
                     expected_version: int, actor: str = "复核人") -> Dict[str, Any]:
        """核对 / 确认字段（R-15、R-45）。"""

        data = _as_mapping(changes)
        timestamp = utc_now()
        with self.connect() as connection:
            self._claim(connection, case_id, expected_version, timestamp)
            row = connection.execute(
                "SELECT * FROM case_fields WHERE case_id=? AND field_key=?", (case_id, field_key)
            ).fetchone()
            if row is None:
                raise NotFound("字段不存在：{0}".format(field_key))
            previous = _field_row(row)

            status = _text(data.get("status")) or previous["status"]
            if status not in FIELD_STATUSES:
                raise InvalidTransition("未知字段状态：{0}".format(status))
            review_state = _text(data.get("review_state")) or previous["review_state"]
            if review_state not in REVIEW_STATES:
                raise InvalidTransition("未知复核状态：{0}".format(review_state))
            confirmed = (previous["confirmed_value"] if data.get("confirmed_value") is None
                         else _text(data["confirmed_value"]))
            normalized = (previous["normalized_text"] if data.get("normalized_text") is None
                          else _text(data["normalized_text"]))
            unit = previous["unit"] if data.get("unit") is None else _text(data["unit"])
            # INV-3：未记录必须是“未记录”，且绝不含“未见异常”（N-5）
            if status == "not_recorded":
                normalized = "未记录"

            connection.execute(
                "UPDATE case_fields SET status=?,review_state=?,confirmed_value=?,normalized_text=?,"
                "unit=?,version=version+1,updated_at=? WHERE case_id=? AND field_key=?",
                (status, review_state, confirmed, normalized, unit, timestamp, case_id, field_key),
            )
            superseded: List[int] = []
            if confirmed != previous["confirmed_value"]:
                superseded = self._supersede_documents(connection, case_id, None, timestamp)
            self._event(connection, case_id, "field_updated", "字段已核对",
                        "{0}（{1}）：{2} / {3}".format(
                            previous["field_label"] or field_key, field_key, status, review_state)
                        + ("；备注：" + _text(data.get("note")) if data.get("note") else "")
                        + ("；已失效文稿 {0} 份".format(len(superseded)) if superseded else ""),
                        actor, timestamp)
        return self.get_field(case_id, field_key) or {}

    def invalidate_reviews(self, case_id: int, target_type: str, target_id: int,
                           reason: str) -> List[Dict[str, Any]]:
        """把被打回复核的对象 `review_state` 重置为 `pending`（R-40/INV-6）。

        返回受影响的对象列表；`target_type='case'` 表示整个案件。
        """

        timestamp = utc_now()
        affected: List[Dict[str, Any]] = []
        with self.connect() as connection:
            if target_type == "case":
                connection.execute(
                    "UPDATE case_fields SET review_state='pending',version=version+1,updated_at=?"
                    " WHERE case_id=?", (timestamp, case_id))
                connection.execute(
                    "UPDATE case_injuries SET review_state='pending',version=version+1,updated_at=?"
                    " WHERE case_id=?", (timestamp, case_id))
            elif target_type == "field":
                connection.execute(
                    "UPDATE case_fields SET review_state='pending',version=version+1,updated_at=?"
                    " WHERE case_id=? AND id=?", (timestamp, case_id, int(target_id)))
            elif target_type == "injury":
                connection.execute(
                    "UPDATE case_injuries SET review_state='pending',version=version+1,updated_at=?"
                    " WHERE case_id=? AND id=?", (timestamp, case_id, int(target_id)))
            elif target_type == "document":
                connection.execute(
                    "UPDATE documents SET doc_status='superseded',updated_at=?"
                    " WHERE case_id=? AND id=?", (timestamp, case_id, int(target_id)))
            self._event(connection, case_id, "review_invalidated", "复核状态已失效",
                        "{0}#{1}：{2}".format(target_type, target_id, reason), "系统", timestamp)
            if target_type in ("case", "field"):
                rows = connection.execute(
                    "SELECT * FROM case_fields WHERE case_id=? AND review_state='pending'"
                    + ("" if target_type == "case" else " AND id=?"),
                    (case_id,) if target_type == "case" else (case_id, int(target_id)),
                ).fetchall()
                affected.extend(_field_row(item) for item in rows)
            if target_type in ("case", "injury"):
                rows = connection.execute(
                    "SELECT * FROM case_injuries WHERE case_id=? AND review_state='pending'"
                    + ("" if target_type == "case" else " AND id=?"),
                    (case_id,) if target_type == "case" else (case_id, int(target_id)),
                ).fetchall()
                affected.extend(_injury_row(item) for item in rows)
        return affected

    def apply_normalization(self, case_id: int, updates: Sequence[Mapping[str, Any]],
                            expected_version: int = 0, actor: str = "系统") -> Dict[str, Any]:
        """写回术语整理结果（R-4 / R-23 / R-33 / R-34 / R-35）。

        `updates` 每项形如::

            {"target_type": "field" | "injury", "target_id": int, "field_key": str,
             "normalized_text": str, "status": str, "applied_rule_codes": [...],
             "rule_note": str, "requires_confirmation": bool, "issues": [...]}

        硬性约束：

        * 只写规范化相关列，**绝不改写事实原文**（`raw_text` / `confirmed_value`），
          因此不会改变数值精度、左右侧或否定词（N-2 / N-3 / N-4）；
        * `status == "not_recorded"` 时 `normalized_text` 恒为"未记录"，
          且绝不写入"未见异常"（INV-3 / N-5）；
        * `requires_confirmation=True` 时把 `review_state` 打回 `pending`
          （R-33 / R-34 / R-44），已确认字段不会被静默改写。
        """

        timestamp = utc_now()
        fields_written = 0
        injuries_written = 0
        pending = 0
        issue_notes: List[str] = []
        with self.connect() as connection:
            self._claim(connection, case_id, expected_version, timestamp)
            for item in updates:
                data = _as_mapping(item)
                target_type = _text(data.get("target_type"))
                normalized = _text(data.get("normalized_text"))
                status = _text(data.get("status")) or "recorded"
                if status == "not_recorded":
                    # INV-3 / N-5：空白一律写"未记录"，绝不写"未见异常"
                    normalized = "未记录"
                requires_confirmation = bool(data.get("requires_confirmation"))
                rules = _json(list(data.get("applied_rule_codes") or []))
                note = _text(data.get("rule_note"))
                issues = list(data.get("issues") or [])
                if target_type == "field":
                    field_key = _text(data.get("field_key"))
                    if not field_key:
                        continue
                    review = ("pending" if requires_confirmation
                              else _text(data.get("review_state")) or "pending")
                    if review not in REVIEW_STATES:
                        review = "pending"
                    cursor = connection.execute(
                        "UPDATE case_fields SET normalized_text=?,status=?,review_state=?,"
                        "applied_rule_codes_json=?,rule_note=?,version=version+1,updated_at=?"
                        " WHERE case_id=? AND field_key=?",
                        (normalized, status, review, rules, note, timestamp, case_id, field_key),
                    )
                    fields_written += int(cursor.rowcount or 0)
                elif target_type == "injury":
                    injury_id = int(data.get("target_id") or 0)
                    if injury_id <= 0:
                        continue
                    review = ("pending" if requires_confirmation
                              else _text(data.get("review_state")) or "pending")
                    if review not in REVIEW_STATES:
                        review = "pending"
                    cursor = connection.execute(
                        "UPDATE case_injuries SET normalized_text=?,status=?,review_state=?,"
                        "applied_rule_codes_json=?,rule_note=?,version=version+1,updated_at=?"
                        " WHERE case_id=? AND id=?",
                        (normalized, status, review, rules, note, timestamp, case_id, injury_id),
                    )
                    injuries_written += int(cursor.rowcount or 0)
                else:
                    continue
                if requires_confirmation:
                    pending += 1
                if issues:
                    issue_notes.append("；".join(
                        _text(one.get("reason")) or _text(one.get("code"))
                        for one in issues if isinstance(one, Mapping)
                    ))
            if issue_notes:
                self._event(connection, case_id, "normalization_issue", "术语整理待确认",
                            " | ".join(issue_notes), actor, timestamp)
            if fields_written or injuries_written:
                self._event(connection, case_id, "normalized", "术语整理完成",
                            "字段 {0} 项、损伤 {1} 条；需确认 {2} 项".format(
                                fields_written, injuries_written, pending),
                            actor, timestamp)
        return {"fields": fields_written, "injuries": injuries_written,
                "requires_confirmation": pending}

    # ------------------------------------------------------------------
    # 损伤
    # ------------------------------------------------------------------
    def upsert_injuries(self, case_id: int, injuries: Sequence[Any], replace_existing: bool,
                        expected_version: int, actor: str = "系统") -> List[Dict[str, Any]]:
        """写入提取到的损伤：**每条独立一行**，绝不合并（INV-4 / R-16 / R-36）。"""

        timestamp = utc_now()
        prepared: List[Dict[str, Any]] = []
        for index, item in enumerate(injuries, start=1):
            data = _as_mapping(item)
            status = _text(data.get("status")) or "recorded"
            if status not in FIELD_STATUSES:
                status = "recorded"
            review_state = _text(data.get("review_state")) or "pending"
            if review_state not in REVIEW_STATES:
                review_state = "pending"
            prepared.append({
                "seq": int(data.get("seq") or index),
                "body_part": _text(data.get("body_part")),
                "side": _text(data.get("side")),
                "injury_type": _text(data.get("injury_type")),
                "count": _text(data.get("count")),
                "length": _text(data.get("length")),
                "width": _text(data.get("width")),
                "size_unit": _text(data.get("size_unit")),
                "morphology": _text(data.get("morphology")),
                "other_traits": _text(data.get("other_traits")),
                "raw_text": _text(data.get("raw_text")),
                "normalized_text": _text(data.get("normalized_text")),
                "status": status,
                "review_state": review_state,
                "confidence": _float_or_none(data.get("confidence")),
                "source_material_id": _int_or_none(data.get("source_material_id")),
                "source_page": int(data.get("source_page") or 0),
                "source_bbox_json": _json(list(data.get("source_bbox") or [])),
                "source_block_id": _int_or_none(data.get("source_block_id")),
                "applied_rule_codes_json": _json(list(data.get("applied_rule_codes") or [])),
                "rule_note": _text(data.get("rule_note")),
            })

        with self.connect() as connection:
            self._claim(connection, case_id, expected_version, timestamp)
            if replace_existing:
                connection.execute(
                    "DELETE FROM case_injuries WHERE case_id=? AND review_state<>'confirmed'",
                    (case_id,),
                )
            for item in prepared:
                columns = ",".join(_INJURY_COLUMNS)
                placeholders = ",".join("?" for _ in _INJURY_COLUMNS)
                connection.execute(
                    "INSERT INTO case_injuries (case_id," + columns + ",version,created_at,updated_at)"
                    " VALUES (?," + placeholders + ",1,?,?)",
                    [case_id] + [item[column] for column in _INJURY_COLUMNS] + [timestamp, timestamp],
                )
            if prepared:
                self._event(connection, case_id, "injuries_extracted", "损伤提取完成",
                            "共 {0} 条，逐条独立保存".format(len(prepared)), actor, timestamp)
        return self.list_injuries(case_id)

    def list_injuries(self, case_id: int) -> List[Dict[str, Any]]:
        """损伤列表（按 `seq`）。"""

        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM case_injuries WHERE case_id=? ORDER BY seq,id", (case_id,)
            ).fetchall()
        return [_injury_row(row) for row in rows]

    def create_injury(self, case_id: int, payload: Mapping[str, Any], expected_version: int,
                      actor: str = "复核人") -> Dict[str, Any]:
        """新增一条损伤。"""

        data = _as_mapping(payload)
        timestamp = utc_now()
        with self.connect() as connection:
            self._claim(connection, case_id, expected_version, timestamp)
            seq = int(data.get("seq") or 0)
            if seq <= 0:
                row = connection.execute(
                    "SELECT MAX(seq) AS total FROM case_injuries WHERE case_id=?", (case_id,)
                ).fetchone()
                seq = int(row["total"] or 0) + 1
            status = _text(data.get("status")) or "recorded"
            if status not in FIELD_STATUSES:
                status = "recorded"
            review_state = _text(data.get("review_state")) or "pending"
            if review_state not in REVIEW_STATES:
                review_state = "pending"
            cursor = connection.execute(
                "INSERT INTO case_injuries (case_id,seq,body_part,side,injury_type,count,length,width,"
                "size_unit,morphology,other_traits,raw_text,normalized_text,status,review_state,"
                "confidence,source_material_id,source_page,source_bbox_json,source_block_id,"
                "applied_rule_codes_json,rule_note,version,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'',1,?,?)",
                (
                    case_id, seq, _text(data.get("body_part")), _text(data.get("side")),
                    _text(data.get("injury_type")), _text(data.get("count")),
                    _text(data.get("length")), _text(data.get("width")),
                    _text(data.get("size_unit")), _text(data.get("morphology")),
                    _text(data.get("other_traits")), _text(data.get("raw_text")),
                    _text(data.get("normalized_text")), status, review_state,
                    _float_or_none(data.get("confidence")),
                    _int_or_none(data.get("source_material_id")),
                    int(data.get("source_page") or 0),
                    _json(list(data.get("source_bbox") or [])),
                    _int_or_none(data.get("source_block_id")),
                    _json(list(data.get("applied_rule_codes") or [])),
                    timestamp, timestamp,
                ),
            )
            injury_id = int(cursor.lastrowid)
            self._event(connection, case_id, "injury_created", "损伤已新增",
                        "第 {0} 条：{1}".format(seq, _text(data.get("raw_text"))
                                                or _text(data.get("body_part"))), actor, timestamp)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM case_injuries WHERE id=?", (injury_id,)).fetchone()
        return _injury_row(row) if row else {}

    def update_injury(self, case_id: int, injury_id: int, changes: Mapping[str, Any],
                      expected_version: int, actor: str = "复核人") -> Dict[str, Any]:
        """修改一条损伤。"""

        data = _as_mapping(changes)
        timestamp = utc_now()
        allowed = {
            "seq": "seq", "body_part": "body_part", "side": "side", "injury_type": "injury_type",
            "count": "count", "length": "length", "width": "width", "size_unit": "size_unit",
            "morphology": "morphology", "other_traits": "other_traits", "raw_text": "raw_text",
            "normalized_text": "normalized_text", "status": "status", "review_state": "review_state",
            "confidence": "confidence", "source_material_id": "source_material_id",
            "source_page": "source_page", "rule_note": "rule_note",
        }
        assignments: List[str] = []
        params: List[Any] = []
        changed: List[str] = []
        for key, column in allowed.items():
            if key not in data or data[key] is None:
                continue
            value = data[key]
            if key in ("seq", "source_page"):
                value = int(value)
            elif key in ("confidence",):
                value = _float_or_none(value)
            elif key in ("source_material_id",):
                value = _int_or_none(value)
            else:
                value = _text(value)
            assignments.append(column + "=?")
            params.append(value)
            changed.append(key)
        if "source_bbox" in data and data["source_bbox"] is not None:
            assignments.append("source_bbox_json=?")
            params.append(_json(list(data["source_bbox"])))
            changed.append("source_bbox")
        if not assignments:
            raise InvalidTransition("没有需要修改的损伤属性")

        with self.connect() as connection:
            self._claim(connection, case_id, expected_version, timestamp)
            existing = connection.execute(
                "SELECT * FROM case_injuries WHERE id=? AND case_id=?", (injury_id, case_id)
            ).fetchone()
            if existing is None:
                raise NotFound("损伤不存在：{0}".format(injury_id))
            assignments.append("version=version+1")
            assignments.append("updated_at=?")
            params.append(timestamp)
            connection.execute(
                "UPDATE case_injuries SET " + ",".join(assignments) + " WHERE id=?", params + [injury_id]
            )
            self._event(connection, case_id, "injury_updated", "损伤已修改",
                        "第 {0} 条：{1}".format(existing["seq"], "、".join(changed))
                        + ("；备注：" + _text(data.get("note")) if data.get("note") else ""),
                        actor, timestamp)
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM case_injuries WHERE id=?", (injury_id,)).fetchone()
        return _injury_row(row) if row else {}

    def delete_injury(self, case_id: int, injury_id: int, expected_version: int) -> None:
        """删除一条损伤。"""

        timestamp = utc_now()
        with self.connect() as connection:
            self._claim(connection, case_id, expected_version, timestamp)
            existing = connection.execute(
                "SELECT seq FROM case_injuries WHERE id=? AND case_id=?", (injury_id, case_id)
            ).fetchone()
            if existing is None:
                raise NotFound("损伤不存在：{0}".format(injury_id))
            connection.execute("DELETE FROM case_injuries WHERE id=?", (injury_id,))
            self._event(connection, case_id, "injury_deleted", "损伤已删除",
                        "第 {0} 条（id={1}）".format(existing["seq"], injury_id), "复核人", timestamp)

    def reorder_injuries(self, case_id: int, ordered_ids: Sequence[int], expected_version: int,
                         actor: str = "复核人") -> List[Dict[str, Any]]:
        """按给定顺序重排损伤（R-7 工作顺序调整）。"""

        timestamp = utc_now()
        with self.connect() as connection:
            self._claim(connection, case_id, expected_version, timestamp)
            rows = connection.execute(
                "SELECT id FROM case_injuries WHERE case_id=? ORDER BY seq,id", (case_id,)
            ).fetchall()
            existing = [int(row["id"]) for row in rows]
            requested = [int(item) for item in ordered_ids]
            unknown = [item for item in requested if item not in existing]
            if unknown:
                raise NotFound("损伤不存在：{0}".format("、".join(str(item) for item in unknown)))
            final = requested + [item for item in existing if item not in requested]
            for index, injury_id in enumerate(final, start=1):
                connection.execute(
                    "UPDATE case_injuries SET seq=?,updated_at=? WHERE id=? AND case_id=?",
                    (index, timestamp, injury_id, case_id),
                )
            self._event(connection, case_id, "injuries_reordered", "损伤顺序已调整",
                        "、".join(str(item) for item in final), actor, timestamp)
        return self.list_injuries(case_id)

    # ------------------------------------------------------------------
    # 术语规则
    # ------------------------------------------------------------------
    def list_terminology_rules(self, active_only: bool = False, search: str = "") -> List[Dict[str, Any]]:
        """规则列表（按 `priority, code` 升序）。"""

        clauses: List[str] = []
        params: List[Any] = []
        if active_only:
            clauses.append("active=1")
        if search:
            like = "%" + str(search).strip() + "%"
            clauses.append("(code LIKE ? OR shorthand LIKE ? OR pattern LIKE ? OR scope_column LIKE ?)")
            params.extend([like, like, like, like])
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM terminology_rules" + where + " ORDER BY priority,code", params
            ).fetchall()
        return [_rule_row(row) for row in rows]

    def get_terminology_rule(self, rule_id: int) -> Optional[Dict[str, Any]]:
        """单条规则。"""

        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM terminology_rules WHERE id=?", (rule_id,)).fetchone()
        return _rule_row(row) if row else None

    def create_terminology_rule(self, payload: Mapping[str, Any], actor: str = "复核人") -> Dict[str, Any]:
        """新增规则（`code` 全局唯一）。"""

        data = _as_mapping(payload)
        timestamp = utc_now()
        code = _text(data.get("code"))
        if not code:
            raise InvalidTransition("规则编码不能为空")
        values = self._rule_values(data)
        try:
            with self.connect() as connection:
                cursor = connection.execute(
                    "INSERT INTO terminology_rules (" + ",".join(_RULE_COLUMNS)
                    + ",version,created_at,updated_at) VALUES ("
                    + ",".join("?" for _ in _RULE_COLUMNS) + ",1,?,?)",
                    values + [timestamp, timestamp],
                )
                rule_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as error:
            if "code" in str(error):
                raise InvalidTransition("规则编码已存在：{0}".format(code)) from error
            raise
        return self.get_terminology_rule(rule_id) or {}

    @staticmethod
    def _rule_values(data: Mapping[str, Any]) -> List[Any]:
        def _field(name: str, default: Any) -> Any:
            return data[name] if name in data and data[name] is not None else default

        return [
            _text(data.get("code")),
            _text(_field("shorthand", "")),
            _text(_field("pattern", "")),
            _text(_field("match_type", "literal")) or "literal",
            _text(_field("template", "")),
            _text(_field("scope_field_key", "")),
            _text(_field("scope_column", "")),
            _text(_field("scope_writer", "")),
            _text(_field("boundary_note", "")),
            _json(list(_field("require_groups", []) or [])),
            _text(_field("missing_group_note", "")),
            int(bool(_field("requires_confirmation", False))),
            int(_field("priority", 100)),
            int(bool(_field("active", True))),
        ]

    def update_terminology_rule(self, rule_id: int, changes: Mapping[str, Any],
                                expected_version: int, actor: str = "复核人") -> Dict[str, Any]:
        """修改规则（乐观锁归属 `terminology_rules.version`）。"""

        data = _as_mapping(changes)
        timestamp = utc_now()
        allowed = {
            "shorthand": "shorthand", "pattern": "pattern", "match_type": "match_type",
            "template": "template", "scope_field_key": "scope_field_key",
            "scope_column": "scope_column", "scope_writer": "scope_writer",
            "boundary_note": "boundary_note", "missing_group_note": "missing_group_note",
            "priority": "priority",
        }
        assignments: List[str] = []
        params: List[Any] = []
        changed: List[str] = []
        for key, column in allowed.items():
            if key not in data or data[key] is None:
                continue
            value = int(data[key]) if key == "priority" else _text(data[key])
            assignments.append(column + "=?")
            params.append(value)
            changed.append(key)
        if "require_groups" in data and data["require_groups"] is not None:
            assignments.append("require_groups_json=?")
            params.append(_json(list(data["require_groups"])))
            changed.append("require_groups")
        for key, column in (("requires_confirmation", "requires_confirmation"), ("active", "active")):
            if key in data and data[key] is not None:
                assignments.append(column + "=?")
                params.append(int(bool(data[key])))
                changed.append(key)
        if not assignments:
            raise InvalidTransition("没有需要修改的规则属性")
        assignments.append("updated_at=?")
        params.append(timestamp)
        with self.connect() as connection:
            self._claim_rule(connection, rule_id, expected_version, timestamp)
            connection.execute(
                "UPDATE terminology_rules SET " + ",".join(assignments) + " WHERE id=?",
                params + [rule_id],
            )
        return self.get_terminology_rule(rule_id) or {}

    def delete_terminology_rule(self, rule_id: int, expected_version: int) -> None:
        """删除规则。"""

        timestamp = utc_now()
        with self.connect() as connection:
            self._claim_rule(connection, rule_id, expected_version, timestamp)
            connection.execute("DELETE FROM terminology_rules WHERE id=?", (rule_id,))

    def import_seed_rules(self) -> int:
        """从 `constants.TERMINOLOGY_SEED` 导入种子规则；已存在则更新为种子值。"""

        timestamp = utc_now()
        count = 0
        with self.connect() as connection:
            for rule in TERMINOLOGY_SEED:
                data = dict(rule)
                data.setdefault("require_groups", [])
                values = self._rule_values(data)
                existing = connection.execute(
                    "SELECT id FROM terminology_rules WHERE code=?", (_text(data.get("code")),)
                ).fetchone()
                if existing is None:
                    connection.execute(
                        "INSERT INTO terminology_rules (" + ",".join(_RULE_COLUMNS)
                        + ",version,created_at,updated_at) VALUES ("
                        + ",".join("?" for _ in _RULE_COLUMNS) + ",1,?,?)",
                        values + [timestamp, timestamp],
                    )
                else:
                    assignments = ",".join(
                        column + "=?" for column in _RULE_COLUMNS if column != "code"
                    )
                    params = [value for column, value in zip(_RULE_COLUMNS, values) if column != "code"]
                    connection.execute(
                        "UPDATE terminology_rules SET " + assignments + ",updated_at=? WHERE code=?",
                        params + [timestamp, _text(data.get("code"))],
                    )
                count += 1
        return count

    # ------------------------------------------------------------------
    # 文稿
    # ------------------------------------------------------------------
    def next_document_version(self, case_id: int, doc_type: str) -> int:
        """下一个文稿版本号（R-51）。"""

        with self.connect() as connection:
            row = connection.execute(
                "SELECT MAX(version_no) AS total FROM documents WHERE case_id=? AND doc_type=?",
                (case_id, doc_type),
            ).fetchone()
        return int(row["total"] or 0) + 1

    def create_document(self, case_id: int, payload: Mapping[str, Any], expected_version: int,
                        actor: str = "系统") -> Dict[str, Any]:
        """登记文稿版本。

        `doc_status='pending_issue'` 时先检查 `open` 阻断项 → `BlockedByChecklist`（INV-5）。
        """

        data = _as_mapping(payload)
        timestamp = utc_now()
        doc_type = _text(data.get("doc_type")) or "report"
        doc_status = _text(data.get("doc_status")) or "internal_draft"
        if doc_status not in DOC_STATUSES:
            raise InvalidTransition("未知文稿状态：{0}".format(doc_status))

        if doc_status == "pending_issue":
            blocking = self.open_blocking_items(case_id)
            if blocking:
                raise BlockedByChecklist(
                    "、".join("{0}（{1}）".format(item["title"] or item["code"], item["code"])
                              for item in blocking[:5])
                )

        with self.connect() as connection:
            self._claim(connection, case_id, expected_version, timestamp)
            # B7：版本号在“取号 → 插入”之间存在 TOCTOU。极端交错下
            # `UNIQUE(case_id, doc_type, version_no)` 会被撞到，此时重取一次号而不是 500。
            document_id = 0
            for attempt in (1, 2):
                row = connection.execute(
                    "SELECT MAX(version_no) AS total FROM documents WHERE case_id=? AND doc_type=?",
                    (case_id, doc_type),
                ).fetchone()
                version_no = int(row["total"] or 0) + 1
                values = [
                    doc_type,
                    version_no,
                    doc_status,
                    _text(data.get("template_version")) or DEFAULT_TEMPLATE_VERSION,
                    _text(data.get("term_rule_version")) or TERM_RULE_DEFAULT_VERSION,
                    _text(data.get("template_path")),
                    _text(data.get("docx_path")),
                    _text(data.get("pdf_path")),
                    _text(data.get("render_engine")),
                    _json(list(data.get("unresolved") or [])),
                    _text(data.get("blocked_reason")),
                    _text(data.get("note")),
                ]
                try:
                    cursor = connection.execute(
                        "INSERT INTO documents (case_id," + ",".join(_DOCUMENT_COLUMNS)
                        + ",approved_at,created_at,updated_at) VALUES (?,"
                        + ",".join("?" for _ in _DOCUMENT_COLUMNS) + ",NULL,?,?)",
                        [case_id] + values + [timestamp, timestamp],
                    )
                except sqlite3.IntegrityError:
                    if attempt >= 2:
                        raise
                    continue
                document_id = int(cursor.lastrowid)
                break
            # 3.2：新版本生成使已审核的旧版本失效
            connection.execute(
                "UPDATE documents SET doc_status='superseded',updated_at=?"
                " WHERE case_id=? AND doc_type=? AND doc_status='approved' AND id<>?",
                (timestamp, case_id, doc_type, document_id),
            )
            self._refresh_case_doc_status(connection, case_id)
            self._event(connection, case_id, "document_created", "文稿已生成",
                        "{0} v{1}（{2}），引擎 {3}".format(
                            doc_type, version_no, DOC_STATUS_LABELS.get(doc_status, doc_status),
                            _text(data.get("render_engine")) or "未记录"),
                        actor, timestamp)
        return self.get_document(document_id) or {}

    def list_documents(self, case_id: int) -> List[Dict[str, Any]]:
        """文稿版本列表。"""

        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM documents WHERE case_id=? ORDER BY doc_type,version_no DESC,id DESC",
                (case_id,),
            ).fetchall()
        return [_document_row(row) for row in rows]

    def get_document(self, document_id: int) -> Optional[Dict[str, Any]]:
        """单个文稿。"""

        with self.connect() as connection:
            row = connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        return _document_row(row) if row else None

    def approve_document(self, document_id: int, expected_version: int, note: str = "",
                         actor: str = "鉴定人") -> Dict[str, Any]:
        """标记文稿已审核（乐观锁归属所属案件，第 4 节）。

        **D5 / R-28 门槛**：存在 `open` 且 `severity='blocking'` 的校核项时拒绝审核
        （`BlockedByChecklist` → HTTP 409，detail 列出具体阻断项）。因此
        `internal_draft → approved → deliver` 不能绕过阻断门；`deliver` 仍只接受
        `doc_status='approved'`（INV-8，`workflow.run_delivery`）。
        """

        timestamp = utc_now()
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            if row is None:
                raise NotFound("文稿不存在：{0}".format(document_id))
            case_id = int(row["case_id"])
            if row["doc_status"] == "approved":
                raise InvalidTransition("该文稿已是已审核状态")
            if row["doc_status"] == "superseded":
                raise InvalidTransition("已被替代的文稿不能重新审核，请生成新版本")
            self._claim(connection, case_id, expected_version, timestamp)
            # D5：先 `_claim` 再查阻断项（同一连接，不在写事务期间另开连接）；
            # 抛出异常时事务回滚，版本号不会被这一次失败的审核占用。
            blocking_rows = connection.execute(
                "SELECT code,title FROM checklist_items WHERE case_id=?"
                " AND severity='blocking' AND status='open' ORDER BY category,id",
                (case_id,),
            ).fetchall()
            if blocking_rows:
                raise BlockedByChecklist("、".join(
                    "{0}（{1}）".format(_text(item["title"]) or _text(item["code"]),
                                       _text(item["code"]))
                    for item in blocking_rows[:5])
                    + ("；共 {0} 条未解决（R-28/INV-5）".format(len(blocking_rows))
                       if len(blocking_rows) > 5 else ""))
            connection.execute(
                "UPDATE documents SET doc_status='approved',approved_at=?,note=?,updated_at=?"
                " WHERE id=?",
                (timestamp, _text(note), timestamp, document_id),
            )
            self._refresh_case_doc_status(connection, case_id)
            self._event(connection, case_id, "document_approved", "文稿已审核",
                        "文稿 v{0}".format(row["version_no"])
                        + ("；备注：" + _text(note) if note else ""), actor, timestamp)
        return self.get_document(document_id) or {}

    def update_document_assets(self, document_id: int, changes: Mapping[str, Any],
                               expected_version: int = 0, actor: str = "系统") -> Dict[str, Any]:
        """补写文稿的产物路径与渲染信息（乐观锁归属**所属案件**，第 4 节）。

        只允许改写 `template_path` / `docx_path` / `pdf_path` / `render_engine` /
        `unresolved` / `blocked_reason` / `note`；**绝不改写 `doc_status`**
        （状态只能通过 `create_document` / `approve_document` / 失效流程变更）。
        """

        data = _as_mapping(changes)
        timestamp = utc_now()
        allowed = {
            "template_path": "template_path",
            "docx_path": "docx_path",
            "pdf_path": "pdf_path",
            "render_engine": "render_engine",
            "blocked_reason": "blocked_reason",
            "note": "note",
        }
        assignments: List[str] = []
        params: List[Any] = []
        changed: List[str] = []
        for key, column in allowed.items():
            if key not in data or data[key] is None:
                continue
            assignments.append(column + "=?")
            params.append(_text(data[key]))
            changed.append(key)
        if "unresolved" in data and data["unresolved"] is not None:
            assignments.append("unresolved_json=?")
            params.append(_json(list(data["unresolved"])))
            changed.append("unresolved")
        if not assignments:
            raise InvalidTransition("没有需要修改的文稿属性")

        with self.connect() as connection:
            row = connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            if row is None:
                raise NotFound("文稿不存在：{0}".format(document_id))
            case_id = int(row["case_id"])
            self._claim(connection, case_id, expected_version, timestamp)
            assignments.append("updated_at=?")
            params.append(timestamp)
            connection.execute("UPDATE documents SET " + ",".join(assignments) + " WHERE id=?",
                               params + [document_id])
            self._event(connection, case_id, "document_assets_updated", "文稿产物已更新",
                        "文稿 v{0}：{1}".format(row["version_no"], "、".join(changed)),
                        actor, timestamp)
        return self.get_document(document_id) or {}

    def supersede_documents(self, case_id: int, keep_document_id: Optional[int], reason: str,
                            actor: str = "系统") -> List[int]:
        """把案件内文稿置为 `superseded`（事实修正触发，INV-6）。"""

        timestamp = utc_now()
        with self.connect() as connection:
            ids = self._supersede_documents(connection, case_id, keep_document_id, timestamp)
            self._refresh_case_doc_status(connection, case_id)
            if ids:
                self._event(connection, case_id, "documents_superseded", "文稿复核状态已失效",
                            "共 {0} 份：{1}".format(len(ids), reason), actor, timestamp)
        return ids

    # ------------------------------------------------------------------
    # 校核清单
    # ------------------------------------------------------------------
    def replace_checklist(self, case_id: int, document_id: Optional[int],
                          items: Sequence[Any], actor: str = "系统") -> List[Dict[str, Any]]:
        """写入校核结果。

        重复运行校核时：删除旧的 `open` 项后重新写入；已被用户 `resolved` / `waived`
        的 `(code, target_type, target_id)` 不再重复出现，保留人工处置结论。

        **不跨文稿版本累积**（W1+W2 收口修复）：同一案件、同一 `doc_type` 下，只有
        **最新被校核的那版文稿**保留 `open` 的 doc 级项；旧版本文稿遗留的 `open` 项
        按规则收敛为 `status='resolved'` 并写明“已由 vN 校核替代”。收敛只改 `open`
        行——已 `waived`（有豁免理由）或人工 `resolved` 的历史记录原样保留，供追溯。
        案件级项（`document_id IS NULL`）仍是原来的“同键删除重写”语义。
        """

        timestamp = utc_now()
        prepared: List[Dict[str, Any]] = []
        for item in items:
            data = _as_mapping(item)
            severity = _text(data.get("severity")) or "warning"
            if severity not in CHECK_SEVERITIES:
                severity = "warning"
            prepared.append({
                "category": _text(data.get("category")) or "content",
                "severity": severity,
                "code": _text(data.get("code")),
                "title": _text(data.get("title")),
                "detail": _text(data.get("detail")),
                "target_type": _text(data.get("target_type")),
                "target_id": int(data.get("target_id") or 0),
                "source_json": _json(dict(_as_mapping(data.get("source")))),
                "suggestion": _text(data.get("suggestion")),
            })

        with self.connect() as connection:
            if document_id is None:
                where = "case_id=? AND document_id IS NULL"
                params: Tuple[Any, ...] = (case_id,)
            else:
                where = "case_id=? AND document_id=?"
                params = (case_id, int(document_id))

            # 收敛旧版本文稿遗留的 open 项：同案件 + 同 doc_type 的其它文稿不再保留
            # open 的 doc 级项（否则每重新生成一版文稿，清单就整体再叠一遍）。
            converged = 0
            if document_id is not None:
                current = connection.execute(
                    "SELECT doc_type,version_no FROM documents WHERE id=? AND case_id=?",
                    (int(document_id), case_id),
                ).fetchone()
                if current is not None:
                    converged = int(connection.execute(
                        "UPDATE checklist_items SET status='resolved',note=?,resolved_by=?,"
                        "resolved_at=?,updated_at=?"
                        " WHERE case_id=? AND status='open' AND document_id IS NOT NULL"
                        " AND document_id<>?"
                        " AND document_id IN (SELECT id FROM documents"
                        " WHERE case_id=? AND doc_type=?)",
                        ("已由 v{0} 校核替代".format(int(current["version_no"] or 0)),
                         actor, timestamp, timestamp, case_id, int(document_id), case_id,
                         _text(current["doc_type"])),
                    ).rowcount or 0)

            handled = {
                (row["code"], row["target_type"], int(row["target_id"] or 0))
                for row in connection.execute(
                    "SELECT code,target_type,target_id FROM checklist_items"
                    " WHERE " + where + " AND status<>'open'", params
                ).fetchall()
            }
            connection.execute(
                "DELETE FROM checklist_items WHERE " + where + " AND status='open'", params
            )
            inserted = 0
            for item in prepared:
                key = (item["code"], item["target_type"], item["target_id"])
                if key in handled:
                    continue
                connection.execute(
                    "INSERT INTO checklist_items (case_id,document_id,category,severity,code,title,"
                    "detail,status,target_type,target_id,source_json,suggestion,resolved_by,note,"
                    "resolved_at,created_at,updated_at)"
                    " VALUES (?,?,?,?,?,?,?,'open',?,?,?,?,'','',NULL,?,?)",
                    (
                        case_id, document_id, item["category"], item["severity"], item["code"],
                        item["title"], item["detail"], item["target_type"], item["target_id"],
                        item["source_json"], item["suggestion"], timestamp, timestamp,
                    ),
                )
                inserted += 1
            self._event(connection, case_id, "check_completed", "校核完成",
                        "写入 {0} 条校核项，保留人工处置 {1} 条，收敛旧版本 {2} 条".format(
                            inserted, len(handled), converged),
                        actor, timestamp)
        return self.list_checklist(case_id)

    def list_checklist(self, case_id: int, status: str = "", category: str = "") -> List[Dict[str, Any]]:
        """校核清单（阻断优先）。"""

        clauses = ["case_id=?"]
        params: List[Any] = [case_id]
        if status:
            clauses.append("status=?")
            params.append(status)
        if category:
            clauses.append("category=?")
            params.append(category)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM checklist_items WHERE " + " AND ".join(clauses)
                + " ORDER BY " + _SEVERITY_ORDER_SQL + ",category,id",
                params,
            ).fetchall()
        return [_checklist_row(row) for row in rows]

    def get_checklist_item(self, item_id: int) -> Optional[Dict[str, Any]]:
        """单条校核项。"""

        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM checklist_items WHERE id=?", (item_id,)).fetchone()
        return _checklist_row(row) if row else None

    def update_checklist_item(self, item_id: int, changes: Mapping[str, Any],
                              expected_version: int, actor: str = "复核人") -> Dict[str, Any]:
        """处理 / 豁免校核项（乐观锁归属所属案件）。"""

        data = _as_mapping(changes)
        timestamp = utc_now()
        status = _text(data.get("status"))
        if status not in CHECK_ITEM_STATUSES:
            raise InvalidTransition("未知校核项状态：{0}".format(status))
        note = _text(data.get("note"))
        if status == "waived" and not note.strip():
            raise InvalidTransition("豁免校核项必须填写理由")
        resolved_by = _text(data.get("resolved_by")) or actor
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM checklist_items WHERE id=?", (item_id,)).fetchone()
            if row is None:
                raise NotFound("校核项不存在：{0}".format(item_id))
            case_id = int(row["case_id"])
            self._claim(connection, case_id, expected_version, timestamp)
            connection.execute(
                "UPDATE checklist_items SET status=?,note=?,resolved_by=?,resolved_at=?,updated_at=?"
                " WHERE id=?",
                (status, note, resolved_by,
                 None if status == "open" else timestamp, timestamp, item_id),
            )
            self._event(connection, case_id, "checklist_updated", "校核项已处理",
                        "{0}（{1}）→ {2}".format(row["title"] or row["code"], row["code"], status)
                        + ("；理由：" + note if note else ""), resolved_by, timestamp)
        return self.get_checklist_item(item_id) or {}

    def open_blocking_items(self, case_id: int) -> List[Dict[str, Any]]:
        """未解决的阻断校核项（R-28 / INV-5）。"""

        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM checklist_items WHERE case_id=? AND severity='blocking'"
                " AND status='open' ORDER BY category,id",
                (case_id,),
            ).fetchall()
        return [_checklist_row(row) for row in rows]

    # ------------------------------------------------------------------
    # 修正
    # ------------------------------------------------------------------
    def create_correction(self, case_id: int, payload: Mapping[str, Any], expected_version: int,
                          actor: str = "复核人") -> Dict[str, Any]:
        """记录并应用用户修正（R-38～R-41）。"""

        data = _as_mapping(payload)
        timestamp = utc_now()
        kind = _text(data.get("kind"))
        target_type = _text(data.get("target_type"))
        target_id = int(data.get("target_id") or 0)
        before_text = _text(data.get("before_text"))
        after_text = _text(data.get("after_text"))
        reason = _text(data.get("reason"))
        scope = _text(data.get("scope")) or "case_only"
        scope_value = _text(data.get("scope_value"))
        confirmed_by = _text(data.get("confirmed_by")) or actor
        apply_to_case = bool(data.get("apply_to_case", True))

        # INV-7：非 case_only 必须写明适用范围，且绝不跨案件改写
        if scope not in CORRECTION_SCOPES:
            raise InvalidTransition("未知修正范围：{0}".format(scope))
        if scope != "case_only" and not scope_value.strip():
            raise InvalidTransition("非 case_only 范围必须填写适用范围")
        if before_text == after_text:
            raise InvalidTransition("修正前后的内容不能相同")

        affected: List[Any] = []
        invalidated: List[Any] = []
        with self.connect() as connection:
            self._claim(connection, case_id, expected_version, timestamp)
            if apply_to_case:
                if target_type == "field":
                    row = connection.execute(
                        "SELECT * FROM case_fields WHERE case_id=? AND id=?",
                        (case_id, target_id),
                    ).fetchone()
                    if row is None:
                        raise NotFound("字段不存在：id={0}".format(target_id))
                    connection.execute(
                        "UPDATE case_fields SET confirmed_value=?,raw_text=CASE WHEN raw_text=''"
                        " THEN ? ELSE raw_text END,version=version+1,updated_at=?"
                        " WHERE case_id=? AND id=?",
                        (after_text, before_text, timestamp, case_id, target_id),
                    )
                    invalidated = self._invalidate_in_connection(
                        connection, case_id, "field", target_id, timestamp, reason)
                elif target_type == "injury":
                    row = connection.execute(
                        "SELECT * FROM case_injuries WHERE case_id=? AND id=?",
                        (case_id, target_id),
                    ).fetchone()
                    if row is None:
                        raise NotFound("损伤不存在：id={0}".format(target_id))
                    connection.execute(
                        "UPDATE case_injuries SET raw_text=?,version=version+1,updated_at=?"
                        " WHERE case_id=? AND id=?",
                        (after_text, timestamp, case_id, target_id),
                    )
                    invalidated = self._invalidate_in_connection(
                        connection, case_id, "injury", target_id, timestamp, reason)
                elif target_type == "terminology_rule":
                    row = connection.execute(
                        "SELECT * FROM terminology_rules WHERE id=?", (target_id,)).fetchone()
                    if row is None:
                        raise NotFound("术语规则不存在：id={0}".format(target_id))
                    connection.execute(
                        "UPDATE terminology_rules SET template=?,version=version+1,updated_at=?"
                        " WHERE id=?",
                        (after_text, timestamp, target_id),
                    )
                elif target_type == "document":
                    row = connection.execute(
                        "SELECT * FROM documents WHERE case_id=? AND id=?",
                        (case_id, target_id),
                    ).fetchone()
                    if row is None:
                        raise NotFound("文稿不存在：id={0}".format(target_id))
                    connection.execute(
                        "UPDATE documents SET doc_status='superseded',updated_at=? WHERE id=?",
                        (timestamp, target_id),
                    )
                    affected.append({"type": "document", "id": target_id})
                # 事实变化 → 旧复核状态失效、旧文稿作废（INV-6 / R-40）
                if target_type in ("field", "injury", "case"):
                    affected.extend(self._supersede_documents(connection, case_id, None, timestamp))
            self._refresh_case_doc_status(connection, case_id)
            cursor = connection.execute(
                "INSERT INTO corrections (case_id,kind,target_type,target_id,before_text,after_text,"
                "reason,confirmed_by,scope,scope_value,affected_json,invalidated_json,applied,created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    case_id, kind, target_type, target_id, before_text, after_text, reason,
                    confirmed_by, scope, scope_value, _json(affected), _json(invalidated),
                    1 if apply_to_case else 0, timestamp,
                ),
            )
            correction_id = int(cursor.lastrowid)
            self._event(connection, case_id, "correction_recorded", "修正已记录",
                        "{0} → {1}；原因：{2}".format(before_text or "（空）", after_text or "（空）", reason)
                        + ("；范围：{0}={1}".format(scope, scope_value) if scope_value else ""),
                        confirmed_by, timestamp)
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM corrections WHERE id=?", (correction_id,)).fetchone()
        return _correction_row(row) if row else {}

    @staticmethod
    def _invalidate_in_connection(connection: sqlite3.Connection, case_id: int, target_type: str,
                                  target_id: int, timestamp: str, reason: str) -> List[Dict[str, Any]]:
        if target_type == "field":
            connection.execute(
                "UPDATE case_fields SET review_state='pending',version=version+1,updated_at=?"
                " WHERE case_id=? AND id=?", (timestamp, case_id, target_id))
            rows = connection.execute(
                "SELECT id,field_key,field_label FROM case_fields WHERE case_id=? AND id=?",
                (case_id, target_id)).fetchall()
        else:
            connection.execute(
                "UPDATE case_injuries SET review_state='pending',version=version+1,updated_at=?"
                " WHERE case_id=? AND id=?", (timestamp, case_id, target_id))
            rows = connection.execute(
                "SELECT id,body_part,raw_text FROM case_injuries WHERE case_id=? AND id=?",
                (case_id, target_id)).fetchall()
        return [{"type": target_type, "id": int(row["id"]),
                 "reason": reason, "label": " ".join(str(item) for item in tuple(row)[1:])}
                for row in rows]

    def list_corrections(self, case_id: int) -> List[Dict[str, Any]]:
        """修正记录（最新在前）。"""

        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM corrections WHERE case_id=? ORDER BY id DESC", (case_id,)
            ).fetchall()
        return [_correction_row(row) for row in rows]
