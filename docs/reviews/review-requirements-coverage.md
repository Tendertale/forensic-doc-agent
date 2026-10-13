# W6 评审 A：需求覆盖与红线评审报告

- 评审角色：子智能体 **W6 评审 A**（需求覆盖与红线评审）
- 工作目录：`C:\Users\Ardeon\Downloads\师表报告`
- 权限：**只读评审**。除本文件外未修改任何文件；未改动 `backend/**`、`frontend/**`、
  `docs/architecture.md`、`docs/requirements.md`、`docs/IMPLEMENTATION_PLAN.md`。
- 评审方式：**以「能否证伪」为目标**——对每条红线与需求先构造能使其失效的最小场景，
  再用真实命令运行取结果。**本报告所有结论均来自本机实际运行输出**，未把
  “文档这样写”“代码看起来是这样”当作“实现如此”。
- 遵守 N-11：评审过程中**未查看任何照片像素内容**，仅读取宽高/DPI 等几何元数据用于
  纵横比与宽度上限的几何比对。
- 报告落盘时间：2026-10-13 21:2x。

---

## 1. 方法与证据来源

### 1.1 实际执行的命令（逐条列出，可在同一工作目录重跑）

```powershell
# 基线
python -m pytest                                   # 三次快照，结果见 §1.2
python -m compileall -q backend
python -c "from backend.app.main import create_app; create_app('data/smoke.db'); print('app ok')"
python -c "from backend.app.docgen import docx_engine_status, detect_libreoffice; print(docx_engine_status(), detect_libreoffice())"
python -c "from backend.app.ocr import engine_status; print(engine_status())"
python -m backend.scripts.smoke
python -c "from backend.app import constants as C; ..."        # 导出枚举/目录/阈值
python -m backend.scripts.serve --port 8011                    # 后台作业，随后 HTTP 校验
Invoke-WebRequest http://127.0.0.1:8011/  /api/health /static/workbench.js
Select-String -Pattern '未见异常' / 'opinion' / 'GA/T' / 'OCR_MATERIAL_KINDS' ...
Get-ChildItem backend\tests -File | Select Name,LastWriteTime

# 自写只读探针（写在项目根，_probe_w6r1_* 前缀，未修改任何产品代码）
python _probe_w6r1_probe.py          # 红线主探针（N-1/N-5/N-11/N-12/R-28/R-56）
python _probe_w6r1_extract_bug.py    # /extract 409 最小复现 + OCR 引擎内核兜底
python _probe_w6r1_api_sweep.py      # 契约 §4 全部端点状态码扫描（43 次调用）
python _probe_w6r1_probe2.py         # 材料登记 / corrections / approve 后草稿标记
python _probe_w6r1_probe3.py         # W2 领域规则（R-4/R-23/R-30~R-37/N-3/N-4）+ R-46
python _probe_w6r1_probe4.py         # 照片链路 N-11/N-12/R-54~R-58 + N-8/N-10/R-19/R-25
python _probe_w6r1_probe5.py         # 模板照片锚点 vs 位置目录；生成文稿结构
python _probe_w6r1_probe6.py         # 损伤顺序 + 无字段行栏位渲染
python _probe_w6r1_probe7.py         # 3 条损伤判定循环渲染是否整体倒序
python _probe_w6r1_probe8.py         # 修正应用 R-7/R-38/R-39/R-40/INV-6 + 五类校核
python _probe_w6r1_probe9.py         # R-39 scope / source 类校核 / 模板栏目 / R-52
python _probe_w6r1_probe10.py        # INV-8 交付门槛 / R-41 版本关系 / R-48 / R-51
python _probe_w6r1_probe11.py        # 案件基础信息修改是否反映到文稿
```

所有探针均在 `%TEMP%\w6r1*` 下建独立 SQLite 与存储根，使用
`create_app(db_path=..., storage_root=..., seed_data=False)`，不触碰 `data/`、`storage/`
的既有内容（除 §1.1 中显式用 `data/w6serve.db` 启动一次 serve 用于 HTTP 校验）。

### 1.2 基线命令实测结果

| 命令 | 实测结果 |
| --- | --- |
| `python -m compileall -q backend` | **EXIT=0**（通过） |
| `python -c "... create_app('data/smoke.db')"` | **`app ok`**，EXIT=0 |
| `docgen` 引擎探测 | `{'preferred': 'builtin', 'docxtpl_available': False, 'todo': 'TODO-DL-1'} C:\Program Files\LibreOffice\program\soffice.exe` |
| `ocr.engine_status()` | `sidecar` available=True；`paddle` available=False（detail 含 `TODO-DL-2`）；`none` available=True |
| `python -m backend.scripts.smoke` | **SMOKE_EXIT=0**，9 步全通过；产出真实 `FY-2026-0001-report-v1.docx/.pdf`（PDF 5 页）；`inline_shapes=3`；照片 `ocr_blocks=0`；正文无 `{{ }}`、无“未见异常”；校核 23 条（blocking 6） |
| `python -m pytest`（**三次快照，测试目录在本评审期间被并发写入**） | ① 评审开始时 `backend/tests/` **只有 `__init__.py`** → **EXIT=5（未收集到任何测试）**；② 21:19 左右 W5 已落 6 个测试文件 → `1 failed`（`test_terminology.py::test_named_rule_saved_to_db_is_applied_by_normalize_endpoint`，失败原因即本报告 D1）；③ 21:21 新建的 `backend/tests/test_api_pipeline.py` 存在 `SyntaxError` → 收集中断 **EXIT=2**；④ 最终快照 **`4 failed, 128 passed, 5 xfailed`，EXIT=1** |
| `python -m backend.scripts.serve --port 8011` + `Invoke-WebRequest` | `GET /` → **200**，`text/html; charset=utf-8`，4022 字节，含「选择材料/核对疑点/查看文档/确认修改/系统不读取照片内容」；`GET /api/health` → **200** `{"status":"ok","service":"forensic-doc-agent"}`；`GET /static/workbench.js` → 200，158975 字节 |
| `node -v` / `npm -v` | v22.23.2 / 10.9.8（可用），但 `frontend/dist` **不存在**，`npm install` 为 TODO-DL-3（按约束未执行） |
| `Get-ChildItem docs\reviews` | **空目录**（评审开始时无任何报告；本文件是 W6 三份报告中落盘的第一份） |

### 1.3 并发写入说明与最终复验（重要）

本评审执行期间，**其它子智能体仍在并发修改源码与测试**（文件修改时间为实测）：

| 文件 | 最后修改时间 | 说明 |
| --- | --- | --- |
| `backend/app/routes.py` | 21:05:46 | W1 |
| `backend/app/schemas.py` | 21:07:30 | 冻结契约（主 Agent） |
| `frontend/static/workbench.js` | 21:08:33 | W4b |
| `backend/app/checker.py` | 21:09:07 | W3 |
| `backend/app/workflow.py` | 21:09:29 | W1 |
| `docs/architecture.md` | 21:10:01 | 主 Agent |
| `backend/app/demo_pipeline.py` | 21:11:02 | **评审期间新增的文件** |
| `backend/tests/test_api_pipeline.py` | 21:21:59 | W5（评审期间创建） |

因此为排除“读到旧代码”的误判，**四条 blocking 缺陷在 2026-10-13 21:27:31–21:27:36 对当时最新代码重跑复验**，
结论全部不变：

```
21:27:31 ############ D1 ############
第 1 次 POST /extract：expected_version=13 → HTTP 409 {"detail":"案件已被其他操作更新，请刷新后重试"}
第 2 次 POST /extract：expected_version=14 → HTTP 409 {"detail":"案件已被其他操作更新，请刷新后重试"}
第 3 次 POST /extract：expected_version=15 → HTTP 409 {"detail":"案件已被其他操作更新，请刷新后重试"}
--- 对照：直接调用 workflow.run_extraction(expected_version=0) ---
ok = True | detail = 提取字段 11 项、损伤 2 条（来源材料 2 个）；跨材料冲突 1 处；未启用模型提取（默认关闭）
############ D2 ############
[FAIL] 材料登记（expected_version 放请求体）成功  —— status=422 body={"detail":"expected_version Field required"}
[FAIL] 材料登记（expected_version 放查询参数）成功  —— status=422 body={"detail":"expected_version Field required"}
############ D3 ############
API 返回顺序: [(1, '前臂'), (2, '小腿'), (3, '额部')]
build_context 顺序: [(1, '左前臂见擦伤，范围为3 '), (2, '右小腿见挫伤，范围为4 '), (3, '额部表皮剥脱1×1cm')]
    3. 额部表皮剥脱1×1cm（明确记载）
    2. 右小腿见挫伤，范围为4 cm × 3 cm。（明确记载）
    1. 左前臂见擦伤，范围为3 cm × 2 cm。（明确记载）
############ D4 ############
[OK  ] 阻断项清空后 pending_issue 可生成
[FAIL] R-46 待签发稿正文已剔除草稿标记【待核对】  —— 【待核对】出现 1 次（doc_status=pending_issue）
[FAIL] checker 对刚生成的 pending_issue 不产出 DOCUMENT_DRAFT_MARKER（blocking）  —— 命中 1 条
21:27:36
```

**本报告引用的行号均以 21:27 复验时的代码为准**（D1/D2/D4 的行号在复验时逐条重新检索确认；
D3 的行号由 21:27 后的一次针对性阅读确认）。若在此之后源码再次变动，请以复验命令的输出为准，
行号可能需重新核对。

### 1.4 静态证据检索结果（grep/Select-String）
- `opinion` 全仓 85 处命中：**唯一写入路径**是 `schemas.py:128`（`CaseUpdate.opinion_text`）→
  `routes.py:495-500` → `database.py:1007/1020`（`update_case` 白名单）。无任何从 OCR/字段推导的写入。
- `未见异常` 全仓 82 处命中：产品代码中的每一处都属以下三类之一——① 需求/注释文本；
  ② `constants.py:413` `STATUS_KEYWORDS["negative"]` 把“未见异常”**识别为阴性所见**（原文如此，属 R-17 正常映射）；
  ③ `checker.py:864/118` `NOT_RECORDED_MISLABELED` 的**违规检测**、`terminology.py:52` 的**禁用串断言**、
  `database.py:1678-1681` 的**落库纠正**。未发现任何把空白写成“未见异常”的路径。
