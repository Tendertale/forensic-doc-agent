# 子智能体提示词设计

本文件定义本项目使用的三类子智能体（**开发 / Review / 测试**）的提示词模板、分工、
文件所有权与交付格式。所有提示词都以 `docs/requirements.md` 的 `R-*`/`N-*` 编号和
`docs/architecture.md` 的接口签名为引用依据。

---

## 0. 设计原则

| 原则 | 做法 |
| --- | --- |
| **契约先行** | 主 Agent 先冻结 `constants.py` / `schemas.py` / API 契约，再并行派发。子智能体只消费契约，不改契约。 |
| **单一所有者** | 每个文件只有一个所有者（`docs/architecture.md` 第 6 节）。提示词里逐条列出「独占文件」与「只读文件」。 |
| **自包含上下文** | 子智能体看不到主对话，提示词必须自带：项目路径、必读文档、任务、验收命令、禁止事项。 |
| **可验证的交付** | 每个提示词都规定「必须实际运行什么命令」和「交付说明必须包含什么」，禁止只描述不验证。 |
| **不联网** | 所有提示词统一禁止 `pip install` / `npm install` / 任何下载；缺能力写 `TODO-DL-x`。 |
| **角色隔离** | 开发不写测试；测试不改实现；Review 只读代码、只写报告。发现缺陷通过主 Agent 回流。 |

---

## 1. 通用上下文包（所有提示词的前置段落）

```text
【项目】法医尸表检验文书整理 Agent（FastAPI + Vue3 + SQLite）
【工作目录】C:\Users\Ardeon\Downloads\师表报告
【必读，动手前全部读完】
  - AGENTS.md                 项目开发规则与硬性约束
  - docs/requirements.md      需求规格，验收编号 R-*/N-*/A-*
  - docs/architecture.md      分层、数据模型、API 契约、模块签名、文件所有权
  - docs/TODO_DOWNLOADS.md    需要下载的待办（一律不执行）
  - backend/app/constants.py  [冻结契约] 枚举、字段目录、阶段、种子规则
  - backend/app/schemas.py    [冻结契约] Pydantic 请求模型

【绝对禁止】
  1. 执行 pip install / npm install / git clone / 任何联网下载；
     缺少的能力写 TODO-DL-x 并保留函数接口。
  2. 修改 backend/app/constants.py 与 backend/app/schemas.py（冻结契约）。
     发现契约缺陷：不要改，写进交付说明的「契约问题」小节。
  3. 编辑「独占文件」清单以外的任何文件。需要别人改时，写进交付说明的「请求协作」小节。
  4. 生成鉴定意见（死亡原因/时间/致伤工具/机制）；不得把空白栏目写成“未见异常”。
  5. 用「未验证」「应该可以」等措辞描述结果；只报告你实际运行过的命令与真实输出。

【环境事实】
  - Python: D:\Env\Py13\python.exe (3.13)，已装 fastapi/pydantic/pytest/httpx/python-docx/Jinja2/Pillow/PyMuPDF
  - 未安装（记为 TODO-DL）：docxtpl(1) / paddleocr(2) / pypdfium2(4) / 前端 npm 依赖(3)
  - LibreOffice 已安装：C:\Program Files\LibreOffice\program\soffice.exe
  - 命令统一用 `python -m ...` 形式，工作目录为项目根目录。

【交付说明格式】回复中必须包含：
  1. 独占文件清单（逐个说明实现了什么）
  2. 实际运行的验证命令 + 真实输出摘要
  3. 未完成/降级项，逐条标注 TODO-DL-x
  4. 契约问题（若有）
  5. 请求协作（若有）
```

---

## 2. 开发子智能体

### 2.1 W1 后端核心（子智能体 A）

**独占文件**：`backend/app/storage.py`、`backend/app/database.py`、`backend/app/workflow.py`、
`backend/app/seed.py`、`backend/app/main.py`、`backend/app/routes.py`、`backend/scripts/smoke.py`

