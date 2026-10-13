# 评审报告（W6 评审 B）· 后端契约与并发

- 评审对象：`backend/**`（W1 后端核心 / W2 后端管道 / W3 后端产出）
- 评审基线：`docs/architecture.md`（冻结基线 v1.0，931 行）、`docs/requirements.md`、`docs/TODO_DOWNLOADS.md`、`AGENTS.md`
- 评审方式：**只读**读码 + 实跑（未修改任何代码；探针只写入 `%TEMP%`）
- 评审日期：2026-10-13

---

## 1. 方法与证据来源

### 1.1 实际执行过的命令

| # | 命令 | 结果 |
| --- | --- | --- |
| 1 | `python -V` | `Python 3.13.3` |
| 2 | `python -m pytest -q` | **退出码 5（no tests collected）**，无任何输出 |
| 3 | `python -m compileall -q backend` | 退出码 0 |
| 4 | `python -m backend.scripts.smoke` | 退出码 0，全部 `[OK]`（23 项断言） |
| 5 | `python %TEMP%\w6_probe.py` | 82 PASS / 5 FAIL（其中 3 条 FAIL 为探针自身假设问题，真实缺陷 2 条；见 §3） |
| 6 | `python %TEMP%\w6_probe_sig.py` | `§5 签名核对：通过 123 / 不符 0`；`§4 端点核对：契约 42 条，缺失 0 条，额外 0 条` |
| 7 | `python -m backend.scripts.serve --port 8021 --db %TEMP%\w6_serve\serve.db --storage %TEMP%\w6_serve\storage --no-seed` + `Invoke-WebRequest` | `GET /` 200 / 4452 B；`GET /static/workbench.js` 200 / 173906 B；`GET /api/health` 200；`GET /api/config` 200 / 15310 B；过期版本 `PATCH /api/cases/1` → **409**；`GET /api/cases/9999` → 404「案件不存在：9999」 |
| 8 | 数据层直测 `db.add_material / update_material_page / set_photo_placement / register_pages`（过期版本） | 全部抛 `VersionConflict` |
| 9 | `grep` 全仓（密钥 / 日志 / `_claim` / INV 关键点） | 见各节 |

> 端口 8021 的服务已在验证后停止：`job_kill` 后 `Testing-NetConnection 127.0.0.1:8021` 返回 `False`，`TCP connect failed`（端口已释放）。

### 1.2 探针文件（均在 `%TEMP%`，未污染仓库）

- `%TEMP%\w6_probe.py`：A 旧库迁移 / B HTTP 契约与乐观锁 / C `_handle_error` 全表 / D INV-6 与连接释放 / E 密钥边界。
- `%TEMP%\w6_probe_sig.py`：§5 冻结签名（`inspect.signature` 参数逐个比对）+ §4 端点集合比对。
- 日志：`%TEMP%\w6_probe_out.txt`、`%TEMP%\w6_smoke.txt`、`%TEMP%\w6_serve.log`。

### 1.3 smoke 证据摘录（`python -m backend.scripts.smoke`）

```
[OK  ] OCR 完成（sidecar）：写入 18 个文本块，跳过照片 4 张
[OK  ] R-55 照片列入 skipped_photo_ids：[7, 4, 5, 6]
[OK  ] N-11 照片材料 ocr_blocks 恒为 0：照片 4 张，共 0 个识别块
[OK  ] 字段提取：字段 11 项、损伤 2 条、冲突 1 处
[OK  ] R-16 损伤逐条：2 条
[OK  ] 术语整理：字段 11 项、损伤 2 条、待确认 7 项
[OK  ] R-56 inline_shapes == 可用照片数：inline_shapes=3，已指派可用照片=3
[OK  ] 正文无占位符残留：未发现 {{ }} / {% %}
[OK  ] 正文不含“未见异常”：未命中
[OK  ] PDF 与 DOCX 同版本且页数 ≥ 1：PDF 页数=5
[OK  ] not_recorded 一律“未记录”且不含“未见异常”：空白字段 3 个：commission_date、exam_time、clothing
[OK  ] 校核清单已写入：23 条
[OK  ] run_check 有输出：校核 23 项，其中阻断 6 项
[OK  ] 交付保存（已审核文稿）：交付已保存：文稿 v1，格式 docx、pdf
演练全部通过：2026-10-13T13:12:40+00:00
```

---

## 2. 逐项核对表

### 2.1 表结构（§2）

| 项 | 结论 | 文件:行号 | 证据 |
| --- | --- | --- | --- |
| 11 张表齐备 | ✅ | `backend/app/database.py:294-527`（`SCHEMA_SQL`） | 探针 A6：实际 11 张 `case_events, case_fields, case_injuries, cases, checklist_items, corrections, documents, material_pages, materials, ocr_blocks, terminology_rules` |
| §2 关键列逐列对齐 | ✅ | 同上 | 探针 A7（按 §2 表格逐表逐列集合比对）：`缺列：{}` |
| 索引 | ✅ | `database.py:517-526` | 探针 A8：10 条 `idx_*` 全部建立（与 `SCHEMA_SQL` 中 10 条 `CREATE INDEX` 一致） |
| `materials.position_key` / `photo_caption` | ✅ | `database.py:329-330`（DDL）、`database.py:531-534`（迁移定义） | 探针 A2/A5 |
| 幂等迁移可在**旧库**原地升级 | ✅ | `database.py:611-631`（`_migrate`）、`database.py:633-637`（`migrate()`） | 探针 A1～A5：用 `ALTER TABLE materials DROP COLUMN` 造出「缺两列但已有数据」的旧库 → `initialize()` 补列成功；旧行 `('old.png','','')` 保留（不重建表）；二次 `migrate()` 返回 `[]`（幂等） |
| 迁移覆盖范围 | ⚠️ minor | `database.py:619-631` 只遍历 `MATERIAL_COLUMN_MIGRATIONS` | 只有 `materials` 两列有迁移路径；`materials.clarity_note`、`case_injuries.rule_note`、`documents.note` 等后加列**没有**迁移项。若继续演进 schema，必须同步扩展该表（见缺陷 B9） |