- `GA/T`：`backend/**` 与 `frontend/**` **零命中**，仅 `docs/requirements.md:178`（R-49 条文本身）。
- `OCR_MATERIAL_KINDS = ('commission','record_book','supplement')`，`photo` 不在其中。
- `backend/**` 无 `eval(` / `exec(` / `os.system` / 宏执行；`subprocess` 仅出现在
  `docgen.py:39/2008`（调用 LibreOffice 转 PDF）。

---

## 2. 覆盖矩阵

结论取值：`满足` / `部分满足` / `不满足` / `无法验证`。
“复现命令”列中的 `_probe_w6r1_*` 均指 §1.1 列出的探针脚本。

### 2.1 红线 N-1 ～ N-12

| 编号 | 结论 | 证据（文件:行号） | 复现命令与关键输出 |
| --- | --- | --- | --- |
| **N-1** 不生成鉴定意见 | **满足** | `schemas.py:128-132`（`CaseUpdate.opinion_text`，唯一写入口）、`database.py:1007/1020`（`update_case` 白名单）、`database.py:1678-1681`（`upsert_fields` 不触碰 `opinion_text`）、`docgen.py:1754` + `docgen.py:1798`（`opinion = _clean(data.get("opinion_text"))`，只此一处来源）、`workflow.py:17`、`checker.py:959-991`（`OPINION_FROM_SOURCE` blocking） | `python _probe_w6r1_probe.py`：`未经用户输入时 opinion_text 为空 ''` → OK；`OCR+extract+normalize 后仍为 ''` → OK；`PATCH 只发 opinion_text` → 200 且原样落库 → OK；`build_context()['opinion']` == 用户文本 → OK；`opinion_text 可清空且不回落为识别文本` → OK。grep 确认无第二条写入路径 |
| **N-2** 不增加原文没有的事实 | **满足** | `terminology.py` 未命中规则时原样返回（`constants.py` 规则模板只含原文已有要素）、`docgen.py:1498-1505`（损伤兜底注释“绝不补造未记载的特征”）、`checker.py` 无“补写”规则 | `python _probe_w6r1_probe3.py`：`normalize_text('其他一般所见：未见皮下出血', recorded)` → 原样返回、`requires_confirmation=True`、`applied_rule_codes=[]`；`N-3`/`N-4` 同批通过 |
| **N-3** 不加精度 | **满足** | `terminology.py`（大小写/空白归一但不动数值）、`constants.py:469` 规则用 `(?P<length>\d+(?:\.\d+)?)` 原样回填 | `_probe_w6r1_probe3.py`：`normalize_text('3')` → `'3'`，`'3.0' not in 结果` → OK |
| **N-4** 不改左右/否定词 | **满足** | `constants.py:410-415`（`NEGATION_WORDS`）、`terminology.py` negative 分支、`checker.py` `NEGATION_UNPRESERVED`(blocking) | `_probe_w6r1_probe3.py`：`normalize_text('未见皮下出血', negative)` → 保留「未见」→ OK；提取器 `side=左/右` 分别保留 → OK |
| **N-5** 空白≠正常 | **部分满足** | 满足部分：`terminology.py:369`（`not_recorded`→“未记录”）、`database.py:1678-1681`（落库纠正）、`docgen.py:1425-1426`（docx 恒“未记录”）、`frontend/static/workbench.js:238`、`frontend/src/components/FieldReviewPanel.vue:187`。**缺口**：`docgen.py:1470-1485` `_scalar()` 对**没有字段行**的已知字段返回 `""` | 满足侧：`_probe_w6r1_probe3.py` 8 个输入（`. ''/' '/'—'/'-'/'无记录'/'未记录'/'未见异常'`）全部 → `未记录` 且不含“未见异常”→ OK；`_probe_w6r1_probe.py` 强行写入 `not_recorded + 未见异常` → 落库被纠正为 `未记录` → OK；`_probe_w6r1_probe4.py` 生成 docx 正文不含“未见异常” → OK。**缺口侧**：`_probe_w6r1_probe6.py` 输出 `体重/尸温/角膜/检验人员` 4 个锚点「行=无 渲染值=''」，`_probe_w6r1_probe5.py` 生成文稿第 6/10/11 段分别为 `体重：`、`尸温：`、`角膜：`（空白，没有任何“未记录”）→ **D6** |
| **N-6** 案情保留来源属性 | **满足** | `docgen.py:1768`（`case_summary` 独立锚点）、模板中「案情简介」为独立栏目（`_probe_w6r1_probe9` 输出）、`case_fields` 保留 `source_material_id`/`source_page` | `_probe_w6r1_probe9.py`：模板含栏目「案情简介」→ OK；`_probe_w6r1_probe4.py`：`来源材料=[2,3]`（案情来自委托书材料，未并入尸表所见） |
| **N-7** 姓名不入委托单位栏 | **满足** | `constants.py` `FIELD_INDEX` 中 `subject_name` 与 `commission_unit` 为两个独立字段；无任何写路径互相拷贝 | `_probe_w6r1_probe4.py`/`_probe_w6r1_probe11.py`：`commission_unit='某市公安局某分局'`、`subject_name='李某某'` 各自独立落库；`_probe_w6r1_probe3.py`：`commission_date`(委托日期) 与 `exam_time`(检验时间) 为两个字段 |
| **N-8** 原件不可覆盖 | **满足** | `storage.py`（originals/derived 分离）、`routes.py:511-528`（删除仅针对案件目录）、`workflow.py:161`（预处理写 derived） | `_probe_w6r1_probe4.py`：预处理 3 个操作 + OCR 后 `全部原件 sha256 不变（变化材料 id=[]）`→ OK；`原件与识别用副本分目录`→ OK；`审核操作不修改已生成文稿字节`（sha 前 12 位 `72395c592bd8` 前后一致）→ OK |
| **N-9** 只作数据、不执行宏 | **满足** | `backend/**` 无 `eval`/`exec`/`os.system`/宏执行；`docgen.py` 用 `python-docx` 读模板（不执行宏）；`subprocess` 仅 `docgen.py:2008` 调 LibreOffice CLI，无 `shell=True` | `Select-String -Pattern 'subprocess|os\.system|eval\(|exec\(|shell=True|macro'` → 仅 3 处 docgen 命中（import 与 LibreOffice 调用） |
| **N-10** 案件相互隔离 | **满足** | `database.py` 全部查询带 `case_id`；`list_photo_slots(case_id)` | `_probe_w6r1_probe4.py`：两案件姓名字段不同（李某某 vs 王某某）→ OK；字段 id 无交叠 → OK；case2 未指派照片不出现在 case1 栏位 → OK |
| **N-11** 不读照片内容 | **部分满足** | 满足侧：`constants.py:127`（`OCR_MATERIAL_KINDS` 不含 photo）、`workflow.py:256-259`（`run_ocr` 跳过照片）、`workflow.py:165-171`（`run_preprocess` 跳过照片）、`workflow.py:359-360`（提取来源过滤）、`ocr/base.py:52-64`（`assert_ocr_kind`）、`checker.py:621-670`（`PHOTO_CONTENT_READ` blocking）、`docgen.py:890-897`（只 `is_file()`）、`docgen.py:900-925`（只读 `image.size`/`info['dpi']`）、`llm.py` 签名无图像参数。**缺口**：`ocr/sidecar.py:94-146` `SidecarOcrEngine.recognize()` **未调用 `assert_ocr_kind`**，而 `ocr/paddle.py:84` 调用了 | `_probe_w6r1_extract_bug.py`：`assert_ocr_kind('photo')` → 抛 `OcrEngineUnavailable` OK；`get_engine('paddle').recognize(kind='photo')` → 抛 `OcrEngineUnavailable` OK；**`SidecarOcrEngine().recognize(kind='photo')` → 未抛异常**（默认引擎无内核兜底）→ **D8**。`_probe_w6r1_probe4.py`：照片 `ocr_blocks==0`、无 derived 目录、未作为任何 `case_fields`/`case_injuries` 的来源材料、`inline_shapes=3` 且纵横比与原件一致（未看像素）→ 端到端未见照片内容外流 |
| **N-12** 空栏位写“（未提供照片）” | **部分满足** | 满足侧：`docgen.py:1008-1011/1034-1036`（无照片写 `PHOTO_PLACEHOLDER_TEXT`）、`constants.py:224`（`（未提供照片）`）、`docgen.py:1006`（只按本栏位 `photos` 取图，不顶替）。**缺口**：`docgen.build_demo_template` 只生成 14 个照片锚点，而 `constants.PHOTO_POSITION_DEFINITIONS` 有 18 个位置 → 4 个位置在演示文稿中**根本不存在栏位**，且 `GET /photo-slots` 仍按 18 个上报 | `_probe_w6r1_probe4.py`：`空栏位写入“（未提供照片）”且数量匹配` → **FAIL**（占位 11 次 / 空栏位 15 个）；`_probe_w6r1_probe5.py`：模板锚点 14 个，`目录中有、模板中无锚点（4 个）= ['waist_hip','perineum','hand_left','hand_right']` → **D7**。对模板已声明的 14 个栏位，11 个空栏位全部写了占位文字、栏位标题段落全部保留、无跨栏位顶替 |

### 2.2 R-1 ～ R-58

