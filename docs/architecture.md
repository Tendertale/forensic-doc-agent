# 架构与接口契约（冻结基线 v1.0）

本文件是**并行开发的唯一契约**。开发、Review、测试子智能体必须先读完
`docs/requirements.md` 与本文件，再动手。

- 分层、状态机与接口风格复用自参考项目
  [`Tendertale/hotspot-workbench`](https://github.com/Tendertale/hotspot-workbench)：
  Vue 3 + Vite + Tailwind 前端、FastAPI 路由层、Pydantic 校验层、
  SQLite 数据访问与审计层、乐观锁 `expected_version` + HTTP 409、
  `generated → modified → confirmed` 式状态机、`X-DeepSeek-API-Key` 仅内存传密钥、
  pytest + `TestClient` + `httpx.MockTransport`、`frontend/dist` 静态挂载。
- **契约冻结**：`backend/app/constants.py` 与 `backend/app/schemas.py` 已由主 Agent 写好，
  任何子智能体**不得修改这两个文件**；发现契约问题必须在交付说明中报告，由主 Agent 统一变更。

---

## 1. 目录结构与分层

```text
Vue 3 + Vite + Tailwind（frontend/）
        │ JSON / X-DeepSeek-API-Key（frontend/src/api.js，冻结）
        ▼
FastAPI 路由（backend/app/routes.py）
        ├── Pydantic 请求校验（schemas.py，冻结）
        ├── 案件流程编排（workflow.py）
        ├── OCR 引擎（ocr/）
        ├── 图像与 PDF（imaging.py）
        ├── 字段提取（extractor.py）
        ├── 术语规则引擎（terminology.py）
        ├── 文书生成（docgen.py）
        ├── 质量校核（checker.py）
        ├── 模型接口（llm.py，可选）
        └── SQLite 数据访问 + 审计（database.py，唯一持久化）
```

```text
backend/
├── __init__.py
├── app/
│   ├── __init__.py
│   ├── constants.py        [冻结] 枚举、字段目录、阶段、种子规则
│   ├── schemas.py          [冻结] Pydantic 请求模型
│   ├── storage.py                 案件目录与原件/副本分离
│   ├── database.py                SQLite schema、乐观锁、审计、统计
│   ├── imaging.py                 图像与 PDF 探测、渲染、预处理
│   ├── ocr/
│   │   ├── __init__.py            引擎注册表与选择
│   │   ├── base.py                OcrBlock / OcrEngine 协议 / 异常
│   │   ├── sidecar.py             离线 JSON 引擎（默认可离线运行）
│   │   └── paddle.py              PaddleOCR 引擎（预留接口，需下载）
│   ├── extractor.py               字段与损伤提取、状态推断、跨材料冲突
│   ├── terminology.py             简写 → 规范表述规则引擎
│   ├── checker.py                 五类校核规则
│   ├── docgen.py                  Word 渲染（docxtpl 优先 / 内置回退）+ PDF 导出
│   ├── llm.py                     可选模型抽取（默认关闭）
│   ├── seed.py                    演示案件与演示模板
│   ├── workflow.py                10 阶段编排
│   ├── routes.py                  HTTP 路由
│   └── main.py                    create_app()
└── tests/                         子智能体只在这里写测试
frontend/
├── index.html, package.json, vite.config.js, tailwind.config.js, postcss.config.js  [冻结]
├── src/
│   ├── main.js, styles.css        [冻结]
│   ├── api.js                     [冻结] 前端接口层
│   └── App.vue, components/*.vue  Vue 3 源码（需 `npm install` 才能构建：TODO-DL-3）
└── static/                        无构建依赖工作台，由 FastAPI 直接提供（**当前可实际运行的前端**）
    ├── index.html
    ├── workbench.js
    ├── workbench.css
    └── api.js                     fetch 封装，端点与 `src/api.js` 完全一致
docs/
├── requirements.md, architecture.md, TODO_DOWNLOADS.md, agent-prompts.md
```

### 存储布局（R-19 / N-8：原件不可覆盖）

```text
storage/
├── templates/report_template.docx          模板原件（只读）
└── cases/<case_no>/
    ├── originals/<kind>/<filename>         上传原件，永不覆盖、永不修改
    ├── derived/<kind>/<stem>_p<N>.png      识别用副本（预处理结果）
    ├── derived/<kind>/<stem>.ocr.json      sidecar OCR 结果
    ├── documents/<case_no>-<doc_type>-v<N>.docx / .pdf
    └── documents/<case_no>-<doc_type>-v<N>.render.txt   生成时的正文快照（仅供校核比对，不是证据）
```

`<kind>` 取值即 `MATERIAL_KINDS`，其中 `photo` 是部位照片：**原件只读、直接插入文稿，
不生成识别用副本、不写 `ocr_blocks`**（N-11）。

环境变量：`FORENSIC_DB_PATH`（默认 `data/cases.db`）、`FORENSIC_STORAGE_ROOT`
（默认 `storage`）、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`。

---

## 2. 数据模型

所有表由 `database.py::Database.initialize()` 用 `CREATE TABLE IF NOT EXISTS` 建立。
时间戳统一 `utc_now()` = `datetime.now(timezone.utc).replace(microsecond=0).isoformat()`。

| 表 | 作用 | 关键列 |
| --- | --- | --- |
| `cases` | 案件主表 | `id`, `case_no` UNIQUE, `stage`, `doc_status`, `section_order_json`, `opinion_text`, `template_version`, `term_rule_version`, `version` |
| `materials` | 材料清单（R-12/R-13/R-54） | `case_id`, `kind`, `filename`, `media_type`, `original_path`, `derived_dir`, `sha256`, `page_count`, `quality_flags_json`, `sort_order`, `position_key`, `photo_caption` |
| `material_pages` | 页级信息（R-18） | `material_id`, `page_number`, `image_path`, `original_image_path`, `is_scanned`, `has_text_layer`, `text_layer_chars`, `quality_flags_json`, `preprocessing_json`, `ocr_status`, UNIQUE(`material_id`,`page_number`) |
| `ocr_blocks` | 识别结果与定位（R-15/R-21） | `case_id`, `material_id`, `page_id`, `page_number`, `block_index`, `text`, `confidence`, `bbox_json`, `engine`, `is_handwritten`, `column_hint` |
| `case_fields` | 事实字段（R-14/R-15/R-17/R-43） | `case_id`, `field_key`, `field_group`, `raw_text`, `confirmed_value`, `normalized_text`, `unit`, `status`, `review_state`, `confidence`, `source_kind`, `source_material_id`, `source_page`, `source_bbox_json`, `source_block_id`, `applied_rule_codes_json`, `rule_note`, `conflict_json`, `version`, UNIQUE(`case_id`,`field_key`) |
| `case_injuries` | 损伤逐条（R-16） | `case_id`, `seq`, `body_part`, `side`, `injury_type`, `count`, `length`, `width`, `size_unit`, `morphology`, `other_traits`, `raw_text`, `normalized_text`, `status`, `review_state`, `confidence`, 来源列同上 |
| `terminology_rules` | 简写规则（R-4/R-23/R-37） | `code` UNIQUE, `shorthand`, `pattern`, `match_type`, `template`, `scope_field_key`, `scope_column`, `scope_writer`, `boundary_note`, `require_groups_json`, `missing_group_note`, `requires_confirmation`, `priority`, `active`, `version` |
| `corrections` | 修正记录（R-38～R-41） | `case_id`, `kind`, `target_type`, `target_id`, `before_text`, `after_text`, `reason`, `confirmed_by`, `scope`, `scope_value`, `affected_json`, `invalidated_json`, `applied` |
| `documents` | 文稿版本（R-50/R-51） | `case_id`, `doc_type`, `version_no`, `doc_status`, `template_version`, `term_rule_version`, `template_path`, `docx_path`, `pdf_path`, `render_engine`, `unresolved_json`, `blocked_reason`, `approved_at`, UNIQUE(`case_id`,`doc_type`,`version_no`) |
| `checklist_items` | 校核清单（R-6/R-46） | `case_id`, `document_id`, `category`, `severity`, `code`, `title`, `detail`, `status`, `target_type`, `target_id`, `source_json`, `suggestion`, `resolved_by`, `note`, `resolved_at` |
| `case_events` | 审计时间线（R-48） | `case_id`, `event_type`, `title`, `detail`, `actor`, `created_at` |

`bbox_json` 统一为归一化坐标 `[x0, y0, x1, y1]`，取值 `0..1`，原点在页面左上角。

### 不变量（必须由 `database.py` 与 `checker.py` 共同保证）

| 编号 | 不变量 |
| --- | --- |
| INV-1 | `case_fields.field_key` 取值必须来自 `FIELD_INDEX`；`field_group` / `field_label` / `display_order` 由目录派生。 |
| INV-2 | `status == "unclear"` 或 `review_state == "pending"` 的关键字段（`CRITICAL_FIELD_KEYS`）构成阻断项（R-28/R-45）。 |
| INV-3 | `status == "not_recorded"` 时 `normalized_text` 必须是“未记录”，**任何情况下不得**包含“未见异常”（N-5/R-35）。 |
| INV-4 | `case_injuries` 每条独立一行，不得把多条记录的部位与尺寸合并（R-16/R-36）。 |
| INV-5 | 生成 `pending_issue` 文稿时，若存在 `severity="blocking"` 且 `status="open"` 的校核项 → 抛 `BlockedByChecklist` → HTTP 409（R-28）。 |
| INV-6 | 事实字段被修正后，依赖它的文稿 `doc_status` 变为 `superseded`，相关字段 `review_state` 回到 `pending`（R-40/R-41）。 |
| INV-7 | `corrections.scope != "case_only"` 时必须写入 `scope_value`，且不得自动改写其他案件（R-39/N-10）。 |
| INV-8 | 仅 `doc_status="approved"` 的文稿可被 `deliver` 引用（R-41）。 |
| INV-9 | 任何写操作都必须先 `_claim(case_id, expected_version)`，`rowcount == 0` → `VersionConflict` → 409。 |
| INV-10 | `opinion_text` 只能由用户请求写入，任何代码路径不得从 OCR 文本推导（N-1）。 |
| INV-11 | `kind == "photo"` 的材料**只**贡献图像文件与 `position_key`；不得为其写入 `ocr_blocks`、不得进入 `extractor` 的来源集合、不得对其调用任何视觉/模型接口（N-11/R-55）。 |

---

## 3. 状态机

### 3.1 案件阶段 `cases.stage`

顺序即 `CASE_STAGES`：`intake → image_check → ocr → extract → verify_facts → normalize
→ render → check_document → professional_review → delivered`。
只允许前进一格或回退一格（`STAGE_TRANSITIONS`），其他跳转 → `InvalidTransition` → 409。

### 3.2 文稿状态 `documents.doc_status`

| 起点 | 允许到达 | 条件 |
| --- | --- | --- |
| —（新建） | `internal_draft` | 允许存在未解决疑点；正文必须带 `【待核对】` 标记（R-27/R-46） |
| —（新建） | `pending_issue` | **不允许**：新建一律先落 `internal_draft` |
| `internal_draft` | `pending_issue` | 无 `open` 阻断项、无未解析占位符、无待核对标记 |
| `internal_draft` / `pending_issue` | `approved` | 通过 `POST /api/documents/{id}/approve` |
| `approved` | `superseded` | 新版本生成，或事实修正触发 |

### 3.3 字段状态

`recorded` / `not_recorded` / `not_examined` / `negative` / `unclear`（见 `FIELD_STATUSES`）。
`review_state`：`pending → confirmed`；事实修正会把 `confirmed` 打回 `pending`。

---

## 4. HTTP API 契约

统一约定：

- 前缀 `/api`，JSON 请求与响应，UTF-8。
- 所有写操作（POST/PATCH/DELETE）必须携带 `expected_version: int >= 1`。
  `POST`/`PATCH` 放在请求体；`DELETE` 放在查询参数（`?expected_version=N`）。
- **`expected_version` 的归属（重要，避免歧义）**：
  | 端点范围 | `expected_version` 指的是 |
  | --- | --- |
  | `/api/cases/**`（含 fields / injuries / materials / documents / check / corrections / deliver / stage） | **案件** `cases.version` |
  | `/api/terminology-rules/**` | **规则** `terminology_rules.version` |
  | `/api/documents/{id}/approve` | **该文稿所属案件** 的 `cases.version`（`documents` 表没有独立版本列） |
  | `/api/checklist/{item_id}` | **该清单项所属案件** 的 `cases.version` |
  实现方式：`database._claim(case_id, expected_version)`；规则类端点用
  `_claim_rule(rule_id, expected_version)`。
- 错误体：`{"detail": "<中文可读信息>"}`。
- **可选访问控制（部署用）**：环境变量 `FORENSIC_BASIC_AUTH="用户名:口令"` 非空时启用 HTTP Basic 鉴权——
  除 `GET /api/health`（探活，只返回状态，不含案件数据）与 CORS 预检 `OPTIONS` 外，所有请求必须携带
  正确凭据，否则 `401` + `WWW-Authenticate: Basic realm="forensic-doc-agent", charset="UTF-8"`。
  未设置或格式非法（缺用户名/口令）时不启用鉴权，适用于本机与 SSH 隧道场景。
  **案件材料对外暴露前必须设置该变量**（应用自身没有账号体系；`expected_version` 只是乐观锁）。
- 状态码：`400` 参数/前置条件不满足；`404` 资源不存在；`409` 版本冲突、非法状态流转、
  被阻断（`BlockedByChecklist`）、重复案件编号、OCR 引擎不可用；`422` Pydantic 校验失败；
  `502` 模型服务错误；`504` 模型超时。

| 方法 | 路径 | 请求模型 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/health` | — | `{"status":"ok","service":"forensic-doc-agent"}` |
| GET | `/api/config` | — | 字段目录、枚举字典、阶段顺序、引擎状态、LibreOffice 状态、`photo_positions`、`demo_data`、`todo_downloads`、**`reference_standards` + `standard_config_confirmed`（R-49：技术标准只作参考提示，采用与否由业务方确认；未确认前不得据标准生成任何内容）**。形状（实测，已冻结）：枚举一律为 `[{key,label}]` 数组；引擎状态在 `engines = {docx:{preferred,docxtpl_available,todo}, libreoffice, libreoffice_available, ocr:[{name,available,detail}]}`；`ocr_engines` 只是名字数组（**不要**用它判断可用性） |
| GET | `/api/stats` | — | 案件数、各阶段数、待核对字段数、阻断项数、文稿数、按材料类型分布 |
| POST | `/api/ai/verify` | 头 `X-DeepSeek-API-Key` | 测试模型连通性（`llm.py`） |
| GET | `/api/cases` | 查询参数 | 服务端分页/搜索/筛选 |
| POST | `/api/cases` | `CaseCreate` | 建案，自动生成 `case_no`，返回完整案件 |
| POST | `/api/quick-run` | `QuickRunRequest` | **普通用户主流程（R-59～R-63）**：一次调用跑完「建案 → 登记材料 → 预处理 → 识别 → 提取 → 整理 → 生成 → 校核」。属**新建案件**（等价于 `POST /api/cases`），因此**不携带 `expected_version`**。材料类型/照片位置留空时由后端按**文件名与扩展名**推断（绝不读取图片内容，N-11）。响应用途固定：`{case_id, case_no, stage, steps[], assignments[], summary{}, documents[], notes[], case_url}`（`documents[].download_urls.{docx,pdf}` 直接可用）。单步失败写进 `steps[].ok=false` 并继续，PDF 失败记入 `notes` |
| GET | `/api/cases/{case_id}` | — | 详情：案件 + `materials` + `fields` + `injuries` + `documents` + `checklist` + `events` + `corrections` |
| PATCH | `/api/cases/{case_id}` | `CaseUpdate` | 修改案件基础信息；**`opinion_text`（鉴定意见）只能由用户请求原样写入**，任何代码路径不得从 OCR 推导（N-1/INV-10） |
| PATCH | `/api/cases/{case_id}/stage` | `CaseStageUpdate` | 推进/回退阶段 |
| DELETE | `/api/cases/{case_id}` | 查询参数 `expected_version` | 删除案件及其材料目录 |
| POST | `/api/cases/{case_id}/materials` | `MaterialCreate` | 登记材料；`expected_version` 在请求体（`MaterialCreate` 已声明，≥1）；`kind="photo"` 时可带 `position_key`/`photo_caption` |
| GET | `/api/cases/{case_id}/materials` | — | 材料清单（含页、`position_key`、`photo_caption`） |
| GET | `/api/cases/{case_id}/materials/{material_id}/file` | 查询参数 `page`（默认 1）、`variant`（`original`\|`derived`，默认 `original`） | `FileResponse`：原件只读预览（字段核对原图、照片缩略图） |
| PATCH | `/api/cases/{case_id}/materials/{material_id}/placement` | `PhotoPlacementUpdate` | 为部位照片指定/调整目标位置与顺序（只写元数据，不读照片内容） |
| GET | `/api/cases/{case_id}/photo-slots` | — | 照片栏位视图，形状（实测，已冻结）：`{"photo_policy", "placeholder_text", "slots":[{"position_key","label","section","section_label","order","anchor","wide","missing","photos":[...]}], "missing_count", "assigned_count"}` |
| PATCH | `/api/cases/{case_id}/materials/{material_id}/pages/{page_number}` | `MaterialPageUpdate` | 修订页级质量标记 |
| POST | `/api/cases/{case_id}/materials/{material_id}/preprocess` | `PreprocessRequest` | 生成识别用副本（原件不动） |
| POST | `/api/cases/{case_id}/ocr` | `OcrRunRequest` | 运行识别，写入 `ocr_blocks` |
| GET | `/api/cases/{case_id}/ocr-blocks` | 查询参数 `material_id`,`page`,`page_size` | 识别结果分页 |
| POST | `/api/cases/{case_id}/extract` | `ExtractRequest` | 字段与损伤提取 |
| GET | `/api/cases/{case_id}/fields` | — | 字段列表 |
| PATCH | `/api/cases/{case_id}/fields/{field_key}` | `FieldUpdate` | 核对/确认字段 |
| GET | `/api/cases/{case_id}/injuries` | — | 损伤列表（按 `seq`） |
| POST | `/api/cases/{case_id}/injuries` | `InjuryCreate` | 新增损伤 |
| PATCH | `/api/cases/{case_id}/injuries/{injury_id}` | `InjuryUpdate` | 修改损伤 |
| DELETE | `/api/cases/{case_id}/injuries/{injury_id}` | 查询参数 `expected_version` | 删除损伤 |
| POST | `/api/cases/{case_id}/injuries/reorder` | `IdListRequest` + 查询参数 `expected_version` | 调整顺序（R-7 工作顺序） |
| POST | `/api/cases/{case_id}/normalize` | `NormalizeRequest` | 术语整理 |
| GET | `/api/terminology-rules` | 查询参数 `active_only`,`search` | 规则列表 |
| POST | `/api/terminology-rules` | `TerminologyRuleCreate` | 新增规则 |
| PATCH | `/api/terminology-rules/{rule_id}` | `TerminologyRuleUpdate` | 修改规则 |
| DELETE | `/api/terminology-rules/{rule_id}` | 查询参数 `expected_version` | 删除规则 |
| POST | `/api/cases/{case_id}/documents` | `DocumentCreate` | 生成文稿 |
| GET | `/api/cases/{case_id}/documents` | — | 文稿版本列表 |
| GET | `/api/documents/{document_id}/download` | 查询参数 `fmt=docx\|pdf` | `FileResponse` |
| POST | `/api/documents/{document_id}/approve` | `CheckRequest` | 标记已审核 |
| POST | `/api/cases/{case_id}/check` | `CheckRequest` | 运行校核，写入清单 |
| GET | `/api/cases/{case_id}/checklist` | 查询参数 `status`,`category` | 校核清单 |
| PATCH | `/api/checklist/{item_id}` | `ChecklistItemUpdate` | 处理/豁免校核项 |
| POST | `/api/cases/{case_id}/corrections` | `CorrectionCreate` | 记录并应用用户修正 |
| GET | `/api/cases/{case_id}/corrections` | — | 修正记录 |
| POST | `/api/cases/{case_id}/deliver` | `DeliverRequest` | 保存交付 |

### 4.1 `GET /api/cases` 查询参数

`search`（案件编号/委托单位/姓名/委托事项 LIKE）、`stage`、`doc_status`、`material_kind`
（存在该类型材料）、`field_status`（存在该状态的字段）、`has_blocking`（`true`/`false`）、
`page`（≥1）、`page_size`（1–100，默认 20）。

响应：`{"items":[...],"total":N,"page":1,"page_size":20}`。
`items` 元素为案件摘要：`id, case_no, subject_name, commission_unit, stage, doc_status,
version, material_count, field_count, injury_count, unresolved_count, blocking_count,
document_count, created_at, updated_at`。

### 4.2 错误映射（`routes.py` 统一）

| 异常 | 状态码 | `detail` |
| --- | --- | --- |
| `VersionConflict` | 409 | 案件已被其他操作更新，请刷新后重试 |
| `DuplicateCase` | 409 | 该案件编号已存在，不能重复建立 |
| `InvalidTransition` | 409 | `str(error)` |
| `BlockedByChecklist` | 409 | 存在未解决的阻断项，不能生成待签发稿：… |
| `ValueError` | 400 | `str(error)`（参数/前置条件不满足，例如“只有部位照片才能指定目标位置”） |
| `OcrEngineUnavailable` | 409 | `str(error)` |
| `DocgenUnavailable` | 409 | `str(error)` |
| `LlmAPIError` | `error.status_code` | `error.message`（不透传上游正文） |
| `KeyError` / `LookupError` | 404 | 资源不存在 |
| 其他 | 500 | 服务内部错误 |

---

## 5. 后端模块签名（冻结）

### 5.1 `storage.py`

```python
DEFAULT_STORAGE_ROOT: str

class CaseStorage:
    def __init__(self, root: str | Path) -> None: ...
    def ensure(self) -> None: ...
    def templates_dir(self) -> Path: ...
    def case_dir(self, case_no: str) -> Path: ...
    def originals_dir(self, case_no: str, kind: str) -> Path: ...
    def derived_dir(self, case_no: str, kind: str) -> Path: ...
    def documents_dir(self, case_no: str) -> Path: ...
    def write_original(self, case_no: str, kind: str, filename: str, data: bytes | None) -> str: ...
    def write_derived(self, case_no: str, kind: str, filename: str, data: bytes) -> str: ...
    def relative(self, path: str | Path) -> str: ...
    def absolute(self, relative_path: str) -> Path: ...
```

### 5.2 `database.py`

```python
class VersionConflict(Exception): ...
class DuplicateCase(Exception): ...
class InvalidTransition(Exception): ...
class BlockedByChecklist(Exception): ...
class NotFound(Exception): ...

def utc_now() -> str: ...
def normalize_case_no(value: str) -> str: ...

class Database:
    def __init__(self, path: str) -> None: ...
    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]: ...
    def initialize(self) -> None: ...
    def seed(self, case_count: int = SEED_CASE_COUNT) -> list[int]: ...

    # 案件
    def create_case(self, payload: dict, actor: str = "系统") -> dict: ...
    def get_case(self, case_id: int) -> dict | None: ...
    def list_cases(self, search: str = "", stage: str = "", doc_status: str = "",
                   material_kind: str = "", field_status: str = "", has_blocking: str = "",
                   page: int = 1, page_size: int = 20) -> dict: ...
    def update_case(self, case_id: int, changes: dict, expected_version: int, actor: str = "复核人") -> dict: ...
    def update_stage(self, case_id: int, stage: str, expected_version: int, note: str = "", actor: str = "复核人") -> dict: ...
    def delete_case(self, case_id: int, expected_version: int) -> None: ...
    def stats(self) -> dict: ...

    # 材料
    def add_material(self, case_id: int, payload: dict, expected_version: int, storage: CaseStorage, actor: str = "复核人") -> dict: ...
    def list_materials(self, case_id: int) -> list[dict]: ...
    def get_material(self, material_id: int) -> dict | None: ...
    def get_page(self, material_id: int, page_number: int) -> dict | None: ...
    def register_pages(self, material_id: int, pages: Sequence[dict], expected_version: int) -> list[dict]: ...
    def update_material_page(self, material_id: int, page_number: int, changes: dict, expected_version: int, actor: str = "复核人") -> dict: ...
    def save_preprocessing(self, material_id: int, page_number: int, operations: Sequence[str], derived_path: str) -> dict: ...
    def set_ocr_status(self, material_id: int, page_number: int, status: str) -> None: ...
    # 部位照片（N-11：只写元数据，不读照片内容）
    def set_photo_placement(self, case_id: int, material_id: int, payload: dict, expected_version: int,
                            actor: str = "复核人") -> dict: ...
    def list_photo_slots(self, case_id: int) -> list[dict]: ...

    # OCR
    def replace_ocr_blocks(self, case_id: int, material_id: int, page: dict, engine: str, blocks: Sequence[dict]) -> int: ...
    def list_ocr_blocks(self, case_id: int, material_id: int | None = None, page: int = 1, page_size: int = 200) -> dict: ...

    # 字段
    def upsert_fields(self, case_id: int, fields: Sequence[dict], replace_existing: bool, expected_version: int, actor: str = "系统") -> list[dict]: ...
    def list_fields(self, case_id: int) -> list[dict]: ...
    def get_field(self, case_id: int, field_key: str) -> dict | None: ...
    def update_field(self, case_id: int, field_key: str, changes: dict, expected_version: int, actor: str = "复核人") -> dict: ...
    def invalidate_reviews(self, case_id: int, target_type: str, target_id: int, reason: str) -> list[dict]: ...

    # 损伤
    def upsert_injuries(self, case_id: int, injuries: Sequence[dict], replace_existing: bool, expected_version: int, actor: str = "系统") -> list[dict]: ...
    def list_injuries(self, case_id: int) -> list[dict]: ...
    def create_injury(self, case_id: int, payload: dict, expected_version: int, actor: str = "复核人") -> dict: ...
    def update_injury(self, case_id: int, injury_id: int, changes: dict, expected_version: int, actor: str = "复核人") -> dict: ...
    def delete_injury(self, case_id: int, injury_id: int, expected_version: int) -> None: ...
    def reorder_injuries(self, case_id: int, ordered_ids: Sequence[int], expected_version: int, actor: str = "复核人") -> list[dict]: ...

    # 规则
    def list_terminology_rules(self, active_only: bool = False, search: str = "") -> list[dict]: ...
    def get_terminology_rule(self, rule_id: int) -> dict | None: ...
    def create_terminology_rule(self, payload: dict, actor: str = "复核人") -> dict: ...
    def update_terminology_rule(self, rule_id: int, changes: dict, expected_version: int, actor: str = "复核人") -> dict: ...
    def delete_terminology_rule(self, rule_id: int, expected_version: int) -> None: ...
    def import_seed_rules(self) -> int: ...

    # 文稿
    def next_document_version(self, case_id: int, doc_type: str) -> int: ...
    def create_document(self, case_id: int, payload: dict, expected_version: int, actor: str = "系统") -> dict: ...
    def list_documents(self, case_id: int) -> list[dict]: ...
    def get_document(self, document_id: int) -> dict | None: ...
    def approve_document(self, document_id: int, expected_version: int, note: str = "", actor: str = "鉴定人") -> dict: ...
    def supersede_documents(self, case_id: int, keep_document_id: int | None, reason: str, actor: str = "系统") -> list[int]: ...

    # 校核
    def replace_checklist(self, case_id: int, document_id: int | None, items: Sequence[dict], actor: str = "系统") -> list[dict]: ...
        # 该签名不含 expected_version（契约遗留）；由 workflow.run_check 在入口先
        # database.claim(case_id, expected_version) 补齐乐观锁（INV-9 由调用方保证）。
    def list_checklist(self, case_id: int, status: str = "", category: str = "") -> list[dict]: ...
    def get_checklist_item(self, item_id: int) -> dict | None: ...
    def update_checklist_item(self, item_id: int, changes: dict, expected_version: int, actor: str = "复核人") -> dict: ...
    def open_blocking_items(self, case_id: int) -> list[dict]: ...

    # 修正
    def create_correction(self, case_id: int, payload: dict, expected_version: int, actor: str = "复核人") -> dict: ...
    def list_corrections(self, case_id: int) -> list[dict]: ...

    # 事件
    def add_event(self, case_id: int, event_type: str, title: str, detail: str = "", actor: str = "系统") -> None: ...
    def list_events(self, case_id: int, limit: int = 200) -> list[dict]: ...
```

> **`get_case()` 返回结构**（前端与测试都依赖，必须严格一致）：
> `{...cases 列..., "intake": {...}, "section_order": [...],
>  "materials": [{...material..., "pages": [...]}],
>  "fields": [...], "injuries": [...], "documents": [...],
>  "checklist": [...], "events": [...], "corrections": [...],
>  "summary": {"material_count":..,"field_count":..,"injury_count":..,
>              "unresolved_count":..,"blocking_count":..,"document_count":..}}`
> 所有 `*_json` 列必须已解析为 Python 对象，不得把 JSON 字符串透传给前端。
>
> `list_photo_slots()` 返回结构（前端与 `docgen` 共用）：
> `[{"position_key","label","section","section_label","order","anchor","wide",
>    "photos":[{"material_id","filename","caption","sort_order","original_path"}]}]`
> 顺序按 `PHOTO_POSITION_DEFINITIONS.order`；未指派的照片**不出现**在任何栏位中。

### 5.3 `imaging.py`

```python
@dataclass
class PageProbe:
    page_number: int
    width: int
    height: int
    is_scanned: bool
    has_text_layer: bool
    text_layer_chars: int
    original_path: str
    image_path: str

def sha256_file(path: str | Path) -> str: ...
def probe(path: str | Path) -> list[PageProbe]: ...
def extract_pdf_text(path: str | Path) -> str: ...
def render_pdf_pages(pdf_path: str | Path, out_dir: str | Path, dpi: int = 200, prefix: str = "page") -> list[str]: ...
def preprocess_image(src: str | Path, dst: str | Path, operations: Sequence[str]) -> dict: ...
```

- PDF 优先 `pypdfium2`，其次 `PyMuPDF`（`fitz`）；两者都不可用时 `render_pdf_pages` 返回 `[]`
  且 `probe()` 把 `is_scanned=True`、`has_text_layer=False`。
- `preprocess_image` 返回 `{"operations":[...],"applied":[...],"skipped":[...],"size":[w,h],"output":path}`，
  任何未知操作进 `skipped`，绝不抛异常中断流程。
- `extract_pdf_text` 只读原生文字层；**不得**把文字层当作准确原文（R-18）。

### 5.4 `ocr/base.py`

```python
@dataclass
class OcrBlock:
    page_number: int
    block_index: int
    text: str
    confidence: float
    bbox: list[float]          # 归一化 [x0,y0,x1,y1]
    engine: str
    is_handwritten: bool = False
    column_hint: str = ""

class OcrEngineUnavailable(RuntimeError): ...

class OcrEngine(Protocol):
    name: str
    def available(self) -> bool: ...
    def describe(self) -> dict: ...
    def recognize(self, image_path: str, page_number: int, *,
                  kind: str = "", column_hint: str = "") -> list[OcrBlock]: ...
```

`ocr/sidecar.py`：`SidecarOcrEngine(sidecar_dir: str | None = None)`，在
`<image_stem>.ocr.json` 或 `sidecar_dir/<image_stem>.ocr.json` 中查找：

```json
{"engine":"sidecar","blocks":[{"text":"尸长172cm","confidence":0.93,
 "bbox":[0.10,0.22,0.46,0.27],"is_handwritten":true,"column_hint":"尸长"}]}
```

**文件命名约定（W1 与 W2 必须一致）**：`material_pages.image_path` 指向识别用副本
（`derived/<kind>/<stem>_p<N>.png`），sidecar JSON 必须写成**同目录同主名**：
`derived/<kind>/<stem>_p<N>.ocr.json`。这样 `SidecarOcrEngine()` 以默认参数即可找到，
`run_ocr` 不需要额外传 `sidecar_dir`。`seed_demo_case` 也必须按此约定落盘。

`ocr/paddle.py`：`PaddleOcrEngine`，`available()` = 能否 import `paddleocr`；
不可用时 `recognize()` 抛 `OcrEngineUnavailable("未安装 PaddleOCR（TODO-DL-2）…")`。

**引擎选择语义（裁决，两份文档口径以此为准）**：
* `OcrRunRequest.engine` 是**用户显式选择**：显式选 `paddle` 而不可用 → `OcrEngineUnavailable` → **HTTP 409**，
  `detail` 明确提示“改用 sidecar 引擎”，**不得静默回退**（TODO-DL-2 的要求）。
* `engine` 留空 / `"auto"` → `resolve_engine()` 回退 `sidecar`，并在 `payload.engine` 与 `StageResult.detail`
  中如实报告实际使用的引擎与回退原因（`payload.fallback_from`）。
* `"none"` 是显式的“不识别”选择，返回空结果而不是错误。

`ocr/__init__.py`：

```python
def register_engine(name: str, factory: Callable[[], OcrEngine]) -> None: ...
def get_engine(name: str) -> OcrEngine: ...
def resolve_engine(preferred: str = "sidecar") -> OcrEngine:      # 不可用则回退 sidecar
def engine_status() -> list[dict]: ...                            # [{"name","available","detail"}]
```

### 5.5 `extractor.py`

```python
@dataclass
class ExtractedField: ...     # 列同 case_fields（不含 id/case_id）
@dataclass
class ExtractedInjury: ...    # 列同 case_injuries（不含 id/case_id）

def infer_status(text: str) -> str: ...
def parse_blocks(rows: Sequence[Mapping[str, Any]]) -> list[dict]: ...
def extract_fields(rows: Sequence[Mapping[str, Any]], case_context: Mapping[str, Any] | None = None,
                   definitions: Sequence[FieldDefinition] = FIELD_DEFINITIONS) -> list[ExtractedField]: ...
def extract_injuries(rows: Sequence[Mapping[str, Any]]) -> list[ExtractedInjury]: ...
def detect_conflicts(fields: Sequence[ExtractedField]) -> list[dict]: ...
```

`infer_status` 规则（优先级从高到低）：

1. 空串或仅 `/`、`—`、`-`、`无记录` → `not_recorded`。
2. 命中 `STATUS_KEYWORDS["not_examined"]` → `not_examined`。
3. 命中 `STATUS_KEYWORDS["negative"]` → `negative`。
4. 其余非空 → `recorded`。
5. 由调用方（`workflow.run_extraction`）在 `confidence < OCR_LOW_CONFIDENCE` 或同字段多来源
   取值不一致时改写为 `unclear`。

`extract_fields` 必须做到：**只填有来源的字段**；没有命中来源的字段不写入记录
（不产生“空字段行”），由 `checker` 报告缺失。`confidence` 取来源块的最小置信度。
同一 `field_key` 命中多个不同来源值时，按 `seq` 全部保留到 `conflict` 列表，
`status` 置 `unclear`。

`extract_injuries` 必须做到：**一行 OCR 文本 → 至多一条损伤**，绝不含并多行（R-16/R-36）；
`seq` 按 OCR 出现顺序（`page_number`，`block_index`）递增。

### 5.6 `terminology.py`

```python
@dataclass
class NormalizationResult:
    target_type: str            # "field" | "injury"
    target_id: int
    field_key: str
    raw_text: str
    normalized_text: str
    status: str
    applied_rule_codes: list[str]
    rule_note: str
    requires_confirmation: bool
    issues: list[dict]

@dataclass
class CompiledRule:
    code: str
    pattern: str
    match_type: str
    template: str
    scope_field_key: str
    scope_column: str
    scope_writer: str
    boundary_note: str
    require_groups: tuple[str, ...]
    missing_group_note: str
    requires_confirmation: bool
    priority: int
    regex: re.Pattern[str] | None

def compile_rules(rules: Sequence[Mapping[str, Any]]) -> list[CompiledRule]: ...
def normalize_text(raw_text: str, *, field_key: str = "", column: str = "", writer: str = "",
                   status: str = "recorded", rules: Sequence[CompiledRule] | None = None) -> NormalizationResult: ...
def normalize_field(field: Mapping[str, Any], rules: Sequence[CompiledRule]) -> NormalizationResult: ...
def normalize_injury(injury: Mapping[str, Any], rules: Sequence[CompiledRule]) -> NormalizationResult: ...
def normalize_case(fields: Sequence[Mapping[str, Any]], injuries: Sequence[Mapping[str, Any]],
                   rules: Sequence[CompiledRule]) -> list[NormalizationResult]: ...
```

硬性规则：

- `status == "not_recorded"` → `normalized_text = "未记录"`，`requires_confirmation=False`，
  并且断言结果中**不含**“未见异常”（N-5/R-35）。
- `status == "not_examined"` → `normalized_text` 保留原文并前置“未进行该项检查：”。
- `status == "negative"` → 保留原文中的否定词（N-4）。
- `status == "unclear"` → `normalized_text = raw_text`，`requires_confirmation=True`，
  写入 `issues`（R-34）。
- 命中 `require_groups` 中为空的分组 → 保留数值原文，`requires_confirmation=True`，
  `rule_note = missing_group_note`（R-33）。
- 未命中任何规则且 `status == "recorded"` → `normalized_text = raw_text`，
  `requires_confirmation=True`，`issues` 记“无认可规则”（R-23）。
- 大小写/空白归一，但**不改变数值精度**：`3` 不得变成 `3.0`（N-3）。
- 规则按 `(priority, code)` 升序，取**第一条命中**者；一条都不命中时按上一条处理。

`normalize_injury` 无规则命中时按结构化字段拼装：
`f"{side}{body_part}见{injury_type}" + (f"，共{count}处" if count else "") +
(f"，范围为{length} {size_unit} × {width} {size_unit}。" if length and width else "。")`，
`side`/`body_part`/`injury_type` 缺失时用原 `raw_text` 兜底。

### 5.7 `checker.py`

```python
@dataclass
class CheckItem:
    category: str       # CHECK_CATEGORIES
    severity: str       # CHECK_SEVERITIES
    code: str
    title: str
    detail: str
    target_type: str = ""
    target_id: int = 0
    source: dict = field(default_factory=dict)
    suggestion: str = ""

def check_case(case_data: Mapping[str, Any], *, include_layout: bool = True,
               template_text: str = "", stored_text: str = "",
               rendered_snapshot: str = "") -> list[CheckItem]: ...
def check_recognition(case_data) -> list[CheckItem]: ...
def check_content(case_data) -> list[CheckItem]: ...
def check_source(case_data) -> list[CheckItem]: ...
def check_format(case_data, *, template_text: str = "", stored_text: str = "",
                 rendered_snapshot: str = "") -> list[CheckItem]: ...
def check_corrections(case_data) -> list[CheckItem]: ...
def has_blocking(items: Sequence[CheckItem]) -> bool: ...
```

必须产出的校核项（`code` 为契约的一部分，测试与前端按 code 断言）：

| code | category | severity | 触发条件 |
| --- | --- | --- | --- |
| `MATERIAL_MISSING` | source | blocking | `REQUIRED_MATERIAL_KINDS` 中任一类型无材料 |
| `FIELD_SOURCE_MISSING` | source | blocking | `status == "recorded"` 但 `source_material_id` 为空**且** `source_kind != "user"`（R-43：用户在建案/核对时直接输入的补充信息不需要扫描来源；`source_kind` ∈ `""` / `"material"` / `"user"`） |
| `FIELD_UNRESOLVED_CRITICAL` | content | blocking | `CRITICAL_FIELD_KEYS` 中 `status == "unclear"` 或 `review_state == "pending"` |
| `FIELD_REQUIRED_EMPTY` | content | blocking | `REQUIRED_FIELD_KEYS` 中字段不存在或无 `confirmed_value` |
| `FIELD_CONFLICT` | content | blocking | `conflict_json` 非空（R-44） |
| `OCR_LOW_CONFIDENCE` | recognition | warning | 存在 `confidence < OCR_LOW_CONFIDENCE` 的块 |
| `OCR_HANDWRITTEN_UNVERIFIED` | recognition | warning | 手写块尚未被任何字段引用（R-21/R-45） |
| `INJURY_UNRESOLVED` | content | warning | 损伤 `review_state == "pending"` |
| `INJURY_MISSING_SIZE` | content | warning | 有 `length` 无 `width`（或反之） |
| `NEGATION_UNPRESERVED` | content | blocking | `negative`/`not_examined` 字段的 `normalized_text` 丢失全部 `NEGATION_WORDS` |
| `NOT_RECORDED_MISLABELED` | content | blocking | `not_recorded` 字段的 `normalized_text` 含“未见异常”（N-5） |
| `OPINION_FROM_SOURCE` | content | blocking | `opinion_text` 非空且与任一 OCR 块文本高度重合（N-1） |
| `DOCUMENT_UNRESOLVED_PLACEHOLDER` | format | blocking | 文稿文本含 `PLACEHOLDER_PATTERNS` |
| `DOCUMENT_DRAFT_MARKER` | format | blocking | 生成 `pending_issue` 时正文仍含 `DRAFT_MARKER`（R-46） |
| `DOCUMENT_PDF_MISSING` | format | warning | `pdf_path` 为空（LibreOffice 不可用或导出失败） |
| `DOCUMENT_TEXT_DIVERGED` | format | warning | **生成时正文快照**（`<docx>.render.txt`）与**当前文稿文本**不一致（文稿被系统外改动）；无快照时不报（宁缺勿猜，绝不能拿“模板≠产物”当分歧） |
| `CORRECTION_UNAPPLIED` | correction | warning | `corrections.applied == 0` |
| `CORRECTION_SCOPE_UNCLEAR` | correction | warning | `scope != "case_only"` 且 `scope_value` 为空 |
| `REVIEW_INVALIDATED` | correction | info | 存在被打回 `pending` 的已确认字段（R-40） |
| `PHOTO_CONTENT_READ` | recognition | blocking | 存在 `kind=="photo"` 材料却有 `ocr_blocks` 记录（违反 N-11/R-55） |
| `PHOTO_POSITION_MISSING` | content | warning | 存在未指定 `position_key` 的部位照片（R-57） |
| `PHOTO_FILE_MISSING` | format | warning | 已指派位置的照片原件文件不存在，渲染时被跳过（N-12/R-56） |
| `PHOTO_SLOT_EMPTY` | format | info | 模板含该照片栏位但本次没有照片（正文写入 `PHOTO_PLACEHOLDER_TEXT`，N-12） |

`check_case` 去重规则：相同 `(code, target_type, target_id)` 只保留一条。
`check_case` 返回顺序固定为 `recognition → content → source → format → correction`。

### 5.8 `docgen.py`

```python
class DocgenUnavailable(RuntimeError): ...

@dataclass
class RenderResult:
    output_path: str
    engine: str
    unresolved: list[str]
    paragraph_count: int
    table_count: int

def docx_engine_status() -> dict: ...     # {"preferred": "docxtpl"|"builtin", "docxtpl_available": bool, "todo": "TODO-DL-1"}
def build_demo_template(path: str | Path, template_version: str = DEFAULT_TEMPLATE_VERSION) -> str: ...
def build_context(case_data: Mapping[str, Any]) -> dict: ...
def resolve_template(case_data: Mapping[str, Any], templates_dir: str | Path) -> str: ...
def render_document(template_path: str | Path, context: Mapping[str, Any], output_path: str | Path,
                    engine: str = "auto") -> RenderResult: ...
def detect_libreoffice() -> str | None: ...
def export_pdf(docx_path: str | Path, output_dir: str | Path | None = None, timeout: int = 120) -> str | None: ...
def extract_document_text(path: str | Path) -> str: ...
def document_filename(case_no: str, doc_type: str, version_no: int, fmt: str) -> str: ...
```

- `engine="auto"`：`docxtpl` 可导入则用 `docxtpl`，否则用 `builtin`。
- `builtin` 渲染器：用 `python-docx` 打开模板副本，对段落与表格单元格逐段取合并文本，
  用 `Jinja2`（`jinja2.Template`）渲染后写回首个 run 并清空其余 run，保留段落格式。
  必须支持 `{{ var }}`、`{% for x in xs %}...{% endfor %}`、`{% if %}...{% else %}...{% endif %}`；
  `{% for %}` 跨段落时按段落块复制（`copy.deepcopy(paragraph._p)`）。
- `unresolved`：渲染后仍未被替换的 `{{ ... }}` 锚点列表。非空则 `checker` 产出
  `DOCUMENT_UNRESOLVED_PLACEHOLDER`。
- `build_context` 输出（模板锚点必须与此一致）：
  `case_no, commission_unit, commission_matter, commission_date, case_summary,
  subject_name, subject_gender, subject_age, subject_identity,
  exam_time, exam_place, examiners, exam_method,
  body_length, body_weight, clothing, livor_mortis, rigor_mortis, body_temperature,
  cornea, general_other, template_version, term_rule_version, doc_version,
  section_order (list), fields (dict[key] -> {label, value, status_label, source}), 
  general_findings (list of {label, value, status_label}), injuries (list of
  {seq, text, status_label}), materials (list), checklist_open (list),
  opinion (str), draft_marker (str),
  photo_slots (dict[position_key] -> {"label","section","wide","anchor",
  "photos":[{"path","caption","label"}]}), photos (list，按栏位顺序展开，供 `{% for %}` 使用),
  photo_placeholder (str), photo_max_width_cm (float), photo_wide_max_width_cm (float)`。
- **照片锚点渲染（R-56/N-11）**：段落文本恰为 `{{ photo_<position_key> }}`（可带前后空白）时，
  该段落被替换为图片段落——按 `photos` 中该栏位的照片顺序插入图片（`run.add_picture`），
  宽度 = `min(栏位最大宽度, 原始宽度)`，保持纵横比，原图不裁切；每张图片之后可选一行说明
  （用户填写的 `photo_caption` 优先，其次栏位 label）。栏位无照片时写入 `PHOTO_PLACEHOLDER_TEXT`。
  **渲染器不检查、不分析图片像素内容**，只按文件路径插入。
- **照片通路（冻结口径）**：`workflow` 负责把照片接进上下文——
  `case_data["photo_slots"] = database.list_photo_slots(case_id)`（**list**，`list_photo_slots()` 的原始输出），
  并为其中每个 photo 追加 `path`（**绝对路径**，由 `case_data["storage_root"]` 解析）；`database.list_photo_slots()`
  本身只返回相对 `original_path`，**docgen 不自行拼接存储根**。docgen 同时兼容 dict 形态（key → 栏位）。
  `run_check` 必须把 `storage_root`（字符串）放进 `case_data`，并把模板正文文本作为 `template_text=` 传给
  `checker.check_case()`，否则 `PHOTO_FILE_MISSING` / `PHOTO_SLOT_EMPTY` 无法判定（宁缺勿猜，不误报）。
- **N-1 硬约束**：`build_context` 只从 `cases.opinion_text` 取 `opinion`，绝不从 OCR 文本生成。
- `document_filename` → `f"{case_no}-{doc_type}-v{version_no}.{fmt}"`（R-51）。
- `detect_libreoffice()` 依次检查环境变量 `LIBREOFFICE_BIN`、`soffice`/`libreoffice`（PATH）、
  `C:\Program Files\LibreOffice\program\soffice.exe`、`C:\Program Files (x86)\...`、`/usr/bin/soffice`、
  `/Applications/LibreOffice.app/Contents/MacOS/soffice`。
- `export_pdf` 失败（未安装/超时/退出码非 0）→ 返回 `None`，由调用方记 `DOCUMENT_PDF_MISSING`，
  **不抛异常**。

### 5.9 `llm.py`（可选，默认关闭）

```python
class LlmAPIError(Exception):
    status_code: int
    message: str

def verify_key(api_key: str, base_url: str = "", model: str = "",
               client: httpx.Client | None = None) -> dict: ...
async def extract_with_llm(blocks: Sequence[Mapping[str, Any]], api_key: str, *,
                           base_url: str = "", model: str = "",
                           client: httpx.AsyncClient | None = None) -> dict: ...
```

- 复用参考项目约定：`DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` 环境变量，密钥只从
  `X-DeepSeek-API-Key` 头读取，**不写数据库、不写日志**。
- 模型只允许**挑选并标注来源块**，返回结构 `{"fields":[{"field_key","source_block_id","value"}]}`；
  未在 `FIELD_INDEX` 中的 key 一律丢弃；上游正文不外泄。
- 错误映射：401 密钥无效 / 429 限流 / 502 上游不可用 / 504 超时。
- 测试必须用 `httpx.MockTransport`，禁止访问真实服务。

### 5.10 `workflow.py`

```python
@dataclass
class StageResult:
    stage: str
    ok: bool
    detail: str
    payload: dict

def run_ocr(database, storage, case_id: int, *, engine: str = "sidecar",
            material_id: int | None = None, force: bool = False,
            expected_version: int = 0, actor: str = "系统") -> StageResult: ...
    # 只处理 OCR_MATERIAL_KINDS 的材料；kind=="photo" 一律跳过并计入
    # payload["skipped_photo_ids"]（N-11/R-55）。
def run_preprocess(database, storage, case_id: int, material_id: int, operations: Sequence[str],
                   page_number: int = 0, expected_version: int = 0, actor: str = "系统") -> StageResult: ...
def run_extraction(database, storage, case_id: int, *, material_ids: Sequence[int] = (),
                   replace_existing: bool = True, use_llm: bool = False,
                   api_key: str = "", expected_version: int = 0, actor: str = "系统") -> StageResult: ...
def run_normalization(database, storage, case_id: int, *, target: str = "all",
                      rule_version: str = TERM_RULE_DEFAULT_VERSION, confirm_safe: bool = False,
                      expected_version: int = 0, actor: str = "系统") -> StageResult: ...
def run_render(database, storage, case_id: int, *, doc_type: str = "report",
               doc_status: str = "internal_draft", formats: Sequence[str] = ("docx", "pdf"),
               template_version: str = "", expected_version: int = 0, actor: str = "系统") -> StageResult: ...
    # 渲染后必须写出生成时正文快照 `<docx>.render.txt`（供 run_check 判定文稿是否被系统外改动）；
    # 照片槽位由 photo_context() 注入，其中 path 必须是绝对路径。
def run_check(database, storage, case_id: int, *, document_id: int | None = None,
              include_layout: bool = True, expected_version: int = 0, actor: str = "系统") -> StageResult: ...
    # 入口先 database.claim(case_id, expected_version)（replace_checklist 冻结签名不含版本）；
    # 传给 checker 时必须包含 storage_root、template_text、stored_text、rendered_snapshot。
def run_delivery(database, storage, case_id: int, *, document_id: int | None = None,
                 formats: Sequence[str] = ("docx", "pdf"), expected_version: int = 0,
                 actor: str = "复核人") -> StageResult: ...
```

### 5.11 `seed.py`

```python
def seed_demo_case(database, storage, *, index: int = 1) -> int: ...
def demo_sidecar_blocks(case_no: str) -> dict: ...
```

`seed_demo_case` 写入一个**完整的演示案件**：委托书材料 + 记录本材料 + sidecar OCR JSON
（含印刷与手写块、含一处低置信度、含一条否定词、含两处损伤、含一处材料冲突）+ 演示模板
+ **两张以上部位照片**（程序化生成的纯色占位 PNG，分别指派到不同 `position_key`，其中一张
不指派位置以触发 `PHOTO_POSITION_MISSING`）+ `intake` 记录，并落一条 `intake` 事件。演示数据
只用于打通流程与测试，**不代表任何真实案件**，且必须在 `GET /api/config` 中标注 `demo_data: true`。
演示照片必须是**不含任何真实人体内容的合成占位图**（纯色块 + 位置文字由前端叠加，不作为证据）。

---

## 6. 文件所有权（并行开发纪律）

同一时刻**不同子智能体不得编辑同一文件**。

| 工作流 | 唯一所有者 | 独占文件 |
| --- | --- | --- |
| W0 契约（已完成，勿改） | 主 Agent | `backend/app/constants.py`, `backend/app/schemas.py`, 所有配置文件, `docs/requirements.md`, `docs/architecture.md`, `docs/TODO_DOWNLOADS.md` |
| W1 后端核心 | 子智能体 A | `backend/app/storage.py`, `backend/app/database.py`, `backend/app/workflow.py`, `backend/app/seed.py`, `backend/app/main.py`, `backend/app/routes.py`, `backend/scripts/smoke.py` |
| W2 后端管道 | 子智能体 B | `backend/app/imaging.py`, `backend/app/ocr/*`, `backend/app/extractor.py`, `backend/app/terminology.py`, `backend/app/llm.py` |
| W3 后端产出 | 子智能体 C | `backend/app/docgen.py`, `backend/app/checker.py` |
| W4 前端（Vue 源码） | 子智能体 D | `frontend/src/App.vue`、`frontend/src/components/**` |
| W4b 前端（可运行工作台） | 子智能体 E | `frontend/static/**`（无构建依赖，FastAPI 直接提供） |
| W5 测试 | 子智能体 E/F | `backend/tests/**` |
| W6 评审 | 评审子智能体 | 只读；报告写到 `docs/reviews/*.md` |

`frontend/src/api.js`、`frontend/src/main.js`、`frontend/src/styles.css`、`frontend/index.html`
与所有 `frontend/*.config.js`、`package.json` 由主 Agent 冻结，前端子智能体只读。

跨工作流依赖只通过**本文件第 5 节的函数签名**与**第 4 节的 HTTP 契约**发生。

---

## 7. 前端接口与组件契约

### 7.1 `frontend/src/api.js`（已冻结）

```js
export const api = {
  config: () => request('/config'),
  stats: () => request('/stats'),
  verifyAi: (apiKey) => ...,
  listCases: (filters) => request(`/cases?${qs(filters)}`),
  case: (id) => request(`/cases/${id}`),
  createCase: (payload) => ...,
  updateCase: (id, payload) => ...,          // payload 含 expected_version
  updateStage: (id, payload) => ...,
  deleteCase: (id, expectedVersion) => ...,
  caseSummary: ...,
  quickRun: (payload) => ...,                              // POST /api/quick-run（普通用户主流程）
  addMaterial: (id, payload) => ...,
  listMaterials: (id) => ...,
  updateMaterialPage: (id, materialId, pageNumber, payload) => ...,
  preprocess: (id, materialId, payload) => ...,
  materialFileUrl: (id, materialId, page, variant) => ...,   // 原件/派生图预览
  updatePhotoPlacement: (id, materialId, payload) => ...,     // 部位照片目标位置（N-11）
  photoSlots: (id) => ...,                                    // 照片栏位视图
  runOcr: (id, payload) => ...,
  ocrBlocks: (id, filters) => ...,
  runExtract: (id, payload) => ...,
  fields: (id) => ...,
  updateField: (id, fieldKey, payload) => ...,
  injuries: (id) => ...,
  createInjury: (id, payload) => ...,
  updateInjury: (id, injuryId, payload) => ...,
  deleteInjury: (id, injuryId, expectedVersion) => ...,
  reorderInjuries: (id, orderedIds, expectedVersion) => ...,
  runNormalize: (id, payload) => ...,
  rules: (filters) => ...,
  createRule: (payload) => ...,
  updateRule: (ruleId, payload) => ...,
  deleteRule: (ruleId, expectedVersion) => ...,
  createDocument: (id, payload) => ...,
  documents: (id) => ...,
  approveDocument: (documentId, payload) => ...,
  runCheck: (id, payload) => ...,
  checklist: (id, filters) => ...,
  updateChecklistItem: (itemId, payload) => ...,
  createCorrection: (id, payload) => ...,
  corrections: (id) => ...,
  deliver: (id, payload) => ...,
  downloadUrl: (documentId, fmt) => `/api/documents/${documentId}/download?fmt=${fmt}`,
}
```

`request()` 约定：非 2xx 抛出带 `status` 与 `message` 的 `Error`，`409` 必须由调用方
触发「刷新后重试」提示。

### 7.2 组件树

```text
App.vue
├── Sidebar.vue                 导航：案件列表 / 复核中心 / 术语规则 / 系统配置
├── StatsCards.vue              统计卡
├── CaseTable.vue               案件表（服务端分页/筛选）
├── CreateCaseModal.vue         建案（含 intake 任务信息表）
├── CaseWorkspace.vue           案件工作台（抽屉或整页）
│   ├── WorkflowStepper.vue     10 阶段步骤条 + 推进/回退
│   ├── MaterialPanel.vue       材料清单、页质量标记、预处理、OCR、原图预览
│   ├── PhotoPanel.vue          部位照片：上传、缩略图、目标位置指派、顺序调整
│   ├── FieldReviewPanel.vue    字段核对：原图/原文/识别/确认值/状态/来源
│   ├── InjuryPanel.vue         损伤逐条编辑与排序
│   ├── TerminologyRulesPanel.vue 规则管理
│   ├── DocumentPanel.vue       文稿版本、生成、下载、审核
│   ├── ChecklistPanel.vue      校核清单、处理/豁免
│   └── CorrectionModal.vue     四类修正录入
├── AISettingsModal.vue         模型接入（密钥仅内存）
└── 全局 toast / 错误横幅
```

前端硬性要求：

- 四步主流程（R-1）：**选择材料 → 核对疑点 → 查看文档 → 确认修改**，每个动作在界面上有明确入口。
- 字段核对必须同时展示**原文、来源文件与页码、原页位置（bbox）、识别结果、确认后的值、
  规范化文字、使用的规则、复核状态**（R-15）。
- 空白字段显示“未记录”，**不得**显示“未见异常”（N-5）。
- 显示 `unclear` 字段时必须有醒目的“待辨认/冲突”标记（R-44）。
- `opinion` 栏目必须标注“由专业人员提供”，且只提供手工输入（N-1）。
- 409 统一处理：刷新案件详情 + 列表，并提示“已被其他操作更新，请刷新后重试”。
- 不出现遗留占位符、文字遮挡、表格溢出（R-52 格式质量）。
- 全部文案中文；不引入额外 npm 依赖（`vue` + `lucide-vue-next` 之外）。
- 部位照片必须标注“**系统不读取照片内容**，只按你选择的位置插入”，且位置只能从目录中选择（N-11/R-58）。

### 7.3 `frontend/static/`（无构建依赖工作台，当前唯一可实际运行的前端）

`frontend/dist` 不存在时（TODO-DL-3：`npm install` 未执行），`main.py` 在 `/` 提供
`frontend/static/index.html`，静态资源挂在 `/static`。该工作台是**纯 ES 模块 + fetch 的原生实现**，
不使用任何打包器，也不依赖任何 CDN 资源，必须覆盖同一个四步主流程与全部契约端点：

| 区块 | 必须提供 |
| --- | --- |
| **主视图「整理」（默认视图，R-59/R-62）** | 拖拽/多选上传（照片、Word、PDF、JSON）、每份文件的类型芯片（默认「自动判断」，可改；选「部位照片」时出现目标位置下拉，取自 `photo_positions`）、输出格式选择（Word/PDF/两者）、一键「开始整理」（`POST /api/quick-run`）、结果面板（成品下载、字段/损伤/疑点/阻断摘要、自动归类说明、进入案件记录） |
| **次级「案件记录」** | 原四步工作台的全部能力（材料、字段来源核对、损伤、部位照片、文稿、校核清单、修正、阶段推进） |
| 案件列表 | 分页/搜索/筛选、建案（含 intake 表）、删除 |
| 材料 | 选择文件上传（`FileReader` → base64）、质量标记、预处理、运行 OCR、原图预览、识别结果分页 |
| 部位照片 | 上传、缩略图、目标位置下拉（`/api/config` 的 `photo_positions`）、`PATCH .../placement`、顺序调整 |
| 核对疑点 | 字段表（8 项来源：原文/来源文件/页码/bbox/识别结果/确认值/规范化文字/规则/复核状态）、损伤逐条编辑、术语整理、校核清单处理 |
| 查看文档 | 生成文稿、版本列表、`docx`/`pdf` 下载（`downloadUrl`）、approve |
| 确认修改 | 四类修正提交、`opinion` 手工录入（标注“由专业人员提供”） |
| 全局 | 409 冲突提示并刷新、错误横幅、加载态；空白字段显示“未记录”，**绝不**显示“未见异常” |

静态工作台与 Vue 源码共享同一份端点定义；两者都不得绕过 `expected_version` 或把 JSON 字符串直接展示。

---

## 8. 预留接口（需要下载的待办）

| 待办 | 预留位置 | 当前可运行路径 | 解锁后的行为 |
| --- | --- | --- | --- |
| TODO-DL-1 `docxtpl` | `docgen.render_document(..., engine="docxtpl")`、`docgen.docx_engine_status()` | `engine="builtin"`（python-docx + Jinja2），`auto` 自动回退 | `auto` 自动改用 docxtpl，`/api/config` 报告 `docxtpl_available: true` |
| TODO-DL-2 `paddleocr` | `ocr/paddle.py::PaddleOcrEngine` | `sidecar` 引擎（离线 JSON） | `get_engine("paddle").available()` 为真，可识别真实扫描件 |
| TODO-DL-3 npm 依赖 | `frontend/`（源码完整） | 后端 API 全可用；`frontend/dist` 缺失时 `/` 由 `frontend/static/**` 提供 | `npm run build` 后 `<http://127.0.0.1:9765>` 直接打开 Vue 版工作台 |
| TODO-DL-4 `pypdfium2` | `imaging.render_pdf_pages()` | 已安装的 `PyMuPDF`（`fitz`） | 优先使用 pypdfium2 |
| TODO-DL-5 云端 OCR | `ocr/__init__.register_engine()` | 未注册 | 注册后可在 `resolve_engine` 中选用 |
| TODO-DL-6 真实样本 | `seed.seed_demo_case()` | 内置演示案件 | 替换为真实样本与已审核文稿 |

**禁止**在任何子任务中执行 `pip install`、`npm install` 或任何联网下载；
需要下载时只在交付说明中标注 `TODO-DL-x`。

---

## 9. 验收命令

```powershell
# 1. 后端自动测试（离线可运行）
python -m pytest

# 2. 后端可启动性 + 配置探测（离线可运行）
python -c "from backend.app.main import create_app; create_app('data/smoke.db'); print('app ok')"
python -c "from backend.app.docgen import docx_engine_status, detect_libreoffice; print(docx_engine_status(), detect_libreoffice())"
python -c "from backend.app.ocr import engine_status; print(engine_status())"

# 3. 端到端离线演练（演示案件 → 识别 → 提取 → 整理 → 生成 → 校核）
python -m backend.scripts.smoke

# 4. 前端构建（需要 TODO-DL-3）
Set-Location frontend; npm install; npm run build; Set-Location ..

# 5. 无构建前端（当前可运行：静态工作台）
python -m backend.scripts.serve                    # 默认端口 9765（--port N 可改）
# 浏览器打开 http://127.0.0.1:9765/ → 应返回静态工作台页面（含“部位照片”区块）
```

离线可完成的是 1—3 与 5；4 记账为 TODO-DL-3。

## 10. 完成定义（Definition of Done）

1. `python -m pytest` 全绿，且测试覆盖每个 `R-*`/`N-*` 条目至少一次。
2. `python -m backend.scripts.smoke` 输出完整链路证据（含生成文件路径与校核清单摘要）。
3. 三份评审报告写入 `docs/reviews/`，其中的 `blocking` 问题全部关闭或有明确豁免理由。
4. 交付说明列出：修改文件清单、实际验证结果、`TODO-DL-*` 未解项、未解决限制。
5. 端到端产出物检查：生成的 `.docx` 中，每个已指派位置的照片都以图片形式落在对应栏位段落，
   未指派位置的照片未进入文稿；`.pdf` 与 `.docx` 同版本；正文无 `{{ }}` 残留。
6. `GET /` 返回可操作的工作台页面（无构建依赖），四步主流程各有入口。