**识别出的「契约未列出但实现中存在」的列**（不算违反 §2「关键列」写法，但应在 §2 或交付说明中登记，避免后续模块按错结构读写）：

- `cases`：`commission_unit, commission_matter, commission_date, case_summary, subject_name, intake_json, note`
- `materials`：`clarity_note`
- `material_pages`：`case_id, note`
- `case_injuries`：`applied_rule_codes_json, rule_note, version`
- `documents`：`note`
- `checklist_items`：`note`

### 2.2 乐观锁覆盖（INV-9 / §4「expected_version 归属」）

> **判断口径**：先以「正确版本成功 + 同一版本第二次必然 409」在 HTTP 层实测（等价于并发重放）；无法经 HTTP 触达的用数据层直测。

| 端点 | 归属版本 | 实现位置 | 实测 |
| --- | --- | --- | --- |
| `PATCH /api/cases/{id}` | 案件 | `routes.py:495-500` → `database.py:1052` | ✅ 首次 200 → 过期 **409**（TestClient 探针 B3；真实服务器同样 409） |
| `PATCH /api/cases/{id}/stage` | 案件 | `routes.py:503-508` → `database.py:1076` | ✅ 跳两格 → 409「阶段只能前进或回退一格…」（B4） |
| `DELETE /api/cases/{id}` | 案件（查询参数） | `routes.py:511-513` → `database.py:1102` | ✅ 过期 → 409（B34） |
| `POST /api/cases/{id}/materials` | 案件（**请求体**） | `routes.py:536-559` → `database.py:1216` | ❌ **HTTP 恒 422**（B5/B5b）；数据层 `add_material` 的 `_claim` 有效（过期 → `VersionConflict`，见 §1.1 命令 8） |
| `PATCH .../materials/{id}/placement` | 案件 | `routes.py:599-607` → `database.py:1354` | ✅ 过期 → 409（B8）；数据层过期 → `VersionConflict` |
| `PATCH .../materials/{id}/pages/{n}` | 案件 | `routes.py:625-633` → `database.py:1538` | ✅ 200 / 过期 409（B9）；数据层过期 → `VersionConflict` |
| `POST .../materials/{id}/preprocess` | 案件 | `routes.py:636-643` → `workflow.py:163` | ✅ 代码路径 + 入口 `claim`（`run_preprocess`） |
| `POST /api/cases/{id}/ocr` | 案件 | `routes.py:651-659` → `workflow.py:243` | ✅ 入口 `claim`（B31 正常路径 200） |
| `POST /api/cases/{id}/extract` | 案件 | `routes.py:672-684` → `workflow.py:352` | ✅ 入口 `claim`（B37 200） |
| `PATCH /api/cases/{id}/fields/{key}` | 案件 | `routes.py:694-699` → `database.py:1775` | ✅ 200 / 过期 409（B11） |
| `POST /api/cases/{id}/injuries` | 案件 | `routes.py:714-719` → `database.py:2027` | ✅ 200（B14） |
| `PATCH /api/cases/{id}/injuries/{id}` | 案件 | `routes.py:729-734` → `database.py:2111` | ✅ 过期 → 409（B15） |
| `DELETE /api/cases/{id}/injuries/{id}` | 案件（查询参数） | `routes.py:737-741` → `database.py:2136` | ✅ 200 / 过期 409（B17） |
| `POST .../injuries/reorder` | 案件（**查询参数**） | `routes.py:722-726` → `database.py:2152` | ✅ 过期 → 409（B16） |
| `POST /api/cases/{id}/normalize` | 案件 | `routes.py:749-758` → `workflow.py:508` → `database.py:1887` | ✅ 200 / 过期 409（B18） |
| `POST /api/terminology-rules` | — | `routes.py:767-770` → `database.py:2200-2217` | ⚠️ **新建资源无版本概念**（返回规则 `version=1`，B19）；〔§4 第 166 行「所有写操作必须携带 `expected_version`」措辞与「POST 建资源」冲突，见缺陷 B11〕 |
| `PATCH /api/terminology-rules/{id}` | **规则** | `routes.py:773-778` → `database.py:2283` `_claim_rule` | ✅ 200 / 过期 409（B20）；detail 复用案件文案（B20b） |
| `DELETE /api/terminology-rules/{id}` | **规则** | `routes.py:781-785` → `database.py:2295` `_claim_rule` | ✅ 过期 → 409（B21） |
| `POST /api/cases/{id}/documents` | 案件 | `routes.py:793-803` → `workflow.py:618-629` → `database.py:2366` | ✅ 200（B22）；过期 → 409（B23） |
| `POST /api/documents/{id}/approve` | **所属案件** | `routes.py:839-844` → `database.py:2430-2438` | ✅ 过期 → 409（B26）；正常 → `approved`（B26b） |
| `POST /api/cases/{id}/check` | 案件 | `routes.py:852-860` → `workflow.py:667` | ✅ 入口 `claim`（B24/B25 的版本推进即由此产生） |
| `PATCH /api/checklist/{item_id}` | **所属案件** | `routes.py:871-876` → `database.py:2622-2628` | ✅ 过期 → 409（B25）；正常 200（B25b） |
| `POST /api/cases/{id}/corrections` | 案件 | `routes.py:884-889` → `database.py:2682` | ✅ 200（B29）；过期 → 409（B30） |
| `POST /api/cases/{id}/deliver` | 案件 | `routes.py:899-906` → `workflow.py:737` | ✅ 过期 → 409（B28） |