```text
【角色】后端核心开发。你是本工作流唯一所有者。

【任务】
按 docs/architecture.md 第 5.1 / 5.2 / 5.10 / 5.11 节的签名，逐字实现：
  storage.py   CaseStorage：原件/副本/文稿目录分离，write_original 不覆盖已存在原件
  database.py  Database：11 张表的 DDL、_claim 乐观锁、审计事件、get_case 聚合返回、
               stats、seed；严格实现第 2 节「不变量」INV-1 ~ INV-10
  workflow.py  run_ocr / run_preprocess / run_extraction / run_normalization /
               run_render / run_check / run_delivery 七个阶段编排
  seed.py      seed_demo_case：一个完整演示案件（委托书+记录本+sidecar OCR JSON+
               程序化生成的演示模板+一处低置信度+一条否定词+两处损伤+一处材料冲突）
  routes.py    第 4 节全部 HTTP 端点 + 第 4.2 节错误映射（统一 _handle_error）
  main.py      create_app(db_path=None, storage_root=None, seed_data=True)，
               CORS 允许 5173，frontend/dist 存在时挂载到 "/"
  scripts/smoke.py  离线端到端演练：建案 → 材料 → OCR → 提取 → 整理 → 生成 → 校核，
               打印每步结果与生成文件路径；用 `python -m backend.scripts.smoke` 运行

【依赖（由其他子智能体并行实现，按签名调用，不要自己实现）】
  imaging.py / ocr/* / extractor.py / terminology.py / llm.py   → W2
  docgen.py / checker.py                                        → W3
  这些模块可能在你写代码时还不存在。请按签名 import 并调用；
  为降低耦合，workflow.py 内对 W2/W3 的调用使用模块级 import（不要函数内延迟 import），
  保证 `python -m compileall` 通过即可，集成由主 Agent 负责。

【必须遵守】
  - 所有写操作先 _claim(case_id, expected_version)；冲突抛 VersionConflict → 409
  - 生成 pending_issue 文稿前调用 database.open_blocking_items()，非空抛 BlockedByChecklist → 409
  - get_case() 返回结构必须与第 5.2 节注记完全一致，所有 *_json 列解析为对象
  - opinion_text 只能由 CaseUpdate / 专用端点写入，任何路径不得从 OCR 文本推导（N-1）
  - 模型网络请求（llm.py）期间不得持有 SQLite 写事务
  - 不覆盖原件；sha256 用于同一材料重复上传的判定
  - 审计事件覆盖：建案、加材料、预处理、OCR、提取、字段修改、损伤增删改、整理、
    生成文稿、审核、校核、清单处理、修正、交付、阶段变更

【验收命令（必须实际运行并贴出真实输出）】
  python -m compileall -q backend
  python -c "from backend.app.main import create_app; app=create_app('data/w1.db', seed_data=True); print('ok')"
  python -m pytest -q          # 此时测试可能尚未由 W5 写好；若失败只报告，不要改测试文件
  python -m backend.scripts.smoke
```

### 2.2 W2 后端管道（子智能体 B）

**独占文件**：`backend/app/imaging.py`、`backend/app/ocr/__init__.py`、`backend/app/ocr/base.py`、
`backend/app/ocr/sidecar.py`、`backend/app/ocr/paddle.py`、`backend/app/extractor.py`、
`backend/app/terminology.py`、`backend/app/llm.py`