| 编号 | 结论 | 证据（文件:行号） | 复现命令与关键输出 |
| --- | --- | --- | --- |
| R-1 四步主流程入口 | **部分满足** | `frontend/static/workbench.js:167`（`{ n:1, title:'选择材料' }` 等四步）、`frontend/static/index.html:55-57`；Vue 侧 `frontend/src/App.vue` / `CaseWorkspace.vue` | `Invoke-WebRequest http://127.0.0.1:8011/` → 200，含「选择材料/核对疑点/查看文档/确认修改」；静态工作台**已 HTTP 验证**；Vue 3 侧仅源码核对（TODO-DL-3 不可构建） |
| R-2 委托书字段提取 | **不满足（API 层）** | `extractor.py` 逻辑可用；但 `routes.py:673-686` → `workflow.py:341-443` | `python _probe_w6r1_probe.py`：`POST /api/cases/{id}/extract` → **409 `{"detail":"案件已被其他操作更新，请刷新后重试"}`**；`_probe_w6r1_api_sweep.py` 同；旁路 `workflow.run_extraction(..., expected_version=0)` → `ok=True 提取字段 11 项、损伤 2 条、冲突 1 处` → **D1** |
| R-3 记录本顺序与对应 | **不满足** | 数据层顺序正确（`_probe_w6r1_probe6.py`：DB/API/`build_context` 均为 seq 1,2,3）；**docgen 渲染倒序**：`docgen.py:1735`（排序正确）+ 跨段 `{% for %}` 块渲染实现（`docgen.py` 段落块复制处） | `python _probe_w6r1_probe7.py`（3 条损伤）：API/`build_context` 顺序 `1,2,3`；生成文稿段落顺序 **`3. 额部…` → `2. 右小腿…` → `1. 左前臂…`**（整体倒序）→ **D3** |
| R-4 简写规范化 | **满足** | `terminology.py` + `constants.py:443-517`（6 条种子规则）+ `/api/terminology-rules` CRUD | `_probe_w6r1_probe3.py`：6 条规则编译；`尸长172cm`→`尸长172 cm。`；`背侧，紫红，指压褪色`→`尸斑位于背侧，呈紫红色，指压褪色。`。`_probe_w6r1_probe2.py`：规则 新增/修改/删除 均 200 |
| R-5 模板填充无残留 | **满足** | `docgen.render_document` + `_collect_unresolved`（`docgen.py:1108`） | `_probe_w6r1_probe4.py`：`正文无占位符残留（{{ }} / {% %}）` → OK；`_probe_w6r1_probe3.py`：pending_issue 正文无残留 |
| R-6 文档校核 | **满足** | `checker.py` 5 类别 + 23 个 code（`docs/architecture.md:586-610`）；`checker.py:1556-1572` 汇总顺序 | `_probe_w6r1_probe8.py`：健康案件产出 `content/correction/format/recognition`；`_probe_w6r1_probe9.py` 空案件产出 `source` → 五类全部可产出；`severity` ∈ `('blocking','warning','info')`、`status` ∈ `('open','resolved','waived')` |
| R-7 修改与再生成 | **满足** | `database.create_correction` / `apply` 路径；`_probe_w6r1_probe8.py` 实测 | 修正 `applied=True`，字段 `confirmed_value` 更新为修正后文本，`review_state` 回到 `pending`，依赖文稿 → `superseded` |
| R-8 第一版重点栏目 | **满足** | `constants.py:528-538`（`DEFAULT_SECTION_ORDER`） | `_probe_w6r1_probe9.py`：模板含「案情简介 / 尸表一般所见 / 损伤检验记录 / 部位照片 / 鉴定意见（由专业人员提供）/ 签名」→ 全部 OK |
| R-9 与原始记录对应、原件保留 | **满足** | `ocr_blocks`（原文+定位）、`case_fields.source_*`、storage originals 只读 | `_probe_w6r1_probe4.py`：原件 sha256 不变；`source_material_id/source_page/source_bbox_json/source_block_id` 齐全 |
| R-10 单流程控制 Agent | **满足** | `workflow.py`（10 阶段编排）+ `database.py`（唯一持久化） | `_probe_w6r1_probe3.py`：`run_ocr/run_extraction/run_normalization/run_render/run_check` 由同一 `case_id` 共享数据 |
| R-11 随时知道状态 | **满足** | `cases.stage`、`cases.doc_status`、`case_fields.review_state`、`/api/stats`、`get_case()` 的 `summary` | `_probe_w6r1_probe9.py`：`/api/stats` 键含 `stage_counts/unresolved_count/blocking_count/field_status_counts`；`GET /cases/{id}` 含 `summary` |
| R-12 每案独立材料集合 | **不满足（API 层）** | `schemas.py:155-169`（`MaterialCreate.expected_version: int = Field(ge=1)`）与 `routes.py:536-559`（第 546 行把 `expected_version` 从 payload 中 `pop` 后再校验）冲突 | `python _probe_w6r1_probe2.py`：请求体带 `expected_version` → **422 `{"detail":"expected_version Field required"}`**；查询参数带 `expected_version` → **同样 422**（`_probe_w6r1_api_sweep.py` 亦复现）→ **D2**。`post` 无法登记材料，但 seed 走 `database.add_material` 直连可正常建材料（演示案件 7 个材料） |
| R-13 支持 PDF/JPG/PNG | **满足** | `MaterialCreate.media_type: Literal["pdf","jpeg","png","docx","json","other"]`；`MATERIAL_KINDS` 7 类 | `_probe_w6r1_probe8.py`：`MATERIAL_KINDS` 覆盖 `commission/record_book/template/photo/approved_report/terminology_reference/supplement` → OK |
| R-14 结构化分组 | **满足** | `constants.FIELD_GROUPS`；`/api/config` 暴露 `field_groups` | `_probe_w6r1_probe9.py`：`/api/config` 键含 `field_groups/field_definitions` |
| **R-15** 八项来源 | **满足** | `database.py:378-393`（表列）、`database.py:1757`（`list_fields` 全列返回）、前端 `frontend/static/workbench.js`（字段表 8 项）与 `frontend/src/components/FieldReviewPanel.vue` | `python _probe_w6r1_probe.py`（同 `_probe_w6r1_probe9.py`）：`R-15 字段行含 8 项来源信息列` → **OK**；样例 `{"raw_text":"委托单位：某市公安局某分局","source_material_id":2,"source_page":1,"source_bbox_json":[...],"confirmed_value":"某市公安局某分局","normalized_text":"某市公安局某分局","applied_rule_codes_json":[],"review_state":"pending"}` |
| **R-16** 损伤逐条 | **满足** | `extractor.extract_injuries`（一行→至多一条）、`database.py` `case_injuries` 无合并列 | `_probe_w6r1_probe3.py`：2 行 OCR → 2 条损伤，`seq=1/2`、`side=左/右`、`part=前臂/小腿`、`3×2 / 4×3`（未错配）；`_probe_w6r1_probe4.py`：API 损伤逐条返回 |
| R-17 五种状态 | **满足** | `FIELD_STATUSES = ('recorded','not_recorded','not_examined','negative','unclear')`；`extractor.infer_status` | `_probe_w6r1_probe3.py`：8 个 `infer_status` 断言全部符合契约（空/`/`/`—`/`未记录`/`无记录`→`not_recorded`；`未检`→`not_examined`；`未见异常`→`negative`；其它→`recorded`） |
| R-18 PDF 类型区分 | **满足** | `imaging.py`（`probe()` 输出 `is_scanned/has_text_layer/text_layer_chars`，优先 `pypdfium2` 回退 `fitz`） | `_probe_w6r1_probe8.py`：源码含 `is_scanned`/`has_text_layer`/`pypdfium2`/`fitz` → OK；`material_pages` 同时存 `original_image_path` 与 `image_path` |
| R-19 原件/处理结果分离 | **满足** | `storage.originals_dir` / `derived_dir` | `_probe_w6r1_probe4.py`：`original=cases/FY-2026-0001/originals/commission/commission_scan.png`，`derived=cases/FY-2026-0001/derived/commission`；原件 sha256 不变 |
| R-20 印刷文字 OCR | **部分满足（契约允许降级）** | `ocr/paddle.py::PaddleOcrEngine`；`ocr/__init__.resolve_engine` 回退 `sidecar` | `ocr.engine_status()`：`paddle available=False`，detail 含 `TODO-DL-2`；`_probe_w6r1_extract_bug.py`：`resolve_engine('paddle').name == 'sidecar'`（回退） |
| R-21 手写输出疑点/候选 | **满足** | `ocr_blocks.confidence`、`workflow.py:388-395`（`confidence < OCR_LOW_CONFIDENCE` → `unclear`）、`checker.py` `OCR_LOW_CONFIDENCE`/`OCR_HANDWRITTEN_UNVERIFIED` | `python -m backend.scripts.smoke`：校核清单含 `[warning/open] OCR_LOW_CONFIDENCE`；`_probe_w6r1_probe3.py`：低置信度块 `颈部压退 0.48 < 0.55` 使字段置 `unclear` 并写 `rule_note` |
| R-22 字段必须关联来源 | **满足** | `checker.py` `FIELD_SOURCE_MISSING`（blocking） | `_probe_w6r1_probe9.py`：空案件 check → `codes=['FIELD_REQUIRED_EMPTY','FIELD_SOURCE_MISSING','FIELD_UNRESOLVED_CRITICAL','MATERIAL_MISSING']`，`FIELD_SOURCE_MISSING` severity=blocking ×5 |
| R-23 多义表达交用户确认 | **满足** | `terminology.py` + `terminology_rules.requires_confirmation` + `/api/terminology-rules` | `_probe_w6r1_probe3.py`：`压退`/`颈部压退` → `requires_confirmation=True`、`applied_rule_codes=['pressure_fading_ambiguous']`；未命中规则 → `requires_confirmation=True` |
| R-24 模板副本填充 | **部分满足** | `docgen.render_document` 写副本；`docgen.docx_engine_status()` → `builtin`（TODO-DL-1） | `_probe_w6r1_probe4.py`：模板原件 sha256 在生成前后不变；`docx_engine_status()={'preferred':'builtin','docxtpl_available':False,'todo':'TODO-DL-1'}` |
| R-25 PDF 页面效果 | **满足** | `docgen.export_pdf` → LibreOffice；`checker.py` `DOCUMENT_PDF_MISSING` | `_probe_w6r1_probe4.py`：`pdf 真实存在`、`PDF 页数=5`；`detect_libreoffice()` = `C:\Program Files\LibreOffice\program\soffice.exe` |
| R-26 内容+完整性校核 | **满足** | `checker.check_case` 5 类顺序固定 | 同 R-6 |
| R-27 疑点可生成内部草稿 | **满足** | `docgen.py:1757`（`draft_marker`）+ 模板 `{{ draft_marker }}`（`docgen.py:1363`） | `_probe_w6r1_probe3.py`：internal_draft 正文含【待核对】1 处 |
| **R-28** 阻断进入待签发稿 | **部分满足** | 满足侧：`workflow.py:560-565`（`pending_issue` 前查 `open_blocking_items` → `BlockedByChecklist`）、`routes.py:153-156`（→409） | `python _probe_w6r1_probe3.py`：存在 6 条 open blocking 时 `POST /documents{doc_status:'pending_issue'}` → **409**，detail = `存在未解决的阻断项，不能生成待签发稿：关键事实尚未定论（FIELD_UNRESOLVED_CRITICAL）…` → **OK（真实复现）**。`_probe_w6r1_api_sweep.py` 同。**但缺口**：`internal_draft → approved`（架构允许）→ `deliver` 放行，绕过该门；见 `_probe_w6r1_probe2.py` → **D5** |
| R-29 与人工复核流程衔接 | **部分满足** | 模板「签名与日期」+ `signature_block`（`docgen.py:1534-1536`）；电子签名/盖章按范围不实现 | `_probe_w6r1_probe5.py`：正文含 `检验人员签名：`、`复核人员签名：＿＿`、`鉴定机构（盖章）：` |
| R-30 保留原数值精度 | **满足** | `constants.py:480-493` `body_length_unit` 规则 | `_probe_w6r1_probe3.py`：`尸长172cm` → `尸长172 cm。` |
| R-31 不补颜色/深度/形态/数量 | **满足** | `constants.py:466-479` `injury_measure` 规则（`require_groups=('type','unit')`） | `_probe_w6r1_probe3.py`：`左前臂擦伤3×2cm` → 提取 `side=左 part=前臂 type=擦伤 3×2 cm`；无补充项 |
| R-32 不推导死亡时间 | **满足** | `constants.py:444-465` 规则 `boundary_note` | `_probe_w6r1_probe3.py`：`背侧，紫红，指压褪色` → `尸斑位于背侧，呈紫红色，指压褪色。`（无时间） |
| R-33 未注单位保留+提示 | **满足** | `constants.py:489-490` `require_groups=('unit',)` + `missing_group_note` | `_probe_w6r1_probe3.py`：`尸长172` → `'尸长172'`、`requires_confirmation=True`、`rule_note='尸长未注明单位，保留数值并提示确认单位。'` |
| R-34 含义不明提示确认 | **满足** | `constants.py:505-516` `pressure_fading_ambiguous`（`priority=5`，`template=""`） | `_probe_w6r1_probe3.py`：`压退` → `'压退'`、`requires_confirmation=True`；`颈部压退` 亦命中该规则 |
| R-35 空白→未记录 | **部分满足** | `terminology.py:369`、`database.py:1678-1681`、`docgen.py:1425-1426`；**缺口同 N-5**：`docgen.py:1470-1485` 无字段行时返回空串 | 同 N-5：接口/落库/`not_recorded` 行全部正确；模板中 `体重/尸温/角膜/检验人员` 因无字段行而渲染为空白（**D6**） |
| R-36 多处损伤不混用 | **满足** | `extractor.extract_injuries`（按行隔离） | `_probe_w6r1_probe3.py`：`a: side=左 part=前臂 3×2 | b: side=右 part=小腿 4×3`（未串行） |
| R-37 同简写多义处理 | **满足** | `terminology_rules` 的 `scope_field_key/scope_column/scope_writer` + `requires_confirmation` | `_probe_w6r1_probe2.py`：规则项含 `scope_field_key/scope_column/scope_writer/boundary_note/requires_confirmation`；`_probe_w6r1_probe3.py` 命中按 `(priority, code)` 升序取第一条 |
| R-38 修正记录六要素 | **满足** | `corrections` 表列 + `database.create_correction` | `_probe_w6r1_probe8.py`：返回含 `kind/before_text/after_text/reason/confirmed_by/scope/applied`；`GET /corrections` 可查（≥1 条） |
| R-39 适用范围 | **满足** | `schemas.py` `CorrectionCreate.scope ∈ (case_only, field_key, column, writer, template)` + `scope_value` 必填校验 | `_probe_w6r1_probe9.py`：`scope=column` 未填 `scope_value` → **422**（提示“非 case_only 范围必须填写适用范围”）；带 `scope_value='尸斑'` → 200、`scope_value` 落库 → OK（INV-7） |
| R-40 事实变化使复核失效 | **满足** | `database.supersede_documents` / `invalidate_reviews` | `_probe_w6r1_probe8.py`：修正后 `review_state` 回到 `pending`，依赖文稿 `internal_draft → superseded`（INV-6） |
| R-41 已审核版本保留 | **满足** | `documents` 版本表 + `_refresh_case_doc_status` | `_probe_w6r1_probe10.py`：v1 `approved` → 生成 v2 后 `{2:('internal_draft',2), 1:('superseded',1)}`，且 **v1 的 .docx 文件仍存在**；`交付 internal_draft` → 409 `仅已审核文稿可交付（INV-8）` |
| R-42 模板优先 | **满足** | `docgen` 保留模板段落格式（`_copy_run_properties`）；演示模板自带字体字号 | `_probe_w6r1_probe9.py`：模板前 12 段字体/字号 `['10.5','12.0','14.0','18.0','宋体']`；`_probe_w6r1_probe5.py`：正文段落格式继承模板 |
| R-43 来源可查 | **满足** | `case_fields.source_*` + `ocr_blocks` + `render.txt` 快照 | `_probe_w6r1_probe.py`/`_probe_w6r1_probe9.py`：8 项来源列可取；`_probe_w6r1_probe4.py`：`<docx>.render.txt` 快照存在 |
| **R-44** 冲突显式处理 | **满足** | `extractor.detect_conflicts` → `conflict_json`；`checker.py` `FIELD_CONFLICT`（blocking） | `python _probe_w6r1_probe.py`：`R-44 冲突以 conflict_json 显式保留（不静默择一）` → **OK**，样例 `[("subject_name", "[{...委托书...},{...记录本...}]")]`；`_probe_w6r1_api_sweep.py`：check 输出含 `FIELD_CONFLICT`（阻断 6 项之一） |
| R-45 关键事实人工核对 | **满足** | `CRITICAL_FIELD_KEYS=(case_no,commission_matter,subject_name,subject_gender,subject_identity,exam_time,body_length)`；`checker.py:590` `FIELD_UNRESOLVED_CRITICAL` blocking；OCR 分数仅排序 | `_probe_w6r1_probe.py`：open blocking 6 条中含 4×`FIELD_UNRESOLVED_CRITICAL`；`OCR_LOW_CONFIDENCE` 为 warning（非结论） |
| R-46 校核信息与正文分开 | **不满足** | `docgen.py:1757`：`draft_marker = "" if doc_status=="approved" else DRAFT_MARKER` → `pending_issue` 也写【待核对】；`checker.py:1199-1212`：`pending_issue` 正文含 DRAFT_MARKER → **blocking** `DOCUMENT_DRAFT_MARKER`。两者互斥，构成自相矛盾；架构 §3.2 又要求 `internal_draft→pending_issue` 需“无待核对标记” | `python _probe_w6r1_probe3.py`（先豁免全部 blocking 以放行 pending_issue）：`R-46 待签发稿正文已剔除草稿标记【待核对】` → **FAIL（出现 1 次）**；随后 check → `DOCUMENT_DRAFT_MARKER` 命中 1 条，detail = `文稿 report v1（id=1）状态为“待签发稿”，正文仍出现 1 处草稿标记“【待核对】”…；进入待签发稿前必须…` → **D4** |
| R-47 案件独立 | **满足** | 见 N-10 | 同 N-10 |
| R-48 案件编号与版本追溯 | **满足** | `cases.case_no/version`、`documents.template_version/term_rule_version/template_path/render_engine/version_no`、`case_events` | `_probe_w6r1_probe10.py`：文稿记录含全部追溯列（`template_version='report-template-v1'`、`term_rule_version='term-rules-v1'`、`template_path=.../originals/template/report-template-v1.docx`）；`事件 23 条`，类型含 `case_created/material_added/fields_extracted/normalized/document_created/document_approved`，均有 `actor`/`created_at` |
| R-49 GA/T 147—2019 | **不满足** | `backend/**`、`frontend/**` **无任何 `GA/T` 或标准版本配置**；`/api/config` 无标准字段 | `Select-String -Pattern 'GA/T'` → 仅 `docs/requirements.md:178`；`python -c "import backend.app.constants as C; print([k for k in dir(C) if 'STANDARD' in k.upper()])"` → `[]` → **D10** |
| R-50 独立模板/栏目 | **满足** | 模板按栏目输出；`doc_type` 可扩展 | `_probe_w6r1_probe9.py`：模板含 6 个必需栏目；`_probe_w6r1_probe10.py`：`doc_type='report'` 独立文件按 `案件编号-report-vN` 命名 |
| R-51 文件命名 | **满足** | `docgen.document_filename` → `f"{case_no}-{doc_type}-v{version_no}.{fmt}"` | `_probe_w6r1_probe4.py`：`FY-2026-0001-report-v1.docx`；`_probe_w6r1_probe10.py`：交付 v2 → `FY-2026-0001-report-v2.docx` |
| R-52 六项验收分开检查 | **部分满足** | 识别质量→`recognition` 类；内容质量→`content` 类；来源完整性→`source` 类 + R-15；格式质量→`format` 类；修改可靠性→`correction` 类；**实际效率无任何度量入口** | `_probe_w6r1_probe9.py`：`/api/stats` 无任何耗时/效率键 → 符合 R-53“不承诺节省时间”；前五项有可复现入口 |
| R-53 真实样本建立正确答案 | **无法验证** | 只有演示案件与程序化模板；`/api/config` 标注 `demo_data: true` | `_probe_w6r1_probe4.py`：`demo_data=True`；`TODO-DL-6` 未解除 |
| **R-54** 照片上传+位置指派 | **部分满足** | 位置目录 `constants.PHOTO_POSITION_DEFINITIONS`（18 项）、`PATCH .../placement`（`schemas.py:194-210` 校验）、`rounds.py:600-608`；**上传入口因 D2 不可用** | `_probe_w6r1_probe4.py`：`GET /api/config` → `photo_positions` 18 项、形状 `{key,label,section,label,order,anchor,wide,max_width_cm}`；非法 `position_key` → **422**；对非照片材料指派 → **400** `只有部位照片（kind=photo）可以指定目标位置，当前类型：commission`；但 `POST /materials` → 422（**D2**）致上传链路不可用 |
| **R-55** 照片永不 OCR | **满足（含 D8 说明）** | `constants.py:127`、`workflow.py:256-259`、`checker.py` `PHOTO_CONTENT_READ`(blocking)、`docs/architecture.md:713-714` | `python _probe_w6r1_probe.py`：`skipped_photo_ids=[7,4,5,6]` 覆盖全部照片；`照片材料 ocr_blocks == 0（直接查 SQLite）` → **OK**；`_probe_w6r1_probe4.py`：照片无 derived 目录、不作为提取来源；`PHOTO_CONTENT_READ` 规则存在（`checker.py:621-670`） |
| **R-56** 照片按栏位插入+宽度上限 | **满足** | `docgen.py:957-1038`（居中、`min(栏位上限, 原宽)`、保持纵横比）、`constants.PHOTO_MAX_WIDTH_CM=7.0`、`PHOTO_WIDE_MAX_WIDTH_CM=11.0` | `python _probe_w6r1_probe4.py`：`inline_shapes == 已指派且文件存在的照片数`（3==3）；`每张图宽度(cm)：[11.0, 7.0, 7.0]`（宽幅 11 / 普通 7）；`图片保持原件纵横比 doc/px 比例=[(1.333,1.333)]×3，偏差=[]`；`未指派位置的照片不进入文稿` → OK |
| **R-57** 未指派照片不入文稿+warning | **满足** | `database.list_photo_slots`（未指派不出现在任何栏位）、`checker.py` `PHOTO_POSITION_MISSING`(warning) | `python -m backend.scripts.smoke`：`[warning/open] PHOTO_POSITION_MISSING`；`R-57 未指派照片不出现在任何栏位：未指派 1 张`；`_probe_w6r1_probe4.py`：`missing_count=15` 且 `inline_shapes=3`（未指派未插图） |
| **R-58** 前端照片位置指派入口 | **部分满足** | `frontend/static/workbench.js:1370-1394`（照片区块 + 上传 + 位置下拉 + 刷新栏位视图）、`:2554-2614`（`photo-upload`/`photo-slots-reload`/顺序调整）、`:1306-1314`（缩略图 + 位置下拉）；`frontend/src/components/PhotoPanel.vue`（Vue，未经构建验证） | `python _probe_w6r1_probe4.py`：`/api/config.photo_positions` 18 项供下拉使用；`Select-String` 命中 `系统不读取照片内容` 标注（`workbench.js:1371`、`index.html:57`）；`Invoke-WebRequest /` 含「系统不读取照片内容」；Vue 侧**仅源码核对**（TODO-DL-3） |