**关于「`replace_checklist` 不含版本、由 `workflow.run_check` 入口 `claim` 补齐」是否成立**

- **按字面成立**：`database.claim()`（`database.py:725-731`）在 `run_check` 入口被调用（`workflow.py:667`），且它确实做了条件 `UPDATE cases SET version=version+1 WHERE id=? AND version=?`（`database.py:642-664`）——过期请求在该点即抛 `VersionConflict` → 409。
- **但不等于「写入被保护」**：`claim()` 自开连接并 `commit()` 后**立即释放连接**（`database.py:729-731`），随后 `database.replace_checklist(case_id, document_id, items, actor)`（`database.py:2516-2580`）在**另一个事务**里执行。两次操作之间存在窗口，窗口内的并发写不会被发现。
- 结论：**记为 major 设计风险（B3）**。`docs/architecture.md:357-358` 的注释与 `workflow.py:9-11` 的说明措辞（「由这一次占位统一保护」）会让读者误以为等价于事务级保护，建议在文档中改为「入口先校验一次版本（best-effort），写入本身不在同一事务内」。

### 2.3 §4.2 错误映射

驱动方式：直接以 14 类异常调用 `routes._handle_error`（`routes.py:144-193`），并尽可能在 HTTP 层复现。

| §4.2 行 | 映射结果 | 能否经 HTTP 触发 | 证据 |
| --- | --- | --- | --- |
| `VersionConflict` → 409「案件已被其他操作更新，请刷新后重试」 | ✅ | ✅ | 探针 C；B3/B8/B9/B11/B15～B18/B20/B21/B23/B25/B26/B28/B30/B34 |
| `DuplicateCase` → 409「该案件编号已存在，不能重复建立」 | ✅ | ✅ | C；B2 |
| `InvalidTransition` → 409 `str(error)` | ✅ | ✅ | C；B4 |
| `BlockedByChecklist` → 409「存在未解决的阻断项，不能生成待签发稿：…」 | ✅ | ✅ | C；B24（detail 前缀完全匹配） |
| `ValueError` → 400 `str(error)` | ✅ | ✅ | C；B7「只有部位照片（kind=photo）可以指定目标位置，当前类型：commission」 |
| `OcrEngineUnavailable` → 409 | ✅（映射层） | ❌ **经 HTTP 不可达** | C 通过；B32 实测 `engine=paddle` 返回 **200** + `engine=sidecar`（`ocr/__init__.py:124-151` 静默回退），B32b 证明 `get_engine("paddle").recognize()` 确实抛 `OcrEngineUnavailable` |
| `DocgenUnavailable` → 409 | ✅ | ⚠️ 未构造（需模板不可用场景） | C 通过 |
| `LlmAPIError` → `error.status_code` / `error.message` | ✅（401/504 实测） | ❌ 未联网 | C：`LlmAPIError(401,"密钥无效")`→401、`(504,"模型超时")`→504；`llm.py:106-116` 不透传上游正文 |
| `KeyError` / `LookupError` → 404 | ✅ | ⚠️ 仅映射层 | C：`KeyError('missing')` → 404「资源不存在：'missing'」 |
| `NotFound` → 404 `str(error)` | ✅ | ✅ | C；B35/B36/B38/B39 |
| 其他 → 500「服务内部错误：{0}」 | ✅ | ✅ 逻辑路径 | C：`RuntimeError("boom")` → 500；`routes.py:193` 把内部异常文本拼进 detail（缺陷 B8） |
| 422（Pydantic） | ✅ 中文压缩 | ✅ | B40 `page_size` 越界、B41 `expected_version=0` |
| 错误体形状 `{"detail": "<中文>"}` | ✅ | ✅ | `routes.py:215-218` `_json_error`；所有实测响应均为 `{"detail": ...}` |

### 2.4 事务与连接