```text
【角色】后端识别与术语管道开发。你是本工作流唯一所有者。

【任务】按 docs/architecture.md 第 5.3 ~ 5.6、5.9 节的签名逐字实现。

  imaging.py     probe / extract_pdf_text / render_pdf_pages / preprocess_image / sha256_file
                 - PDF 渲染优先 pypdfium2，缺失时用已安装的 PyMuPDF(fitz)，都不可用返回 []
                 - preprocess_image 未知操作进 skipped，绝不抛异常
                 - extract_pdf_text 只读原生文字层，不得当作准确原文（R-18）
  ocr/base.py    OcrBlock / OcrEngineUnavailable / OcrEngine 协议
  ocr/sidecar.py SidecarOcrEngine：读 <image_stem>.ocr.json，离线可用（默认引擎）
  ocr/paddle.py  PaddleOcrEngine：available() 探测 import；不可用时 recognize() 抛
                 OcrEngineUnavailable，错误信息含 "TODO-DL-2"
  ocr/__init__.py register_engine / get_engine / resolve_engine / engine_status
  extractor.py   infer_status / parse_blocks / extract_fields / extract_injuries / detect_conflicts
                 - 一行 OCR 文本 → 至多一条损伤，绝不合并多行（R-16/R-36）
                 - 只填有来源的字段；同字段多来源不同值 → 保留冲突并把 status 置 unclear
                 - confidence 取来源块最小置信度；低置信度由 workflow 置 unclear
  terminology.py compile_rules / normalize_text / normalize_field / normalize_injury / normalize_case
                 - 严格实现第 5.6 节全部硬性规则，特别是：
                   not_recorded → "未记录" 且绝不含"未见异常"（N-5/R-35）
                   不改数值精度（3 不得变 3.0，N-3）
                   不改左右侧、不漏否定词（N-4）
                   未命中规则且 recorded → 保留原文 + requires_confirmation=True + issues 记"无认可规则"
  llm.py         verify_key / extract_with_llm：密钥只从参数来，不落库不落日志；
                 模型只允许挑选并标注来源块；非法 field_key 丢弃；错误映射 401/429/502/504

【必须遵守】
  - 纯离线：不得 import 未安装的包到模块顶层（docxtpl/paddleocr/pypdfium2 必须延迟 import 或用 try/except）
  - 不做任何 I/O 网络请求（除 llm.py，且测试必须用 httpx.MockTransport）
  - 不生成鉴定意见；不从 OCR 文本推导死亡时间等结论（N-1/R-32）

【验收命令（必须实际运行并贴出真实输出）】
  python -m compileall -q backend
  python -c "from backend.app.ocr import engine_status; print(engine_status())"
  python -c "from backend.app.terminology import normalize_text; print(normalize_text('', status='not_recorded'))"
  python -c "from backend.app.terminology import normalize_text; print(normalize_text('尸长172', field_key='body_length', column='尸长'))"
  # 期望：'未记录'（不含“未见异常”）；'尸长172 cm。' 且 requires_confirmation=True（未注明单位）
```

### 2.3 W3 后端产出（子智能体 C）

**独占文件**：`backend/app/docgen.py`、`backend/app/checker.py`

```text
【角色】文书生成与质量校核开发。你是本工作流唯一所有者。

【任务】按 docs/architecture.md 第 5.7、5.8 节逐字实现。

  docgen.py
    - docx_engine_status()：报告 preferred/docxtpl_available/todo="TODO-DL-1"
    - render_document(..., engine="auto")：auto → docxtpl 可导入则 docxtpl，否则 builtin
      builtin 渲染器：python-docx 打开模板副本，对段落与表格单元格取合并文本，
      用 jinja2.Template 渲染后写回首个 run 并清空其余 run，保留段落格式；
      必须支持 {{ var }}、{% for x in xs %}...{% endfor %}、{% if %}...{% else %}...{% endif %}；
      跨段落的 {% for %} 用 copy.deepcopy(paragraph._p) 复制段落块
    - unresolved：渲染后仍存在的 {{ ... }} 锚点列表
    - build_demo_template()：用 python-docx 程序化生成演示模板（标题/基础信息表/
      案情/尸表一般所见/损伤循环/意见栏/签名栏），占位符与 build_context 的 key 一致
    - build_context()：见第 5.8 节列出的 key；opinion 只取 cases.opinion_text（N-1）
    - detect_libreoffice() / export_pdf()：失败返回 None，绝不抛异常
    - extract_document_text() / document_filename()（案件编号-文档类型-v版本.格式，R-51）
  checker.py
    - 实现第 5.7 节表格中全部 19 个 code，severity/category 严格按表
    - check_case 去重 (code,target_type,target_id)，返回顺序 recognition→content→source→format→correction
    - has_blocking()
    - 硬性：NOT_RECORDED_MISLABELED 检查 not_recorded 字段的 normalized_text 是否含"未见异常"
      （N-5）；OPINION_FROM_SOURCE 检查 opinion 是否与任一 OCR 块文本高度重合（N-1）；
      NEGATION_UNPRESERVED 检查 negative/not_examined 字段是否丢失全部否定词（N-4）

【依赖】W1 的 database 结构、W2 的 constants 字段目录。只按签名调用，不自己实现。

【验收命令（必须实际运行并贴出真实输出）】
  python -m compileall -q backend
  python -c "from backend.app.docgen import docx_engine_status, detect_libreoffice, build_demo_template; print(docx_engine_status()); print(detect_libreoffice()); print(build_demo_template('storage/templates/report_template.docx'))"
  python -c "
from backend.app.docgen import build_demo_template, render_document, extract_document_text
t=build_demo_template('storage/templates/report_template.docx')
r=render_document(t, {'case_no':'FY-2026-0001','subject_name':'张三','injuries':[{'seq':1,'text':'左前臂见擦伤，范围为3 cm × 2 cm。'}],'general_findings':[]}, 'storage/_t.docx')
print(r); print(extract_document_text('storage/_t.docx')[:300])"
  # 期望：engine 为 builtin，unresolved 为空，输出文本含 "FY-2026-0001" 与损伤文字
```