### 2.3 契约 §4 端点可用性扫描（`python _probe_w6r1_api_sweep.py`）

43 次调用，其中 **9 次 4xx/5xx**：3 次属探针自身误用（`match_type` 传了非法枚举；`stage` 误用 POST；
`corrections.target_id=0`），1 次属**正确行为**（`pending_issue` 被 409 阻断 = R-28 生效），
1 次属合理拒绝（`/api/ai/verify` 无密钥 → 400），其余为真实缺陷：

| 端点 | 状态码 | 判定 |
| --- | --- | --- |
| `POST /api/cases/{id}/extract` | **409**（每次、恒定） | **缺陷 D1** |
| `POST /api/cases/{id}/materials` | **422** | **缺陷 D2** |
| `DELETE /api/cases/{id}` | 409 | 由 D2 导致（材料登记失败未递增版本，探针取到的版本已过期）——非独立缺陷，未计入 |

---

## 3. 缺陷列表

> 严重度：`blocking` 阻断交付；`major` 影响需求符合性或可验证性；`minor` 措辞/死代码/一致性问题。
> “所有者”按 `docs/architecture.md` 第 6 节与 `docs/IMPLEMENTATION_PLAN.md` §2.1。

### D1 blocking —— `POST /api/cases/{id}/extract` 恒定 409，提取阶段经 API 完全不可达；R-2/R-3/R-15/R-16/R-22 因此无法经接口验证