| 项 | 结论 | 文件:行号 | 证据 |
| --- | --- | --- | --- |
| `connect()` 所有路径都释放连接 | ✅ | `database.py:587-601`（`try/yield/commit` + `except rollback/raise` + `finally close`） | 60 次连续 `GET /api/cases/{id}` 前后进程打开文件数 2 → 2（D6） |
| 嵌套 `connect()` | ✅ 未发现 | 所有 `with self.connect()` 内的返回值都在 with 之外读取：`create_case:823`、`get_case:863`、`add_material:1290`、`upsert_fields:1747`、`create_injury:2066`、`create_correction:2754`、`replace_checklist:2580` | 代码核对；无自引用连接（否则 `timeout=15` 会自我阻塞） |
| 模型网络请求期间不持有写事务 | ✅ | `workflow.py:397-444`：先 `list_ocr_blocks`（各自开闭连接）→ `asyncio.run(llm.extract_with_llm(...))`(:402) → 之后才 `upsert_fields(:442) / upsert_injuries(:444)` | 代码核对；符合 `AGENTS.md` 硬性约束 10 |
| OCR/图像处理期间不持有写事务 | ✅ | `workflow.py:243`（claim，独立事务，已提交）→ 循环内 `ocr_engine.recognize`(:304) 与 `replace_ocr_blocks`(:312) 分离 | 代码核对 |
| LibreOffice 导出期间不持有写事务 | ✅ | `run_render` 的 `export_pdf`(`workflow.py:612`) 早于 `create_document`(:618)；`run_delivery` 的 `export_pdf`(`workflow.py:770`) 早于 `update_document_assets`(:781) | 代码核对 |
| `export_pdf` 是否可能长时间持锁 | ✅ 不会持锁；自身有超时 | `docgen.py:1968-2014`：`subprocess.run(..., timeout=timeout_value)`，默认 120 s、非法/≤0 回退 120 s | B22/B27b：本机 LibreOffice 生效，`W6-001-report-v2.pdf` 生成成功；smoke `PDF 页数=5` |
| 长耗时的写锁窗口 | ⚠️ minor | `run_preprocess` 在 `claim`(:163) 之后做图像预处理(:197-212) 才 `save_preprocessing`(:211)；`add_material` 在写事务内做磁盘写 `storage.write_original`(`database.py:1239/1241`) | 每次连接独立、`claim` 已提交，故不构成阻塞；但写锁窗口被放大（B7 关联） |

### 2.5 密钥边界

| 项 | 结论 | 证据 |
| --- | --- | --- |
| `X-DeepSeek-API-Key` 只作为请求头参数流转 | ✅ | 全仓 grep 仅 `routes.py:455`、`routes.py:674` 的 `Header(alias="X-DeepSeek-API-Key")`，以及错误文案/文档说明 |
| 不落库 | ✅ | 探针 E1：带 `X-DeepSeek-API-Key: W6SECRETPROBE-0123456789-ABCDEF` 调 `POST /api/cases/1/extract`（`use_llm=false`）后，对 `data/cases.db` + `storage/**` 共 16 个文件做**字节级**搜索 → `命中 []` |
| 不进 `case_events` | ✅ | 探针 E2：`case_events` 37 条记录 0 命中 |
| 不落日志 | ✅ | `backend/app/**` 内 `logging|logger.|log.|print(|traceback` → **零匹配**；`llm.py:119-122` 的 `_headers()` 是唯一出现密钥的位置，且不返回、不打印 |
| 不写文件 / 不进构建产物 | ✅ | 同 E1（`storage/**` 全部文件）；源码 grep 无字面密钥 |
| `verify_key` / `extract_with_llm` 是否有把密钥写进异常或日志的路径 | ✅ 未发现 | `llm.py:106-116` `_public_error()` 只输出固定中文文案与状态码；`llm.py:157-163` `_require_key()` 的空密钥提示不含密钥；异常链 `raise ... from error` 携带的是 httpx 异常（不含请求头） |

### 2.6 INV-1 ～ INV-11 逐条