### 2.4 W4 前端（子智能体 D）

**独占文件**：`frontend/src/App.vue`、`frontend/src/components/**`

**只读**：`frontend/src/api.js`、`frontend/src/main.js`、`frontend/src/styles.css`、
`frontend/index.html`、`frontend/*.config.js`、`frontend/package.json`、全部 `docs/**`、`backend/**`

```text
【角色】前端开发（Vue 3 组合式 API + Vite + Tailwind）。你是本工作流唯一所有者。

【任务】按 docs/architecture.md 第 7.2 节的组件树实现完整工作台。
组件（每个一个 .vue 文件，全部放在 frontend/src/components/）：
  Sidebar.vue / StatsCards.vue / CaseTable.vue / CreateCaseModal.vue / CaseWorkspace.vue /
  WorkflowStepper.vue / MaterialPanel.vue / FieldReviewPanel.vue / InjuryPanel.vue /
  TerminologyRulesPanel.vue / DocumentPanel.vue / ChecklistPanel.vue / CorrectionModal.vue /
  AISettingsModal.vue
另加 App.vue 作为编排层（参考 hotspot-workbench 的 App.vue 结构：视图切换、筛选、
分页、抽屉、toast、409 刷新处理）。

【硬性要求】
  - 四步主流程必须各有明确入口（R-1）：选择材料 / 核对疑点 / 查看文档 / 确认修改
  - 字段核对面板必须同时显示 8 项（R-15）：原文、来源文件、页码、原页位置(bbox)、
    识别结果、确认后的值、规范化文字、使用的规则、复核状态
  - 空白字段显示「未记录」，绝不显示「未见异常」（N-5）
  - unclear 字段必须有醒目的「待辨认/冲突」标记，并展示冲突双方来源（R-44）
  - opinion 栏目必须标注「由专业人员提供」，且只提供手工输入（N-1）
  - 409 统一处理：刷新案件详情与列表，提示「已被其他操作更新，请刷新后重试」
  - 全部文案中文；只用 vue 与 lucide-vue-next，不新增依赖
  - 不出现遗留占位符、文字遮挡、表格溢出（R-52）

【环境限制（重要）】
  无法执行 npm install / npm run build（TODO-DL-3，需要下载）。
  因此你必须：
    1. 只使用 api.js 已导出的方法，不得新增/改名 api.js（它是冻结契约）
    2. 逐个文件自查：import 路径存在、组件名与 props/emits 一致、
       `<script setup>` 语法正确、模板标签闭合、没有未定义变量
    3. 用 `node --check` 检查所有 .js 文件；.vue 文件无法构建验证，
       必须在交付说明中明确写「未经过构建验证，原因 TODO-DL-3」

【验收命令（必须实际运行并贴出真实输出）】
  node --check frontend/src/api.js
  node --check frontend/src/main.js
  Get-ChildItem frontend/src/components/*.vue | ForEach-Object { $_.Name }
```

---

## 3. Review 子智能体（只读，输出报告到 `docs/reviews/`）

### 3.1 R1 需求覆盖与红线评审

**独占文件**：`docs/reviews/review-requirements-coverage.md`

```text
【角色】需求覆盖评审员。你只读代码，只写这一份报告。

【方法】
  1. 逐条读 docs/requirements.md 的每个 R-* / N-* / A-* 编号。
  2. 对每条，在 backend/ 与 frontend/ 中找出实现位置（文件:行）。
  3. 判定：已实现 / 部分实现 / 未实现 / 无法验证，并给出证据。
  4. 重点核验 10 条红线 N-1 ~ N-10 是否在代码层面真的不可违反
     （不是靠注释声明，而是靠数据结构、约束或测试）。
  5. 特别检查：
     - N-5：not_recorded 是否在任何路径都不可能变成"未见异常"
     - N-1：opinion 是否有任何从 OCR 文本推导的代码路径
     - R-15：8 项来源关联是否都落库且都能通过 API 取出
     - R-16：损伤是否逐条存储、是否可能被合并
     - R-28：pending_issue 是否真的被阻断项拦住

【输出】docs/reviews/review-requirements-coverage.md，含：
  - 覆盖矩阵表：编号 | 状态 | 证据(文件:行) | 备注
  - 缺陷列表：每条含 严重级别(blocking/major/minor) | 编号 | 现象 | 证据 | 建议
  - 结论：是否可以进入验收
【禁止】修改任何非 docs/reviews/ 下的文件。
```