- **位置**：`backend/app/workflow.py:352`（入口 `database.claim(case_id, expected_version, actor)`）
  与 `backend/app/workflow.py:442-443`（`database.upsert_fields(case_id, fields, replace_existing, expected_version, actor)`）；
  根因在 `backend/app/database.py:643-664`：`_claim` **无论 `expected_version` 是否为 0 都会 `version=version+1`**。
- **复现**：
  ```
  python _probe_w6r1_extract_bug.py
  ```
  输出：
  ```
  初始 cases.version = 12
  第 1 次 POST /extract：expected_version=13 → HTTP 409 {"detail":"案件已被其他操作更新，请刷新后重试"}
         调用后 version = 14（version 被 claim 递增：True）
  第 2 次 POST /extract：expected_version=14 → HTTP 409 ...
  第 3 次 POST /extract：expected_version=15 → HTTP 409 ...
  raw 库中 case_fields 行数 = 8 | case_injuries 行数 = 0
  --- 对照：直接调用 workflow.run_extraction(expected_version=0) ---
  ok = True | detail = 提取字段 11 项、损伤 2 条（来源材料 2 个）；跨材料冲突 1 处；未启用模型提取（默认关闭）
  ```
  独立佐证：`backend/tests/test_terminology.py::test_named_rule_saved_to_db_is_applied_by_normalize_endpoint`
  与 `backend/tests/helpers.py:213` 因同一原因失败（`AssertionError: {"detail":"案件已被其他操作更新，请刷新后重试"}`）。
- **影响**：领域逻辑本身正确（旁路实测 11 字段/2 损伤/1 冲突），但**前端与 HTTP 用户永远无法执行提取**；
  R-2/R-3/R-15/R-16/R-22 在接口层不成立；`python -m backend.scripts.smoke` 之所以“全绿”，
  是因为 `backend/scripts/smoke.py:171/196/210/250/311/335` **全部传 `expected_version=0`**，
  恰好绕过该校验，掩盖了本缺陷。
- **建议最小修复**：`run_extraction` 入口的 `database.claim(...)` 与随后的 `upsert_fields(...)` 只允许其一
  持有 `expected_version`——把 `upsert_fields` 的 `expected_version` 改为 `0`（与同函数第 444 行
  `upsert_injuries(..., 0, ...)` 及注释“第二次是同一阶段的系统内部写，传 0”保持一致），
  或删除入口的 `claim` 并保留 `upsert_fields` 的版本校验。
- **所有者**：**W1**（`backend/app/workflow.py`；如判定 `_claim` 语义需改，则由 **主 Agent** 在契约中澄清）。

### D2 blocking —— `POST /api/cases/{id}/materials` 恒定 422，任何材料都无法经 API 登记（R-12/R-13/R-54 上传链路不可用）

- **位置**：`backend/app/routes.py:536-559`。第 546 行 `version = raw.pop("expected_version", expected_version)`
  先把 `expected_version` 从请求体移除，第 547 行再用 `_payload(MaterialCreate, raw)` 校验；
  而 `backend/app/schemas.py:158` 的 `MaterialCreate` **已声明** `expected_version: int = Field(ge=1)`（必填），
  故校验必然失败。第 541-542 行注释「`schemas.MaterialCreate` 未声明 `expected_version`」是**过时假设**。
- **复现**：
  ```
  python _probe_w6r1_probe2.py
  ```
  输出：
  ```
  [FAIL] 材料登记（expected_version 放请求体）成功  —— status=422 body={"detail":"expected_version Field required"}
  [FAIL] 材料登记（expected_version 放查询参数）成功  —— status=422 body={"detail":"expected_version Field required"}
  ```
- **影响**：前端「选择材料」第一步（上传委托书/记录本/模板/部位照片）在接口层完全不可用；
  走 `database.add_material` 直连的 `seed_demo_case` 不受影响，所以演示与 smoke 掩盖了该缺陷。
  R-54 的“上传”半边、R-12、R-13 因此不成立。
- **建议最小修复**：`routes.py:547` 改为先校验再取版本，即
  `data = _payload(MaterialCreate, dict(payload or {}))`、`version = int(data.pop("expected_version"))`，
  并按第 552-556 行保留 `version < 1` 的 400 分支。
- **所有者**：**W1**（`backend/app/routes.py`）。若确认 `MaterialCreate` 不应含 `expected_version`，
  则该契约变更属 **主 Agent**（`schemas.py` 冻结）。

### D3 blocking —— 跨段 `{% for %}` 循环渲染为**整体倒序**，损伤顺序被颠倒（R-3 不满足）

- **位置（已定位到行）**：`backend/app/docgen.py:692`
  `anchor = paragraphs[end_para]._p` —— 该语句位于 `_emit_branch()` **内部**，
  而 `_emit_branch()` 由 `backend/app/docgen.py:738` 在 `_process_for()` 的循环里**逐项各调用一次**。
  每次调用都把锚点重新解析回 `{% endfor %}` 那一段，于是第 N 项的副本总是被插到该段**紧后面**，
  把前一项挤到更后面 → 最终呈现**整体倒序**。
  （对照：`backend/app/docgen.py:693-697` 在**单次** `_emit_branch` 内部用 `anchor = new_element`
  正确串联，因此块内多段的相对顺序是对的——问题只在“跨项”的锚点被重置。）
  数据侧排序本身正确：`backend/app/docgen.py:1735`（`injuries.sort(key=lambda item: (item["seq"], item["text"]))`）。
- **复现**：
  ```
  python _probe_w6r1_probe7.py
  ```
  输出：
  ```
  API 返回顺序: [(1, '前臂'), (2, '小腿'), (3, '额部')]
  build_context 顺序: [(1, '左前臂见擦伤，范围为3 '), (2, '右小腿见挫伤，范围为4 '), (3, '额部表皮剥脱1×1cm')]

  生成文稿中损伤行（按文档顺序）:
      3. 额部表皮剥脱1×1cm（明确记载）
      2. 右小腿见挫伤，范围为4 cm × 3 cm。（明确记载）
      1. 左前臂见擦伤，范围为3 cm × 2 cm。（明确记载）
  ```
  同一现象在 `_probe_w6r1_probe5.py`（2 条损伤）与 `_probe_w6r1_probe6.py` 复现。
- **影响**：R-3「保留原有顺序和对应关系」在**交付文稿**中不成立；模板中 `injuries` 之外的
  `{% for %}` 块（`general_findings`、`checklist_open`、`photos`、`materials` 等）很可能同样受影响
  —— 交付物中所有循环栏目的顺序都需重验。