| 不变量 | 代码位置 | 实跑证据 | 是否可绕过 |
| --- | --- | --- | --- |
| INV-1 `field_key` 来自 `FIELD_INDEX`，目录列派生 | `database.py:1668-1670`（非目录 key 丢弃）、`database.py:832-833`（`_sync_case_fields` 过滤）、`database.py:1682-1686`（group/label/display_order 由目录派生） | B10：建案即同步出 `case_fields`，`commission_unit` 等 4 项按目录顺序；B13：未知 key → 404 | 不可（写入层过滤；`field_key` 不在目录时既不 upsert 也不可 PATCH） |
| INV-2 `unclear`/`pending` 关键字段构成阻断 | `checker.py`（`FIELD_UNRESOLVED_CRITICAL`，基于 `CRITICAL_FIELD_KEYS`） | smoke：校核 23 项、阻断 6 项；B24 detail 明确列出「关键事实尚未定论（FIELD_UNRESOLVED_CRITICAL）×2」 | 可被人工 `waived`（设计允许，需填理由：`database.py:2619-2620`） |
| INV-3 `not_recorded` → 必为「未记录」且绝不含「未见异常」 | `database.py:1678-1681`（upsert）、`database.py:1795-1796`（update_field）、`database.py:1893-1895`（apply_normalization） | B12：PATCH 传 `status=not_recorded, normalized_text="未见异常"` → 落库 `normalized_text='未记录'`；smoke「正文不含“未见异常”」+「空白字段 3 个」 | 不可（三条写入路径都强制覆写） |
| INV-4 损伤逐条独立 | `database.py:1998-2005`（一行一 INSERT）、`database.py:2040-2061`（create_injury） | smoke「R-16 损伤逐条：2 条」；B14 两条独立行 | 不可（无任何合并逻辑） |
| INV-5 生成 `pending_issue` 前查阻断项 | `database.py:2357-2363`、`workflow.py:560-565` | B24：`blocking=9` → `POST .../documents {doc_status:"pending_issue"}` → **409**「存在未解决的阻断项，不能生成待签发稿：必填字段缺失（FIELD_REQUIRED_FIELD_EMPTY）…」（5 条标题） | 不可（两处独立拦截；`DocStatus` Literal 也限制为 `internal_draft`/`pending_issue`） |
| INV-6 修正后文稿 `superseded` + 相关字段回 `pending` | `database.py:2697-2737`（corrections）、`database.py:2758-2777`（`_invalidate_in_connection`）、`database.py:1814-1859`（`invalidate_reviews`） | D1 `approved → superseded`；D2 `review_state=pending`；D3 `corrections.applied=True` 且 `invalidated` 非空 | ⚠️ **部分**：`PATCH /api/cases/{id}/fields/{key}` 改 `confirmed_value` 只作废文稿（D4 ✅）**但不会**把该字段打回 `pending`（D5：`review_state` 仍 `confirmed`） |
| INV-7 非 `case_only` 必须写 `scope_value`，不跨案件 | `schemas.py:460-465`（Pydantic 校验）、`database.py:2672-2675`（`InvalidTransition`） | B30b：`scope="template"` + 空 `scope_value` → **422**「非 case_only 范围必须填写适用范围」 | 不可（双层拦截）；副作用见 §2.3 说明与缺陷 B6 |
| INV-8 仅 `approved` 可 deliver | `workflow.py:751-755` | B27：`internal_draft` → 409「仅已审核文稿可交付：当前状态为 internal_draft（INV-8）」；B27b：`approved` → 200 | 不可（deliver 前显式判断） |
| INV-9 所有写操作先 `_claim` | 见 §2.2 表 | 见 §2.2 | ⚠️ **可绕过**：`_claim` 在 `expected_version=0` 时不校验版本（`database.py:656-660`），而 `workflow._advance()`(`workflow.py:113-115`)、`upsert_injuries(..., 0, ...)`(`workflow.py:444`)、`update_document_assets(..., 0, ...)`(`workflow.py:781`)、`update_material_page` 之外的多处系统写均传 0。这是**有意的系统内部写**（`database.py:20` 已注明），但「INV-9 必须由调用方保证」意味着任何新增路由忘记传版本都会静默通过 |
| INV-10 `opinion_text` 只能用户请求写入 | `docgen.py:1754`（`opinion = _clean(data.get("opinion_text"))`）、`docgen.py:1798`（context 输出）、`database.py:1011-1022`（`allowed` 白名单） | 全仓 grep：`opinion` 的唯一来源是 `cases.opinion_text`；`build_context` 不从 OCR 推导 | 不可（无第二条赋值路径） |
| INV-11 `kind=photo` 只贡献文件与 `position_key` | `workflow.py:256-259`（OCR 跳过）、`workflow.py:357-360`（提取来源只含 `OCR_MATERIAL_KINDS`）、`workflow.py:165-171`（预处理跳过）、`database.py:1262-1274`（照片不生成副本、不写 `ocr_blocks`）、`checker.py:621-649`（`PHOTO_CONTENT_READ`） | B31：`skipped_photo_ids=[2,3]`、`blocks=0`；B31b：照片材料 `ocr_blocks total=0`；B37：提取来源集不含照片；smoke「R-55」「N-11 照片材料 ocr_blocks 恒为 0」 | 不可（三条管线都过滤）。**注**：`docgen.py:900-925` `_natural_width_cm()` 用 PIL 读取 `image.size[0]` 以获得原始像素宽度，用于 R-56 的「宽度 = min(栏位上限, 原始宽度)」；这是排版所需的尺寸读取，不做内容识别/描述/推断，不构成 N-11 违反，但建议在校核说明中显式声明，避免后续评审误判 |

---

## 3. 缺陷列表

### B1 · blocking · `POST /api/cases/{case_id}/materials` 在 HTTP 层恒返回 422，材料登记功能完全不可用

- **所有者**：**W1 后端核心（子智能体 A）** → `backend/app/routes.py`；同时需 **主 Agent** 更新 `docs/architecture.md:194` 的措辞（若修复方向改为查询参数）。
- **位置**：`backend/app/routes.py:536-559`
  ```python
  raw = dict(payload or {})
  version = raw.pop("expected_version", expected_version)   # ← 先从体里摘掉
  data = _payload(MaterialCreate, raw)                      # ← 再交给 Pydantic，字段已被摘掉
  ```
  而 `backend/app/schemas.py:158` 明确声明 `expected_version: int = Field(ge=1)`（**必填**），`backend/app/routes.py:541-543` 的 docstring 却写「`schemas.MaterialCreate` 未声明 `expected_version`」——注释与冻结契约不一致。
- **复现**（真实服务器，端口 8021）：
  ```
  POST /api/cases/1/materials
  body {"expected_version":1,"kind":"commission","filename":"live.pdf","media_type":"pdf","page_count":1}
  → 422 {"detail": "expected_version Field required"}

  POST /api/cases/1/materials?expected_version=1
  body {"kind":"commission","filename":"live2.pdf","media_type":"pdf","page_count":1}
  → 422 {"detail": "expected_version Field required"}

  GET /api/cases/1/materials → []（材料数 = 0）
  ```
  两种位置（体 / 查询参数）都失败；`MaterialCreate` 的 `position_key` 校验器也因此无法被独立验证（探针 B5c）。