### 3.2 R2 后端契约与并发评审

**独占文件**：`docs/reviews/review-backend-contract.md`

```text
【角色】后端契约、并发与数据一致性评审员。

【方法】读 backend/app/**，对照 docs/architecture.md 第 2、3、4、5 节逐项核对：
  1. 11 张表的列是否与第 2 节一致；INV-1 ~ INV-10 是否真的被代码强制
  2. 第 4 节 API 表：路径、请求模型、状态码是否逐一对应；
     _handle_error 是否覆盖第 4.2 节全部异常映射
  3. 乐观锁：是否**每个**写操作都先 _claim；是否存在漏掉的写路径；
     是否存在先写后校验、或在网络请求期间持有写事务的情况
  4. 状态机：阶段是否只能 ±1；文稿状态流转是否与第 3.2 节一致；
     是否有绕过 BlockedByChecklist 的路径
  5. get_case() 返回结构是否与第 5.2 节注记完全一致（字段名、嵌套、JSON 已解析）
  6. 密钥：X-DeepSeek-API-Key 是否可能进入数据库/日志/异常信息
  7. 事务边界：sqlite3 连接是否 commit/rollback/close 正确；是否有连接泄漏
  8. 幂等与重复：同一材料重复上传、同一案件编号重复建案的行为是否明确

【输出】docs/reviews/review-backend-contract.md：
  - 逐项核对表：契约条目 | 符合/不符合 | 证据(文件:行) | 说明
  - 缺陷列表：严重级别 | 现象 | 复现路径 | 证据 | 建议修复
  - 明确列出你**实际运行过**的验证命令与输出（如 python -m compileall、pytest）
【禁止】修改 backend/ 下任何文件；只读、只写报告。
```

### 3.3 R3 前端与领域正确性评审

**独占文件**：`docs/reviews/review-frontend-domain.md`

```text
【角色】前端与领域正确性评审员。

【方法】
  1. 读 frontend/src/**，对照 docs/architecture.md 第 7 节：
     组件树是否完整、api.js 是否被按契约使用、props/emits 是否自洽
  2. 逐文件检查 Vue 语法：`<script setup>`、模板标签闭合、v-for 有 :key、
     未定义变量、错误的 ref/reactive 用法、watch 死循环风险
  3. 领域正确性（最重要）：
     - 界面是否会把空白字段显示为"未见异常"（N-5 违规）
     - 字段核对面板是否展示 R-15 要求的全部 8 项
     - unclear/冲突字段是否有醒目区分（R-44）
     - opinion 栏目是否只允许手工输入且标注来源（N-1）
     - 四步主流程入口是否清晰（R-1）
     - 409 是否触发刷新与提示
  4. 由于无法 npm run build（TODO-DL-3），你必须用**逐文件静态推演**替代：
     列出你推演过的文件与结论，并明确声明构建未验证。

【输出】docs/reviews/review-frontend-domain.md：
  - 组件清单核对表：组件 | 是否存在 | 契约符合性 | 备注
  - 语法与一致性缺陷列表（含文件:行）
  - 领域正确性缺陷列表（含 R-*/N-* 编号）
  - 「未验证事项」小节，明确列出因 TODO-DL-3 无法验证的内容
【禁止】修改 frontend/ 下任何文件；只读、只写报告。
```

---

## 4. 测试子智能体

### 4.1 T1 领域规则测试（纯单元，无 I/O）

**独占文件**：`backend/tests/__init__.py`、`backend/tests/conftest.py`、
`backend/tests/test_terminology.py`、`backend/tests/test_extractor.py`、`backend/tests/test_checker.py`