- **建议最小修复**：让 `_emit_branch()` 接收/返回锚点并在 `_process_for()` 的逐项循环中串联，
  即把 `backend/app/docgen.py:692` 的 `anchor = paragraphs[end_para]._p`
  改为只在进入循环前解析一次、此后以“上一次插入的最后一个元素”为锚
  （`_emit_branch` 返回最后插入的 element，`_process_for` 用它作为下一项的锚）；
  并补一条“3 条损伤按 seq 升序出现在正文”的回归断言。
- **所有者**：**W3**（`backend/app/docgen.py`）。

### D4 blocking —— `pending_issue` 文稿必然携带【待核对】并被 `DOCUMENT_DRAFT_MARKER` 判为阻断，R-46 自相矛盾

- **位置**：`backend/app/docgen.py:1757`
  `draft_marker = "" if _clean(data.get("doc_status")) == "approved" else C.DRAFT_MARKER`；
  模板锚点 `backend/app/docgen.py:1363`（`{{ draft_marker }}`）；
  判定 `backend/app/checker.py:1199-1212`（`doc_status == "pending_issue"` 且正文含 `DRAFT_MARKER` → blocking）。
  架构侧 `docs/architecture.md:150` 要求 `internal_draft → pending_issue` 需“无待核对标记”，
  `docs/requirements.md:175`（R-46）要求“草稿中的待核对标记须在进入待签发稿前处理”。
- **复现**：
  ```
  python _probe_w6r1_probe3.py
  ```
  输出（先豁免全部 blocking 以放行 pending_issue）：
  ```
  [OK  ] 存在 open blocking 时 pending_issue 被 409 阻断（R-28）
       已豁免 6 条 blocking
  [OK  ] 阻断项清空后 pending_issue 可生成
  [FAIL] R-46 待签发稿正文已剔除草稿标记【待核对】  —— 【待核对】出现 1 次（doc_status=pending_issue）
  [FAIL] checker 对刚生成的 pending_issue 不产出 DOCUMENT_DRAFT_MARKER（blocking）
         —— 命中 1 条：文稿 report v1（id=1）状态为“待签发稿”，正文仍出现 1 处草稿标记“【待核对】”…
  ```
- **影响**：只要生成 `pending_issue`，该文稿立刻带一条 blocking 校核项；由于 D1/R-28 只拦“新生成”，
  用户唯一出路是豁免或改回 `internal_draft`。R-46 在实现层不成立；「进入待签发稿前必须处理待核对标记」
  没有任何代码路径可完成。
- **建议最小修复**：把 `draft_marker` 的判定从“非 approved”收窄为“仅 `internal_draft`”
  （即 `"" if doc_status in ("pending_issue", "approved") else C.DRAFT_MARKER`），
  并在渲染 `pending_issue` 前要求 `checklist` 无 open 的“待核对类”提示。改动落在 **W3**
  （`docgen.build_context`）；若判定需 `run_render` 侧配合，则由 **W1** 调整
  `backend/app/workflow.py:556-594` 的上下文注入。
- **所有者**：**W3**（`backend/app/docgen.py`），与 **W1** 协同确认 `pending_issue` 的渲染语义。

### D5 major —— `internal_draft → approved → deliver` 绕过 R-28 阻断门：带【待核对】与 6 条 open blocking 的文稿可被审核并交付

- **位置**：`backend/app/database.py`（`approve_document`，只改 `doc_status`，不重新渲染、不检查 open blocking）；
  `backend/app/workflow.py`（`run_delivery` 仅校验 `doc_status == "approved"`）。
- **复现**：
  ```
  python _probe_w6r1_probe2.py
  ```
  输出：
  ```
  internal_draft 正文【待核对】出现 1 次
  当前 open blocking 项 6 条
  [OK  ] 存在 open blocking 项时仍可 approve internal_draft（状态机允许）  —— status=200 …, "doc_status":"approved"
  [FAIL] R-46 approve 后正文仍含【待核对】草稿标记（未在进入签发稿前处理）  —— 【待核对】出现 1 次；doc_status=approved
  [FAIL] INV-8/R-41 存在 open blocking 项时 deliver approved 文稿是否被拒绝  —— status=200
         body={"stage":"delivered","ok":true,"detail":"交付已保存：文稿 v1，格式 docx", …}
  ```
- **影响**：R-28 只保护 `pending_issue`；但由于架构允许直接从 `internal_draft` 审核，
  一条含 6 个未解决阻断项、且正文仍印有【待核对】的文稿可被标记 `approved` 并交付，
  “阻止该版本进入待签发稿”被等价绕过；R-41“新版本重新进入相应复核流程”在 `approved` 之后无拦截点。
- **建议最小修复**：`approve_document` 在置为 `approved` 前调用 `open_blocking_items(case_id)`，
  非空则该文稿不得置 `approved`（返回 409 并说明哪些阻断项未解决）；或在 `run_delivery`
  增加“无 open blocking + 正文无 DRAFT_MARKER”前置校验。
- **所有者**：**W1**（`backend/app/database.py`、`backend/app/workflow.py`）。

### D6 major —— 模板声明了栏目但该字段无行时，正文渲染为**空白**而非“未记录”（N-5 / R-35 缺口）

- **位置**：`backend/app/docgen.py:1470-1485`（`_scalar`：字段行无值 → `case_data` 顶层无值 → 返回 `default=""`），
  与 `backend/app/docgen.py:1419-1431`（`_field_value` 只在**有行**时保证 not_recorded → “未记录”）。
  相关设计注记：`docs/architecture.md:488-489`（`extract_fields` 不产生“空字段行”）。
- **复现**：
  ```
  python _probe_w6r1_probe6.py
  ```
  输出：
  ```
  无字段行、渲染为空的标量锚点 4 个：
     body_temperature -> ''
     body_weight -> ''
     cornea -> ''
     examiners -> ''
  ```
  `python _probe_w6r1_probe5.py` 生成文稿中对应段落：
  ```
    6 | 体重：
   10 | 尸温：
   11 | 角膜：
  ```
- **影响**：同一份文稿中，`衣着`（有行、status=not_recorded）显示“未记录”，
  而 `体重/尸温/角膜/检验人员`（无行）显示空白——同一个“栏目空白”在一份文书里出现两种表现，
  直接违反 `docs/requirements.md:23`（N-5）与 `:140`（R-35）“栏目空白必须标记为‘未记录’”。
  现有校验（`FIELD_REQUIRED_EMPTY`）只覆盖 8 个必填键，`body_weight/body_temperature/cornea/examiners`
  均不在其中，因此不会报警。
- **建议最小修复**：`_scalar` 对 `key in C.FIELD_INDEX` 且三处取值皆空时返回 `"未记录"`
  （`opinion`、`draft_marker`、`signature_block` 等非字段锚点保持原样）；并为
  “模板声明的每个字段锚点在有/无字段行两种情况下都不得渲染为空串”补断言。
- **所有者**：**W3**（`backend/app/docgen.py`）。

### D7 major —— 演示模板只有 14 个照片锚点，而位置目录有 18 项；`GET /photo-slots` 口径与交付文稿不一致

- **位置**：`backend/app/docgen.py::build_demo_template`（生成 14 个 `{{ photo_* }}` 锚点）；
  `backend/app/seed.py:208-232`（`_augment_template_with_photo_slots` 在检测到已有 `{{` + `photo_` 时
  直接 `return False`，不再补齐）；`backend/app/constants.py`（`PHOTO_POSITION_DEFINITIONS` 18 项）；
  `backend/app/database.py::list_photo_slots`（按 18 项目录产出栏位）。
- **复现**：
  ```
  python _probe_w6r1_probe5.py
  ```
  输出：
  ```
  模板中的照片锚点 14 个: photo_body_overall photo_head photo_face photo_neck photo_chest
                          photo_abdomen photo_back photo_arm_left photo_arm_right
                          photo_leg_left photo_leg_right photo_foot_left photo_foot_right photo_other
  位置目录 18 个 key
  目录中有、模板中无锚点的位置（4 个）: ['waist_hip', 'perineum', 'hand_left', 'hand_right']
  photo-slots 栏位 18 个；missing=15 assigned=3
  ```
  对应地 `python _probe_w6r1_probe4.py` 的
  `[FAIL] 空栏位写入“（未提供照片）”且数量匹配（N-12）  —— 占位 11 次 / 空栏位 15 个`。
- **影响**：① `GET /api/cases/{id}/photo-slots` 上报 `missing_count=15`、`assigned_count=3`，
  但交付文稿只有 14 个栏位、11 处占位文字，前端据此显示的「栏位 18 个」在文稿中并不存在；
  ② `checker` 的 `PHOTO_SLOT_EMPTY`（info）只按模板实际锚点产出（11 条），与 `missing_count` 不闭合；
  ③ R-54/R-56/R-58 在演示数据上无法覆盖 `腰臀/会阴/左手/右手` 四个位置，削减了可验证范围。
- **建议最小修复**：让 `build_demo_template` 直接按 `PHOTO_POSITION_DEFINITIONS` 生成全部 18 个锚点
  （或把 `_augment_template_with_photo_slots` 的存在性判定从“是否已有任意照片锚点”改为“是否缺某个 key，缺则补”），
  并在 `run_render`/`checker` 之间统一“栏位集合”的单一来源。
- **所有者**：**W3**（`backend/app/docgen.py`）与 **W1**（`backend/app/seed.py`）协同。

### D8 major —— 默认 OCR 引擎缺 N-11 内核兜底：`SidecarOcrEngine.recognize()` 不调用 `assert_ocr_kind`，与 `ocr/base.py` 的声明不符

- **位置**：`backend/app/ocr/sidecar.py:94-146`（无 `assert_ocr_kind(kind)` 调用）；
  对照 `backend/app/ocr/paddle.py:84`（有）；声明见 `backend/app/ocr/base.py:52-64`
  「正常流程由 `workflow.run_ocr` 过滤，这里是**内核层的兜底**——一旦被误传就立刻失败」。
- **复现**：
  ```
  python _probe_w6r1_extract_bug.py
  ```
  输出：
  ```
  get_engine('paddle').recognize(kind='photo') → OcrEngineUnavailable: 部位照片不进行任何识别（N-11）…
  SidecarOcrEngine.recognize(kind='photo') → 未抛异常（默认引擎无内核兜底）
  ```