- **影响**：R-1 四步主流程的第一步「选择材料」不可用；`frontend/static/workbench.js:2442-2450`（材料上传）与 `:2561-2570`（照片上传）均按契约在**请求体**携带 `expected_version`，因此真实后端上这两条主路径 100% 失败 → 阻断 R-12/R-13/R-54/R-58 与 smoke 之外的全链路。`python -m backend.scripts.smoke` 之所以通过，是因为它直接调用 `database.add_material()`（数据层正常）。
- **建议最小修复**（改 `routes.py` 一处）：
  ```python
  # 保留 pop 前的原始体交给 Pydantic，校验通过后再取版本
  data = _payload(MaterialCreate, dict(payload or {}))
  version = int(data.pop("expected_version"))
  ```
  （同时删除 `routes.py:541-543` 的错误 docstring 与 `expected_version: int = Query(0, ge=0)` 的兜底分支；`version < 1` 的 400 分支可保留作为防御式断言。）
- **验证方式**：重跑 `python %TEMP%\w6_probe.py` 的 B5/B5b 应转为 PASS；`python -m backend.scripts.smoke` 后追加一次 HTTP 材料登记。

### B2 · major · `OcrEngineUnavailable → 409` 经 HTTP 不可达（引擎不可用时静默回退 sidecar）

- **所有者**：**W2 后端管道（子智能体 B）** 决策，或由 **主 Agent** 统一裁决契约（`docs/TODO_DOWNLOADS.md:14` 与 `docs/architecture.md` §4.2）。
- **位置**：`backend/app/ocr/__init__.py:124-151`（`resolve_engine` 在首选不可用时回退 sidecar）、`backend/app/workflow.py:276-282`（只在 `resolve_engine` **抛异常**时才转 `StageUnavailable`）。
- **复现**：`POST /api/cases/1/ocr {"expected_version":N,"engine":"paddle"}` → **200**，`payload.engine = "sidecar"`，detail「识别 0 页…（引擎 sidecar）」；而 `backend/app/ocr/paddle.py` 的 `recognize()` 确实抛 `OcrEngineUnavailable`（探针 B32b）。
- **冲突点**：`docs/TODO_DOWNLOADS.md:14` 明确「调用时抛 `OcrEngineUnavailable`，API 返回 409 + 提示改用 sidecar 引擎或配置引擎」。当前实现是「静默回退 + payload 报告」，两者不一致；前端/校核无法通过 409 得到「请改用 sidecar」提示，§4.2 的该行也无 HTTP 触发路径（`DocgenUnavailable` 同理需构造场景）。
- **建议最小修复**（二选一，需主 Agent 裁决后落地文档）：
  1. 若要保留回退：在 `run_ocr` 中当 `preferred != 实际引擎名` 时把回退写进 `payload["engine_fallback"]`，并补一条 `checklist_items`（`severity=warning`）显式报告；同步修改 `TODO_DOWNLOADS.md:14`。
  2. 若要 409：`run_ocr` 改为直接 `ocr.get_engine(engine)` 并显式 `if not engine.available(): raise OcrEngineUnavailable(...)`（仅当 `engine` 非空且非 `none` 时）。

### B3 · major · `claim()` 与后续写入不在同一事务，`replace_checklist` 等「入口 claim」只提供 best-effort 保护

- **所有者**：**W1 后端核心（子智能体 A）** → `backend/app/database.py:725-731` + `backend/app/workflow.py:662-706`；措辞修订归 **主 Agent**（`docs/architecture.md:357-358`、`workflow.py:9-11`）。
- **复现/证据**：`run_check` 在 `workflow.py:667` 调用 `database.claim(case_id, expected_version, actor)`；`claim()` 内部 `with self.connect()` 在退出时 `commit()` 并 `close()`（`database.py:729-731`）。随后 `database.replace_checklist(...)`（`workflow.py:706`，实现在 `database.py:2516-2580`）开启**新连接**写入。同理适用于 `run_preprocess/run_ocr/run_extraction/run_delivery`（入口 claim 后各自多次独立写）。
- **影响**：入口版本校验通过后、写入前发生的并发修改不会被检测；「由这一次占位统一保护」（`workflow.py:9-11`）的表述与实际语义不符。
- **建议最小修复**：把阶段函数改为在同一 `with database.connect() as conn:` 内完成 claim 与全部写操作（需要给 `_claim` / `replace_checklist` / `replace_ocr_blocks` 等增加「接受外部 connection」的变体，或新增 `Database.transaction()` 上下文管理器）；若改动过大，至少在文档中把 INV-9 表述为「入口校验一次版本（best-effort），非事务级保护」。

### B4 · major · INV-9 的保护完全依赖调用方传对 `expected_version`；`expected_version=0` 绕过校验

- **所有者**：**W1（子智能体 A）**/`workflow.py` 调用点。
- **位置**：`database.py:651-660`（`if expected_version:` … `else:` 无条件 UPDATE）；调用方传 0 的位置：`workflow.py:113-115`（`_advance`）、`workflow.py:444`（`upsert_injuries(..., 0, ...)`；同语句上一行 `upsert_fields` 才带版本）、`workflow.py:781`（`update_document_assets(..., 0, ...)`）。
- **复现**：`_advance` 的注释即写明「系统内部推进：expected_version=0（不重复校验，但同样占位递增）」（`workflow.py:113`）。任何新增路由若忘记透传版本，会静默通过而不报错。
- **建议最小修复**：把「系统内部写」从 `0` 改为显式哨兵（如 `expected_version=None` + 独立的 `_claim_system()`），使「传 0」不再是「跳过校验」的同义词；或在 `_claim` 中对 `0` 加断言白名单（只允许来自 `workflow.py` 内部调用）。