```text
【角色】领域规则测试作者。你只写测试，不改实现。

【任务】为 W2/W3 的纯逻辑模块写 pytest 用例，逐条锚定需求编号。
  test_terminology.py
    - R-30 尸长172cm → 尸长172 cm。；数值精度不变（N-3：3 不得变 3.0）
    - R-31 左前臂擦伤3×2cm → 左前臂见擦伤，范围为3 cm × 2 cm。
    - R-32 尸斑"背侧，紫红，指压褪色" → 尸斑位于背侧，呈紫红色，指压褪色。；不得出现死亡时间
    - R-33 "尸长172" 未注明单位 → 保留数值 + requires_confirmation=True
    - R-34 "压退" 含义不明 → 不展开为完整尸斑描述 + requires_confirmation=True
    - R-35 栏目空白 → normalized_text == "未记录" 且 "未见异常" not in 结果（N-5）
    - N-4 左右侧/否定词不被改写
    - R-23 未命中任何规则且 recorded → 保留原文 + requires_confirmation
    - R-36 两处损伤分别整理，尺寸不串位
  test_extractor.py
    - infer_status 五种状态各自的判定
    - 一行 OCR → 至多一条损伤（R-16）
    - 只填有来源的字段；无来源字段不产生记录
    - 同字段多来源不同值 → status=unclear + conflict 非空（R-44）
    - seq 按 (page_number, block_index) 递增（R-3 保留原有顺序）
  test_checker.py
    - 逐个 code 构造触发条件，断言 code/category/severity 与第 5.7 节表一致
    - NOT_RECORDED_MISLABELED 必须能在 normalized_text 含"未见异常"时触发
    - OPINION_FROM_SOURCE 必须能在 opinion 与 OCR 块重合时触发
    - 去重规则 (code,target_type,target_id)
    - 返回顺序 recognition→content→source→format→correction
    - has_blocking 判定

【纪律】
  - 断言必须锚定需求编号（测试函数 docstring 写 `R-35 / N-5`）
  - 不写"跑通就行"的空测试；每个用例要有明确的失败含义
  - 不 mock 被测函数本身；只 mock 外部 I/O
  - 若发现实现与契约不符：**不要改实现**，把用例保留为失败并在交付说明中报告

【验收命令】python -m pytest backend/tests/test_terminology.py backend/tests/test_extractor.py backend/tests/test_checker.py -q
```

### 4.2 T2 API 与集成测试

**独占文件**：`backend/tests/test_api_cases.py`、`backend/tests/test_api_pipeline.py`、
`backend/tests/test_api_corrections.py`

```text
【角色】API 与集成测试作者。你只写测试，不改实现。

【任务】用 fastapi.testclient.TestClient + tmp_path 隔离数据库，覆盖完整链路。
  test_api_cases.py
    - 建案 → 列表（分页/搜索/筛选）→ 详情 → 改案 → 阶段推进/回退 → 删除
    - 重复案件编号 → 409；不存在 → 404；非法阶段跳转 → 409
    - 每个写端点：缺 expected_version → 422；过期版本 → 409
  test_api_pipeline.py
    - 演示案件全链路：加材料 → 预处理 → OCR → 提取 → 字段核对 → 损伤增删改序
      → 术语整理 → 生成 internal_draft → 校核 → 处理阻断项 → 生成 pending_issue
    - R-28：存在 open 的 blocking 项时生成 pending_issue → 409
    - N-5：任何响应里 not_recorded 字段的 normalized_text 不得是"未见异常"
    - R-15：字段响应必须含 8 项来源关联
    - OCR 引擎不可用（paddle）→ 409 且 detail 含 TODO-DL-2
    - 生成的 .docx 文件真实存在且可被 python-docx 打开，正文无 {{ }} 残留（R-5）
  test_api_corrections.py
    - 四类修正各自生效（R-38）：recognition / terminology / format / workflow_order
    - 事实修正后依赖字段 review_state 回到 pending、旧文稿 superseded（R-40/R-41）
    - 已审核版本仍可下载（R-41）
    - scope != case_only 但 scope_value 为空 → 422（R-39）
    - corrections 记录含 before/after/reason/confirmed_by/scope/affected（R-38）

【纪律】
  - 每个测试用独立 tmp_path 数据库，不共享状态
  - 不访问网络；模型相关用 httpx.MockTransport 或 monkeypatch
  - 断言状态码 + 关键字段，不要只断言 200
  - 不修改 backend/app/**；发现缺陷保留失败用例并报告

【验收命令】python -m pytest backend/tests/test_api_cases.py backend/tests/test_api_pipeline.py backend/tests/test_api_corrections.py -q
```