- **影响**：端到端红线目前仍守住（`workflow.run_ocr:256-259`、`run_preprocess:165-171`、
  `run_extraction:359-360` 三处过滤 + `PHOTO_CONTENT_READ` 兜底 + 实测照片 `ocr_blocks=0`），
  但“内核层兜底”只存在于**当前不可用**的 paddle 引擎；一旦有任何调用方漏掉
  `OCR_MATERIAL_KINDS` 过滤（例如未来新增端点、批处理脚本、第三方集成），
  **默认引擎会静默按照片同名的 `.ocr.json` 生成 `ocr_blocks`**，而非立刻失败。
- **建议最小修复**：在 `SidecarOcrEngine.recognize()` 第一行加 `assert_ocr_kind(kind)`
  （与 paddle 引擎一致）。
- **所有者**：**W2**（`backend/app/ocr/sidecar.py`）。

### D9 major —— 测试套件在评审期间不稳定：一次收集 SyntaxError + 4 条失败用例

- **位置**：`backend/tests/test_api_pipeline.py`（21:21:17 / 21:21:59 两次写入；
  曾出现 `from backend.app.docgen import detect_libreoffice, docgen_engine_status = None  # noqa: E999` → `SyntaxError`）；
  失败用例见下。
- **复现**：
  ```
  python -m pytest -o addopts="" --no-header -q --tb=no
  ```
  输出（最终快照）：
  ```
  FAILED backend/tests/test_api_pipeline.py::test_r5_r6_full_pipeline_from_materials_to_docx_and_checklist
  FAILED backend/tests/test_api_pipeline.py::test_r21_low_confidence_block_enters_the_checklist_as_warning
  FAILED backend/tests/test_api_pipeline.py::test_n11_run_ocr_skips_photos_and_photos_never_get_ocr_blocks
  FAILED backend/tests/test_terminology.py::test_named_rule_saved_to_db_is_applied_by_normalize_endpoint
  4 failed, 128 passed, 5 xfailed, 1 warning in 9.99s
  ```
  失败原因（`--tb=line`）：
  - 3 条 `test_api_pipeline` 失败：`KeyError: 'OCR_LOW_CONFIDENCE'`（`:392`）、
    `AssertionError: assert '某市公安局某分局' in '<正文含 委托单位 某测试单位 …>'`（`:202`）、
    以及 `helpers.py:213 AssertionError: {"detail":"案件已被其他操作更新，请刷新后重试"}`（= D1）；
  - `test_terminology.py::test_named_rule_...` 同样因 D1 失败。
- **影响**：`docs/IMPLEMENTATION_PLAN.md` §4.1 第 2 条与 `docs/architecture.md:925` 的完成定义第 1 条
  （`python -m pytest` 全绿）**不成立**；`test_api_pipeline.py:202` 的断言还揭示了
  §3 中的 **D11**（`cases` 列与 `case_fields.confirmed_value` 的取值优先级问题）。
- **建议最小修复**：修 D1 后重跑；`test_api_pipeline.py` 的语法损坏与 `KeyError` 需由测试作者修正；
  `:202` 的期望值需与 D11 的结论对齐后再定稿。
- **所有者**：**W5**（`backend/tests/**`）；D1 修好后 3 条失败应转为通过。

### D10 major —— R-49 无任何实现：`GA/T 147—2019` 既未记录也未进入可配置的标准/版本项

- **位置**：`backend/app/constants.py`（无标准相关常量）；`GET /api/config` 响应无标准字段；
  全仓仅 `docs/requirements.md:178` 提到。
- **复现**：
  ```
  Select-String -Path backend\**\*.py, frontend\**\*.* -Pattern 'GA/T'
  python -c "import backend.app.constants as C; print([k for k in dir(C) if 'GA' in k.upper() or 'STANDARD' in k.upper()])"
  ```
  输出：`(backend/frontend 中无 GA/T 命中)`；常量查询返回 `[]`
  （`_probe_w6r1_probe8.py` 的 `R-49` 检查仅因 `NEGATION_WORDS` 含 "GA" 子串而误判为命中，属探针缺陷，已人工排除）。
- **影响**：R-49 的“可核对 GA/T 147—2019”“由用户确认后写入规则配置”两项均无落地；
  专业人员无法在系统内记录本次采用的标准与版本，削弱 R-42/R-48 的可追溯性。
- **建议最小修复**：在 `constants.py` 增加 `TECHNICAL_STANDARDS`（编号/名称/版本/是否已由用户确认），
  在 `/api/config` 暴露，并在文稿「检验方法」栏输出“按 GA/T 147—2019（版本由用户确认）”——
  属契约扩展，须由 **主 Agent** 落 `constants.py` + `routes.py:315-431` 的 config 形状并同步 `docs/architecture.md` §4。
- **所有者**：**主 Agent**（契约与 `docs`）；如需渲染侧改动则一并派 **W3**。

### D11 major —— `cases` 列与 `case_fields.confirmed_value` 的最初值会永久压过材料识别值

- **位置**：`backend/app/database.py:826-858`（`_sync_case_fields` 把案件列同时写入
  `raw_text/confirmed_value/normalized_text`，第 847-849、852-857 行）；
  `backend/app/database.py:1731-1743`（`upsert_fields` 更新已存在字段行时
  **显式排除 `confirmed_value`**：`if column not in ("confirmed_value",)`）；
  `backend/app/docgen.py:1419-1431`（`_field_value` 优先级 `confirmed_value → normalized_text → raw_text`）。
- **复现**：`backend/tests/test_api_pipeline.py::test_r5_r6_full_pipeline_from_materials_to_docx_and_checklist`
  的实际断言输出（`python -m pytest -o addopts="" -q --tb=line`）：
  ```
  E   AssertionError: assert '某市公安局某分局' in '尸表检验记录\n…\n案件编号\nFY-2026-0001\n委托单位\n某测试单位
      \n委托事项\n对测试对象进行尸表检验\n委托日期\n未记录\n姓名\n李…'
  ```
  即：案件由 API 以 `commission_unit='某测试单位'` 建立（`_sync_case_fields` 写入 `confirmed_value`），
  随后材料 OCR/提取得到 `委托单位：某市公安局某分局`，但 `upsert_fields` **只更新 raw/normalized、
  不覆盖 `confirmed_value`**，而文书取值优先 `confirmed_value` → 正文显示建案时的旧值，
  同时 `confirmed_value`（旧）与 `raw_text`（材料）不一致。
  我的独立探针 `_probe_w6r1_probe11.py` 证实另一侧：`PATCH /api/cases/{id}` 改 `commission_unit` 时
  `_sync_case_fields` 会同步写 `confirmed_value`，因此正文能跟着变——两条路径行为不一致。
- **影响**：若用户在“建案/修改案件信息”时先填了委托单位或姓名，之后材料识别出的值会被静默忽略，
  且字段行的 `confirmed_value` 与 `raw_text` 互相矛盾（R-15 来源一致性、R-45 关键事实核对、R-2 提取正确性）。
- **建议最小修复**：二者择一并写入契约——① `_sync_case_fields` 只写 `raw_text/normalized_text`，
  不写 `confirmed_value`（把“确认值”留给识别结果或用户显式确认）；
  或 ② `upsert_fields` 在 `review_state != 'confirmed'` 时也更新 `confirmed_value`。
  改动在 **W1**；若涉及 `confirmed_value` 语义澄清，需 **主 Agent** 更新 `docs/architecture.md` §5.2/§3.3。
- **所有者**：**W1**（`backend/app/database.py`），契约澄清由 **主 Agent**。

### D12 minor —— `run_render` 在 `_claim` 之前做阻断检查，过期版本时报 `BlockedByChecklist` 而非 `VersionConflict`

- **位置**：`backend/app/workflow.py:558-565`（阻塞检查）先于 `database.create_document(...)`（内含 `_claim`）。
- **影响**：两个 409 语义混淆（违反 INV-9“任何写操作先 `_claim`”的措辞）；并发场景下用户会收到
  “阻断项未解决”而实际是版本过期，误导刷新决策。
- **建议最小修复**：把 `database.claim(case_id, expected_version, actor)` 提到阻塞检查之前。
- **所有者**：**W1**（`backend/app/workflow.py`）。

### D13 minor —— `database.py:1678-1681` 死分支

- **位置**：`backend/app/database.py:1678-1681`
  ```
  if status == "not_recorded":
      normalized = "未记录"
  elif "未见异常" in normalized and status == "not_recorded":
      normalized = "未记录"
  ```
  第二个 `elif` 的条件永远为假（`status` 已由前一支处理）。
- **影响**：无功能影响（第一个分支已保证 INV-3），但会被误读为“存在第二条纠正路径”。
- **建议最小修复**：删除该 `elif`，保留注释说明 INV-3 由前一支保证。
- **所有者**：**W1**（`backend/app/database.py`）。

### D14 minor —— 前端两处声称 `CaseUpdate` 未声明 `opinion_text`，与实际契约不符并误导用户

- **位置**：`frontend/static/workbench.js:2410-2413`、`frontend/src/components/CaseWorkspace.vue:166、177-188`
  （提示“该栏目的手工输入目前无法落库”“契约缺口，已报告主 Agent”）；
  实际 `backend/app/schemas.py:128-132` **已声明** `CaseUpdate.opinion_text`，
  `backend/app/database.py:1007-1020` 也已支持更新。
- **复现**：
  ```
  python _probe_w6r1_probe.py
  ```
  输出：`[OK  ] PATCH 只发 opinion_text 返回 200`、`[OK  ] opinion_text 原样落库`。
  即写入**是成功的**，界面却提示“无法落库”。
- **影响**：N-1 的“由专业人员提供”栏目在真实使用中会被用户误认为不可保存而放弃填写；
  也污染了交付说明的“契约问题”记录。
- **建议最小修复**：删除该提示分支，改为正常成功提示（保留“系统不生成、只手工输入”的说明）。
- **所有者**：**W4b**（`frontend/static/workbench.js`）与 **W4**（`frontend/src/components/CaseWorkspace.vue`）。

### D15 minor —— 正文标签与归一化文本重复，产生“尸长：尸长172 cm。”这类叠字

- **位置**：模板栏目「尸长：{{ body_length }}」+ `terminology.py` 的输出本身已含“尸长”；
  `_probe_w6r1_probe5.py` 文稿第 5 段 = `尸长：尸长172 cm。`、第 12 段 = `其他一般所见：其他一般所见：未见皮下出血`。