### B5 · minor · `PATCH /api/cases/{id}/fields/{key}` 改 `confirmed_value` 时不会把该字段 `review_state` 打回 `pending`（INV-6 后半不完整）

- **所有者**：**W1（子智能体 A）** → `backend/app/database.py:1768-1812`。
- **复现**（探针 D5）：先把 `subject_name` 置 `status=recorded, review_state=confirmed`，再 `PATCH {"expected_version":N,"confirmed_value":"张三丰"}` → 文稿正确变 `superseded`（D4 ✅），但该字段 `review_state` 仍为 `confirmed`（D5）。
- **对比**：走 `POST /api/cases/{id}/corrections` 时会经 `_invalidate_in_connection` 正确打回 pending（D1/D2/D3 ✅）。
- **建议最小修复**：在 `database.py:1803-1805` 的分支里，`confirmed != previous["confirmed_value"]` 时一并 `UPDATE case_fields SET review_state='pending' WHERE case_id=? AND id=?`。

### B6 · minor · INV-7 的数据库分支被 Pydantic 422 抢先，§4.2 中 `InvalidTransition` 行在该端点不可达

- **所有者**：**主 Agent**（`schemas.py` 冻结）或 **W1**（`database.py`）。
- **证据**：`schemas.py:460-465` 的 `CorrectionCreate` model_validator 已对 `scope != case_only && !scope_value` 抛 `ValueError` → 422；实测 B30b 返回 422「非 case_only 范围必须填写适用范围」，`database.py:2674-2675` 的 `InvalidTransition` 永远不会被这个入口触发。
- **影响**：契约文档需要注明该前置条件由 422 承载；不算安全缺陷（不变量仍然成立）。
- **建议最小修复**：`docs/architecture.md` §4.2 补一行说明「`corrections` 的 `scope_value` 前置条件由 422 返回」。

### B7 · minor · `run_render` 先落盘后 `claim`：过期版本会留下孤儿文件；`version_no` 存在 TOCTOU → 可能 500

- **所有者**：**W1（子智能体 A）** → `backend/app/workflow.py:567-629`、`backend/app/database.py:2365-2398`。
- **证据**：`workflow.py:594`（`render_document`）与 `:612`（`export_pdf`）早于 `:618` 的 `create_document(...)`（内部 `_claim`）；过期版本时接口返回 409，但 `cases/<no>/documents/<no>-report-vN.docx/pdf` 已落盘且无人清理。另外 `next_document_version`(`workflow.py:568`) 与 `create_document` 内部重算版本号(`database.py:2368-2371`) 之间无锁；极端交错下会撞 `UNIQUE(case_id, doc_type, version_no)`，而 `database.py:2386-2391` 未捕获 `IntegrityError` → 500。
- **建议最小修复**：`create_document` 捕获 `sqlite3.IntegrityError` 并重试一次取号，或把版本号由 `next_document_version` 传入并去掉内部重算；在 `run_render` 的 `_claim` 失败分支删除刚生成的文件（或改用临时目录+成功后移动）。

### B8 · minor · 500 响应把内部异常文本拼进 `detail`

- **所有者**：**W1（子智能体 A）** → `backend/app/routes.py:193`：`detail="服务内部错误：{0}".format(error)`。
- **影响**：可能泄漏 SQL、路径等内部信息（例如 `sqlite3.IntegrityError` 的完整文本）。§4.2 只要求 500 与中文可读信息，不需要把异常正文外传。
- **建议最小修复**：`detail="服务内部错误"`；若需排查，把异常写入 `case_events`（当前 `backend/app/**` 完全没有日志通道，见 §2.5）。

### B9 · minor · 列级迁移只覆盖 `materials` 两列

- **所有者**：**W1（子智能体 A）** → `backend/app/database.py:529-534`、`:611-631`。
- **建议最小修复**：把 `MATERIAL_COLUMN_MIGRATIONS` 泛化为 `{table: {column: ddl}}`，至少覆盖 `materials.clarity_note`、`case_injuries.rule_note`、`documents.note`，避免下一次 schema 变更后旧库静默缺列。

### B10 · minor · `workflow.py:584-585` 假设 `context["photo_slots"]` 是 dict

- **所有者**：**W1（子智能体 A）** → `backend/app/workflow.py:581-588`。
- **证据**：`context.setdefault("photos", [photo for slot in context["photo_slots"].values() ...])`；只有 `if not context.get("photo_slots")` 为假（即非空 **list**）时会命中 `.values()` → `AttributeError` → 500。当前 `docgen.build_context` 返回 dict（`docgen.py:1801`）故不触发；但 §5.8 明确 docgen「同时兼容 dict 形态（key → 栏位）」与 list 形态，一旦 docgen 改为输出 list 即崩。
- **建议最小修复**：`slots_map = context["photo_slots"] if isinstance(context["photo_slots"], Mapping) else _slot_mapping(context["photo_slots"])`，再基于 `slots_map` 取值。