### 4.3 T3 独立验证（对抗性）

**独占文件**：`docs/reviews/verification-report.md`（不写任何测试文件）

```text
【角色】独立验证员。你的任务是**试图证伪**前面所有"已完成"的声明。

【方法】
  1. 亲自运行：python -m pytest -q，记录真实通过/失败数与失败原因。
  2. 亲自运行：python -m compileall -q backend。
  3. 亲自运行：python -m backend.scripts.smoke，检查是否真的产出 .docx 与校核清单。
  4. 亲自做**绕过测试**（用 python -c 或临时脚本，放在 storage/ 下，不留在仓库）：
     - 能否让 not_recorded 字段的 normalized_text 变成"未见异常"？
     - 能否在存在 blocking 项时生成 pending_issue？
     - 能否绕过 expected_version 写入（如并发两次同版本请求）？
     - 能否让 opinion 由 OCR 文本自动生成？
     - 能否把两处损伤合并成一条？
     - 能否在响应里拿到未解析的 JSON 字符串（而非对象）？
  5. 检查是否有测试是"空测试"（永远通过、无断言、只断言 True）。
  6. 检查是否真的没有执行过下载（搜索代码与文档中的 pip install / npm install 痕迹，
     确认只出现在文档的 TODO 说明里）。

【输出】docs/reviews/verification-report.md：
  - 命令 | 真实输出摘要 | 判定
  - 绕过尝试表：尝试 | 结果(成功绕过/被拦住) | 证据
  - 空测试清单（若有）
  - 未能证伪的声明 vs 已证伪的声明
  - 最终结论：可交付 / 有条件可交付 / 不可交付，并列出前置条件
【禁止】修改 backend/** 与 frontend/** 的任何文件。
```

---

## 5. 主 Agent 的编排职责

1. 冻结契约（`constants.py`、`schemas.py`、配置文件、`docs/*`）。
2. 同时派发 W1~W4（4 个开发子智能体），互不重叠文件。
3. 开发完成后派发 R1~R3（3 个只读评审子智能体）。
4. 评审报告回流后，把 `blocking` 缺陷以最小补丁派回**原所有者**修复。
5. 派发 T1~T3（3 个测试/验证子智能体）。
6. 主 Agent 亲自集成：运行 `python -m pytest`、`python -m compileall -q backend`、
   `python -m backend.scripts.smoke`，核对生成文件，汇总交付说明。
7. 只有测试全绿、无未关闭 blocking 缺陷、`TODO-DL-*` 已明确记账时，才报告完成。

---

## 6. 附录：v2 新增约束（部位照片与可运行前端）

**每个子智能体提示词都必须追加下面这段（不得省略）：**

```text
【N-11 部位照片禁令】kind == "photo" 的材料只按用户指定的 position_key 放到模板对应栏位。
不得对照片做 OCR、内容识别、描述、分类或推断；不得调用任何视觉/模型接口；不得把照片内容
写进任何字段、损伤或鉴定意见。你本人在实现与自测过程中也不得查看或理解照片像素内容，
只能把它当文件路径处理（允许读取宽高、尺寸等几何元数据）。
数据通路固定为：上传（originals/photo/…）→ 用户选择位置（materials.position_key）→
模板锚点 {{ photo_<key> }} → docgen 按路径插入图片。除这条通路外，照片不得流向其它任何模块。

【N-12 空栏位】模板显式声明的照片栏位若本次没有照片，正文写 PHOTO_PLACEHOLDER_TEXT
（“（未提供照片）”），不得删除栏位、不得用别的栏位照片顶替、不得写“未见异常”。

【可运行前端】本机无法离线安装 npm 依赖（已是实测结论，见 docs/TODO_DOWNLOADS.md），
`frontend/static/**` 是无构建、无外链的工作台，由 FastAPI 在 `/` 提供；Vue 3 源码只是
契约合规的源码补全，未经构建验证。
```

**Wave 划分（v2 实际执行）**：Wave 0 契约与阻塞修复（主 Agent）→ Wave 1 五个开发子智能体
（W1 后端核心 / W2 后端管道 / W3 后端产出 / W4 Vue 源码 / W4b 可运行工作台）→
Wave 2 测试 → Wave 3 三份评审 + 主 Agent 集成。