- **影响**：文书文字重复，影响 R-4/R-52 的“格式质量”，不触发任何现有校核项。
- **建议最小修复**：由 **W3** 在 `docgen` 渲染字段值时去掉与栏目 `label` 重复的前缀
  （或在 `terminology.py` 侧避免回填栏目名），并补一条“正文不出现 `label + '：' + label`”的断言（**W5**）。
- **所有者**：**W3**（`backend/app/docgen.py`），如需改规则模板则由 **W2**（`backend/app/terminology.py`）调整。

### D16 minor —— `CorrectionCreate.target_id` 允许 0，落到 404 而非 422

- **位置**：`backend/app/schemas.py`（`CorrectionCreate.target_id` 未设 `ge=1`，与 `InjuryCreate.source_material_id: Optional[int] = Field(default=None, ge=1)` 风格不一致）。
- **复现**：`python _probe_w6r1_api_sweep.py`：
  `POST /api/cases/{id}/corrections`（`target_id=0`）→ `404 {"detail":"字段不存在：id=0"}`。
- **影响**：参数级错误被映射为“资源不存在”，前端难以区分“填错”与“已被删除”。
- **建议最小修复**：`CorrectionCreate.target_id: int = Field(ge=1)`（契约变更 → 主 Agent），
  或路由层在 `target_id <= 0` 时返回 400。
- **所有者**：**主 Agent**（`schemas.py` 冻结）或 **W1**（`routes.py` 参数前置校验）。

### 缺陷统计

| 严重度 | 数量 | 编号 |
| --- | --- | --- |
| **blocking** | **4** | D1（/extract 恒定 409）、D2（/materials 恒定 422）、D3（损伤顺序倒序）、D4（pending_issue 必带【待核对】） |
| **major** | **7** | D5（approve/deliver 绕过 R-28）、D6（无行栏位空白）、D7（14 锚点 vs 18 位置）、D8（sidecar 无 N-11 兜底）、D9（测试不稳定 4 failed）、D10（R-49 无实现）、D11（confirmed_value 压过识别值） |
| **minor** | **5** | D12（claim 顺序）、D13（死分支）、D14（前端误报 opinion 不可落库）、D15（标签叠字）、D16（target_id=0 → 404） |
| 合计 | **16** | |

---

## 4. 无法验证项与原因

| 项 | 编号 | 原因（本机实测） |
| --- | --- | --- |
| 真实手写/印刷 OCR 识别质量、识别率 | R-20/R-21/R-53、TODO-DL-2 | `paddleocr` 未安装：`ocr.engine_status()` 中 `paddle available=False`；`resolve_engine('paddle')` 回退 `sidecar`。离线 `sidecar` 只读预置 JSON，不产生真实识别能力，**不能据此评估识别质量** |
| 真实样本与已审核文稿的对应关系 | R-53、TODO-DL-6 | 只有 `seed_demo_case` 的程序化演示案件；`/api/config` 标注 `demo_data: true`。没有真实样本即无法建立“正确答案”，故按文档口径**不承诺识别率或节省时间** |
| 真实部位照片的插入效果与排版观感 | R-54～R-58、TODO-DL-7 | 演示案件使用程序化纯色占位 PNG（`backend/app/seed.py:104-119`）。我用几何方式（`inline_shapes` 宽度、与原件像素纵横比比对）验证了插入与缩放规则，但**未查看任何像素内容**（N-11 要求），因此照片“看起来是否合理”不在可验证范围 |
| Vue 3 前端的构建与浏览器主流程 | R-1/R-58、TODO-DL-3 | `frontend/dist` **不存在**；`npm install` 属禁止的联网操作且 pnpm/npm 离线缓存不匹配（`docs/TODO_DOWNLOADS.md:21-28` 的实测结论）。我只做了源码逐文件核对 + 静态工作台的 HTTP 实测，Vue 侧“未经构建验证” |
| `docxtpl` 渲染路径 | R-24、TODO-DL-1 | `docxtpl_available=False`，实际走 `builtin`；`engine="docxtpl"` 分支未被覆盖 |
| `pypdfium2` PDF 渲染路径 | R-18、TODO-DL-4 | 未安装，实际走 `PyMuPDF(fitz)`；`render_pdf_pages` 的 pypdfium2 分支未被覆盖 |
| 云端 OCR（Azure Document Intelligence 等）对比选型 | TODO-DL-5 | 未接入，仅有 `register_engine()` 扩展点 |
| 真实并发下的乐观锁竞争 | INV-9 | 我用 `expected_version=999999` 验证了冲突 → 409 的单线程行为，但未做真实多进程并发压测（探针为 `TestClient` 串行） |
| 长文档/大页数下的排版溢出 | R-52 格式质量 | 演示文稿 58 段/1 表/5 页 PDF；大样本排版未验证（无样本，TODO-DL-6） |
| 测试套件的最终稳定性 | §10.1 | `backend/tests/**` 在本评审期间被 **W5 并发写入**（21:15→21:21 共 8 个文件）。我的快照为 `4 failed, 128 passed, 5 xfailed`，但在 W5 收尾前无法给出最终判定 |

---

## 5. 结论

**结论：不可交付（Not Deliverable）。**

判定依据（均为实测）：

1. **两条核心 API 通路永久不可用**：
   `POST /api/cases/{id}/extract` → **恒定 409**（D1）、`POST /api/cases/{id}/materials` → **恒定 422**（D2）。
   这意味着「选择材料 → 识别 → 提取」这条主链路在前端/HTTP 层根本走不通，
   `docs/IMPLEMENTATION_PLAN.md` §0 的 U-1（前端可实际调用）与 R-2/R-3/R-12/R-13/R-15/R-16/R-54 均不成立。
2. **交付物本身违反两条红线/需求**：损伤在正文中**整体倒序**（D3，违反 R-3）；
   `pending_issue` 文稿**必然**携带【待核对】并被自家校核判为阻断（D4，违反 R-46 且使架构 §3.2 状态机自相矛盾）。
3. **完成定义不达标**：`python -m pytest` **非全绿**（`4 failed, 128 passed, 5 xfailed`，EXIT=1），
   `docs/architecture.md:925` 与 `docs/IMPLEMENTATION_PLAN.md` §4.1 第 2 条不满足；
   `docs/reviews/` 在本报告落盘前为空，§10.3「三份评审报告」尚缺其二。
4. **R-49 完全未实现**（D10）。

同时应明确指出：**领域逻辑质量总体良好**——N-1/N-3/N-4/N-8/N-9/N-10、
R-4～R-11、R-15～R-19、R-21～R-27、R-30～R-44、R-48、R-50、R-51、R-55～R-57 均通过实测，
且多数红线是以“构造能使其失效的场景”方式验证过的（含直接查 SQLite、几何比对、`{% for %}` 三元素倒序判定）。
因此本项目的缺口集中在**接口装配与渲染细节**，而非领域模型本身。

### 前置条件（按修复顺序，全部满足后方可重新评估为“可交付”）

| # | 前置条件 | 对应缺陷 | 所有者 |
| --- | --- | --- | --- |
| 1 | 修复 `run_extraction` 的双重 `_claim`，使 `POST /api/cases/{id}/extract` 在携带当前 `expected_version` 时返回 200 且真实写入 11 字段/2 损伤/1 冲突 | D1 | W1 |
| 2 | 修复 `POST /api/cases/{id}/materials` 的 `expected_version` 校验顺序，使材料登记（含 `kind="photo"`）返回 200 | D2 | W1（契约问题 → 主 Agent） |
| 3 | 修正跨段 `{% for %}` 渲染顺序，使 3 条损伤在正文中按 `seq` 升序出现 | D3 | W3 |
| 4 | `pending_issue` 正文剔除【待核对】，且新生成的 `pending_issue` 不再被 `DOCUMENT_DRAFT_MARKER` 判为阻断 | D4 | W3 + W1 |
| 5 | `approve_document`/`run_delivery` 在存在 open blocking 或正文含草稿标记时拒绝，堵住 R-28 绕过 | D5 | W1 |
| 6 | 模板声明但无字段行的栏目渲染为“未记录”，不再出现空白栏目 | D6 | W3 |
| 7 | 演示模板照片锚点数量与 `PHOTO_POSITION_DEFINITIONS` 一致（18/18），`/photo-slots` 口径与交付文稿闭合 | D7 | W3 + W1 |
| 8 | `SidecarOcrEngine.recognize()` 补 `assert_ocr_kind(kind)` | D8 | W2 |
| 9 | `python -m pytest` 全绿（修完 D1 后重跑；测试自身的语法与期望值问题一并修正） | D9 | W5 |
| 10 | 统一 `cases` 列与 `case_fields.confirmed_value` 的取值优先级，消除“识别值被建案值静默压制” | D11 | W1（契约澄清 → 主 Agent） |
| 11 | 补 R-49 的标准/版本配置项并同步 `docs/architecture.md` §4 契约表 | D10 | 主 Agent |
| 12 | 三份评审报告（R1/R2/R3）全部落盘，`blocking` 全部关闭或有明确豁免理由 | §10.3 | W6 + 主 Agent |

`minor` 项（D12～D16）建议同批修复，但**不构成**交付前置条件。

### 对主 Agent 的补充提示（评审过程观察到的事实）

- `backend/scripts/smoke.py` 的全部 workflow 调用都传 `expected_version=0`
  （`smoke.py:171/196/210/250/311/335`），这在契约里是“系统内部写、不校验”的合法取值，
  但它使 smoke **无法**发现 D1 这类“接口层装配缺陷”。建议 smoke 增加一段“经 HTTP 走真实
  `expected_version` 的写操作”校验，否则「全链路演练通过」会持续给出虚假绿灯。
- 我的三处探针自身误用已在本报告中标注（`match_type` 非法枚举、`stage` 误用 POST、
  `corrections.scope='terminology_rule'` 不在枚举内、`target_id=0`），
  并已用修正后的探针（`_probe_w6r1_probe9.py`）重测，结论未受其影响 —— 列出以免后续读者误判为产品缺陷。
- 本报告未修改任何产品代码；探针脚本 `_probe_w6r1_*.py` 留在项目根供复现，可随时删除。