### B11 · minor · `POST /api/terminology-rules` 无 `expected_version`，与 §4「所有写操作必须携带」措辞冲突

- **所有者**：**主 Agent**（`docs/architecture.md`）。
- **证据**：`routes.py:767-770`、`database.py:2200-2217`；实测 B19 返回 200 且 `version=1`；同一文件的 `TerminologyRuleCreate`(`schemas.py:354-370`) 未声明 `expected_version`。
- **建议最小修复**：§4 第 166 行改为「所有**修改/删除**操作必须携带 `expected_version`；POST 创建资源不需要」，并在端点表中对 `POST /api/cases`、`POST /api/terminology-rules` 标注「新建，无版本」。

### B12 · minor · 注释/文档与代码不一致 + 死代码

- **所有者**：**W1（子智能体 A）** → `backend/app/routes.py:541-543`（docstring 称 `MaterialCreate` 未声明 `expected_version`，实际 `schemas.py:158` 声明了，且 `ge=1`）；**主 Agent** → `backend/app/schemas.py:190-191`（重复的 `return self`）。
- **建议最小修复**：删除错误 docstring；删除重复 `return self`（属冻结文件，须由主 Agent 改）。

### B13 · 交付级 · `backend/tests/**` 空 → `python -m pytest` 退出码 5，DoD 第 1 条不达标

- **所有者**：**W5 测试（子智能体 E/F）** → `backend/tests/**`。
- **证据**：`backend/tests/` 只有 `__init__.py`（0 字节）；`python -m pytest -q` 输出为空、退出码 **5**（`no tests collected`）；`pytest.ini` 的 `testpaths = backend/tests`。
- **影响**：`docs/architecture.md:925`（DoD 1「`python -m pytest` 全绿，且测试覆盖每个 `R-*`/`N-*` 条目至少一次」）未满足；CI 语义下退出码 5 视为失败；§4.2 的 `LlmAPIError`、`DocgenUnavailable`、`OcrEngineUnavailable` 分支以及「模型请求期间不持有写事务」这类只有测试能覆盖的断言目前缺证据。
- **建议最小修复**：按 W5 职责补齐 pytest（最低限度：§4.2 逐行映射、乐观锁 409 覆盖、INV-1～INV-11 各一条、`httpx.MockTransport` 覆盖 `llm.verify_key`/`extract_with_llm`）。

---

## 4. 无法验证项（明确列出）

1. **`LlmAPIError` 的真实网络路径**（401 / 429 / 502 / 504 由上游产生）：本机禁止联网，因此只用 `routes._handle_error` 直接驱动 + `llm.py:106-116` 静态核对；未调用 `POST /api/ai/verify`（真实密钥）与 `use_llm=true` 的提取路径。
2. **`use_llm=true` 端到端 + 「模型请求期间不持有写事务」的真实并发时序**：静态证据在 `workflow.py:397-444`；需用 `httpx.MockTransport` 补测（属 W5 职责）。
3. **多进程/多线程真实竞争压力**：本次用「过期版本重放」（同一版本第二次使用必然 `rowcount=0`）等价构造 409，未做多进程同时写同一行的压力测试，也未验证 SQLite `timeout=15` 在长事务下的表现。
4. **`DocgenUnavailable` 的 HTTP 触发场景**：需要构造「模板不可用/渲染器缺失」环境，本次仅验证映射层。
5. **TODO-DL-1 `docxtpl`**（未安装，当前走 `builtin`，smoke/B22 已证实 builtin 可用）、**TODO-DL-2 `paddleocr`**、**TODO-DL-4 `pypdfium2`**（本机用 `fitz`）。
6. **前端构建与浏览器主流程（TODO-DL-3）**：见另一份报告 `docs/reviews/review-frontend-domain.md`。

---

## 5. 结论

**后端契约实现度很高**：§5 冻结签名 **123/123 完全一致**（含参数名与顺序）；§4 端点 **42/42 齐全、无未登记端点**；§4.2 的 14 行映射**全部按契约返回状态码与中文 detail**；INV-1～INV-5、INV-7、INV-8、INV-10、INV-11 均有**代码位置 + 实跑证据**；`python -m compileall` 通过、`python -m backend.scripts.smoke` 全绿（含 `PHOTO_CONTENT_READ`/N-5/N-11 的端到端断言）。

但存在 **1 条 blocking**：`POST /api/cases/{id}/materials` 在 HTTP 层恒 422（体/查询参数两种位置都失败），使「材料登记 / 照片上传」这两条主路径完全不可用——这正是 R-1 第一步与 R-54/R-58 的入口，**必须在交付前修复**。

另有 **3 条 major**：
- 乐观锁的「入口 claim」与「实际写入」不在同一事务（B3），且 `expected_version=0` 可绕过校验（B4）——INV-9 目前是**约定级**而非**机制级**保护；
- `OcrEngineUnavailable → 409` 经 HTTP 不可达，与 `docs/TODO_DOWNLOADS.md:14` 明文矛盾（B2）。

**8 条 minor** 与 **1 条交付级缺口（无任何自动测试）**。建议的交付顺序：B1（改 `routes.py` 一处，30 分钟内）→ B13（补测试，用测试固化 §4.2/乐观锁/INV 断言）→ B2/B3/B4（契约裁决 + 事务边界重构）→ 其余 minor 批量清理。
