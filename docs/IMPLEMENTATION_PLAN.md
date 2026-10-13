# 实施计划（Implementation Plan）

版本：**v2**（v1 的现状核对已过期，本版按实际执行结果重写）
日期：2026-10-12
依据：`AGENTS.md`「工作流程」第 4 步（文件所有权、接口契约、验收标准）；
需求来源：`法医尸表检验文书Agent_整合稿.md`（用户提供的整合设计稿，桌面文件）
必读基线：`docs/requirements.md`、`docs/architecture.md`、`docs/TODO_DOWNLOADS.md`、`docs/agent-prompts.md`

---

## 0. 本期新增的三项用户要求（相对 v1 计划的增量）

用户原话（要点）：**「让用户可以在前端调用这个 agent 系统，并返回对应的文件，文件内部的文本、图片排版都合理的文档。
注意要对图像进行 OCR 处理，以及用户也会有对目标位置（如身体、腿、头等）的图片，这部分图片需要放到指定位置，
全程不对图片内容进行阅读。」**

| 编号 | 要求 | 落地方式 |
| --- | --- | --- |
| U-1 | **前端可实际调用**并取回文件 | 后端 `GET /` 直接提供 `frontend/static/` 无构建工作台（本机无法离线安装 npm 依赖，见 §2.3）；Vue 3 源码同时补全 |
| U-2 | **文档内文本与图片排版合理** | docgen 渲染器新增照片锚点与居中/说明行排版；导出 PDF 后按几何/结构校验 |
| U-3 | **图像 OCR** | 委托书/记录本扫描件走 `ocr/` 引擎（`sidecar` 离线可跑，`paddle` 预留 TODO-DL-2） |
| U-4 | **部位照片按用户指定位置插入** | `position_key` + `{{ photo_<key> }}` 锚点；新增端点与校验规则 |
| U-5 | **全程不阅读图片内容** | 新增红线 N-11/N-12 + INV-11 + `PHOTO_CONTENT_READ` 校核项；照片只当文件路径处理 |

---

## 1. 现状核对（Wave 0 完成后的事实基础）

### 1.1 环境（实测，非文档推断）

Python 3.13.3（`D:\Env\Py13\python.exe`）；已装 `fastapi 0.138.0`、`pydantic 2.13.3`、`uvicorn 0.49.0`、
`pytest 9.0.3`、`httpx 0.28.1`、`python-docx`、`Jinja2`、`Pillow`、`PyMuPDF 1.27.2.2`；
LibreOffice 已装于 `C:\Program Files\LibreOffice\program\soffice.exe`；`docxtpl` **未装**（TODO-DL-1）。
Node v22.23.2 / npm 10.9.8 / pnpm 11 可用。

### 1.2 Wave 0 已完成（主 Agent，串行）

| ID | 内容 | 证据 |
| --- | --- | --- |
| P0-1 | 修复 `backend/app/docgen.py` 的语法损坏（`class _LoopContext:` → `def _emit_branch(`），并删除 `_process_for` 中死代码 | `python -m py_compile backend/app/docgen.py` 退出码 0；`from backend.app.docgen import render_document, build_demo_template, extract_document_text` 成功 |
| P0-2 | 目录与配置就位：`data/`、`storage/templates/`、`storage/cases/`、`backend/tests/`、`backend/scripts/`、`docs/reviews/`、`frontend/static/`；`pytest.ini` 增 `norecursedirs`；`.gitignore` 增补 | 目录存在；`pytest.ini` 不再递归收集 `_ref_hotspot` |
| P0-3 | **契约扩展**：`constants.py`（`MATERIAL_KINDS` 增 `photo`、`PHOTO_POSITION_DEFINITIONS` 18 个栏位、`OCR_MATERIAL_KINDS`、`PHOTO_MAX_WIDTH_CM`/`PHOTO_WIDE_MAX_WIDTH_CM`/`PHOTO_PLACEHOLDER_TEXT`、`DEFAULT_SECTION_ORDER` 增 `photos`）；`schemas.py`（`MaterialCreate.position_key/photo_caption`、新增 `PhotoPlacementUpdate`） | `python -c "from backend.app.constants import PHOTO_POSITION_DEFINITIONS"` 18 项；`schemas` 正反例校验通过 |
| P0-4 | 文档同步：`docs/requirements.md`（N-11/N-12、R-54～R-58）、`docs/architecture.md`（数据模型、API 表、5.2/5.7/5.8/5.10/7.3、INV-11、存储布局）、`docs/TODO_DOWNLOADS.md`（TODO-DL-7 + TODO-DL-3 实测结论） | 见相应文件 |

### 1.3 仍然缺失（本计划要补齐的部分）

| 编号 | 缺失 | 所有者 |
| --- | --- | --- |
| B-2 | `backend/app/ocr/__init__.py`（四个契约函数） | W2 |
| B-3 | `backend/app/extractor.py` | W2 |
| B-4 | `backend/app/terminology.py` | W2 |
| B-5 | `backend/app/llm.py` | W2 |
| B-6 | `backend/app/workflow.py` | W1 |
| B-7 | `backend/app/routes.py`、`backend/app/main.py` | W1 |
| B-8 | `backend/app/seed.py` | W1 |
| B-9 | `backend/scripts/smoke.py`、`backend/scripts/serve.py` | W1 |
| B-10 | `backend/tests/**`（本项目目前**零测试**，此前 10 passed 来自 `_ref_hotspot` 参考项目，属虚假绿灯） | W5 |
| B-11 | `frontend/src/App.vue` + 12 个组件 | W4 |
| B-12 | `frontend/static/**`（无构建工作台，U-1 的关键） | W4b |
| B-13 | `docs/reviews/**` 三份评审报告；交付说明 | W6 / 主 Agent |
| B-14 | `docgen` 照片锚点渲染、`checker` 四个 `PHOTO_*` 校核项、演示模板照片栏、演示案件占位照片 | W3 / W1 |

---

## 2. 契约台账

### 2.1 文件所有权（同一时刻不得有两人编辑同一文件）

| 工作流 | 所有者 | 独占文件 |
| --- | --- | --- |
| W0 契约（**本波已完成**） | 主 Agent | `backend/app/constants.py`、`backend/app/schemas.py`、`frontend/src/api.js`、`pytest.ini`、`.gitignore`、全部 `docs/**` |
| W1 后端核心 | 子智能体 A | `backend/app/storage.py`、`database.py`、`workflow.py`、`seed.py`、`routes.py`、`main.py`、`backend/scripts/smoke.py`、`backend/scripts/serve.py` |
| W2 后端管道 | 子智能体 B | `backend/app/imaging.py`、`ocr/**`、`extractor.py`、`terminology.py`、`llm.py` |
| W3 后端产出 | 子智能体 C | `backend/app/docgen.py`、`checker.py` |
| W4 Vue 源码 | 子智能体 D | `frontend/src/App.vue`、`frontend/src/components/**` |
| W4b 可运行工作台 | 子智能体 E | `frontend/static/**` |
| W5 测试 | 子智能体 F | `backend/tests/**` |
| W6 评审 | 评审子智能体 | 只读；报告写 `docs/reviews/*.md` |

**只读**：`frontend/index.html`、`frontend/src/main.js`、`frontend/src/styles.css`、`frontend/*.config.js`、
`frontend/package.json`、`frontend/src/components/Sidebar.vue`、`StatsCards.vue`。

### 2.2 必须逐字遵守的不变量（写进每个子智能体提示词）

1. 任何写操作先 `_claim(case_id, expected_version)` → `VersionConflict` → HTTP 409（INV-9）。
2. `not_recorded` → `normalized_text == "未记录"`，任何路径不得含「未见异常」（N-5/INV-3）。
3. 生成 `pending_issue` 前查 `open_blocking_items()`，非空抛 `BlockedByChecklist` → 409（INV-5/R-28）。
4. `opinion_text` 只由用户请求写入，无任何从 OCR 推导的路径（N-1/INV-10）。
5. 一行损伤 OCR → 至多一条记录，绝不合并（R-16/INV-4）。
6. **`kind == "photo"` 的材料只贡献图像文件与 `position_key`；不得写入 `ocr_blocks`、不得进入提取来源、
   不得调用任何视觉/模型接口（N-11/INV-11/R-55）。**

### 2.3 前端无法构建的实测结论与处置

`pnpm install --offline` 失败：`ERR_PNPM_NO_OFFLINE_META`（pnpm 元数据缓存没有 `vue@3`、`vite`、
`@vitejs/plugin-vue`、`tailwindcss@3`、`postcss`、`autoprefixer`、`lucide-vue-next`）；npm 缓存中只有
`vue@2.7.16` 与 `vite@8.x`，与 `frontend/package.json` 不匹配。**在“禁止联网下载”约束下，Vue 3 源码
不能构建、也不能在浏览器运行**。处置：新增 `frontend/static/`（原生 ES 模块 + fetch，无构建、无外链）
由 FastAPI 在 `/` 提供，作为当前唯一可运行前端；Vue 3 源码仍按契约补全（TODO-DL-3 记账）。

---

## 3. 任务清单

### 3.1 Wave 1 —— 五个开发子智能体并行（已完成派发）

| ID | 所有者 | 文件 | 关键要求 |
| --- | --- | --- | --- |
| P1-A | A | 见 §2.1 W1 | 照片登记与位置元数据、幂等迁移、`set_photo_placement`/`list_photo_slots`、`run_ocr` 跳过照片、`run_render` 接照片槽位、全部 HTTP 端点、`create_app` 静态托管回退、演示案件（含 4 张占位照片）、smoke（含 inline_shapes 断言）、serve |
| P1-B | B | 见 §2.1 W2 | `ocr/__init__.py` 契约函数（解冻点，最先做）、`paddle` 预留、`extractor` 状态推断与逐条损伤、`terminology` 全部硬性规则、`llm` 默认关闭、`imaging` 存量复核 |
| P1-C | C | 见 §2.1 W3 | `docgen` 全签名 + **照片锚点渲染（居中、按栏位宽度、保持纵横比、无照片写占位文本）** + 演示模板（含 14 个照片栏位）；`checker` 19 个原 code 逐字一致 + 4 个 `PHOTO_*` code |
| P1-D | D | 见 §2.1 W4 | 13 个文件按组件树补全；`PhotoPanel.vue` 位置指派；R-15 八项来源同屏；N-1/N-5/R-44 前端硬性要求 |
| P1-E | E | 见 §2.1 W4b | 无构建工作台：四步主流程、文件上传（base64）、原图预览、照片位置指派、字段 8 项来源、文稿生成与下载、409 统一处理 |

**Wave 1 出口**：`python -m compileall -q backend` 退出码 0；`create_app` 成功；
`python -m backend.scripts.smoke` 全链路跑通并打印真实 `.docx`/`.pdf` 路径与 inline_shapes 断言。

### 3.2 Wave 2 —— 测试（依赖 Wave 1 出口）

| ID | 所有者 | 文件 | 覆盖重点 |
| --- | --- | --- | --- |
| P2-F | F | `backend/tests/**` | 逐条锚定 R-/N- 编号；N-1/N-3/N-4/N-5/N-11、R-15 八项来源、R-16 逐条损伤、R-28 阻断 409、R-44 冲突、R-54～R-58 照片链路、乐观锁 409、422 校验；`.docx` 真实存在、可被 python-docx 打开、正文无 `{{ }}` 残留、照片落在正确栏位 |

**出口**：`python -m pytest` 全绿，且收集到的用例全部来自 `backend/tests/`。

### 3.3 Wave 3 —— 评审、集成与交付（主 Agent 收口）

| ID | 所有者 | 产物 |
| --- | --- | --- |
| P3-R1 | 评审子智能体 | `docs/reviews/review-requirements-coverage.md`（逐条 R-/N- 覆盖矩阵 + blocking/major/minor） |
| P3-R2 | 评审子智能体 | `docs/reviews/review-backend-contract.md`（11 表列名、INV-1～INV-11、端点状态码、乐观锁覆盖、密钥不落库） |
| P3-R3 | 评审子智能体 | `docs/reviews/review-frontend-photo.md`（工作台/组件树、照片栏位与 N-11 前端合规、TODO-DL-3 未验证项） |
| P3-INT | 主 Agent | 集成：`python -m pytest`、`python -m backend.scripts.smoke`、启动 `serve` 并用 HTTP 校验 `/` 与 `/api/health`、核对产物 `.docx`/`.pdf`（几何/结构级检查，不查看图片内容）、写交付说明 |

---

## 4. 验收标准（Definition of Done）

### 4.1 交付级（逐条可执行）

| # | 命令 / 检查 | 期望 |
| --- | --- | --- |
| 1 | `python -m compileall -q backend` | 退出码 0 |
| 2 | `python -m pytest` | 全绿；用例全部来自 `backend/tests/` |
| 3 | `python -c "from backend.app.main import create_app; create_app('data/smoke.db'); print('app ok')"` | `app ok` |
| 4 | `python -c "from backend.app.docgen import docx_engine_status, detect_libreoffice; print(docx_engine_status(), detect_libreoffice())"` | `preferred='builtin'`、`todo='TODO-DL-1'`、LibreOffice 非 None |
| 5 | `python -c "from backend.app.ocr import engine_status; print(engine_status())"` | `sidecar` 可用、`paddle` 不可用且 detail 含 `TODO-DL-2` |
| 6 | `python -m backend.scripts.smoke` | 全链路成功，打印真实 `.docx`/`.pdf` 路径、`inline_shapes` 数、校核摘要 |
| 7 | `python -m backend.scripts.serve`（默认端口 9765）+ `Invoke-WebRequest http://127.0.0.1:9765/` | 返回工作台 HTML；`/api/health` 返回 `{"status":"ok",...}` |
| 8 | 生成文稿结构检查（python-docx + PyMuPDF） | 图片数 = 已指派可用照片数；照片段落位于对应栏位标题之后；无照片栏位为“（未提供照片）”；正文无 `{{ }}`/`{% %}` 残留；PDF 页数 ≥ 1 |
| 9 | `Select-String -Path backend/app/*.py -Pattern '未见异常'` | 只出现在 checker 的 `NOT_RECORDED_MISLABELED` 判定与注释中 |

### 4.2 领域级（不可用「已实现」代替）

| 项 | 判定方式 | 通过标准 |
| --- | --- | --- |
| N-1 无鉴定意见 | 代码搜索 + 用例 | 无任何从 OCR 推导 `opinion` 的路径 |
| N-5 空白≠正常 | 单元用例 + 生成文稿 | `not_recorded` → 「未记录」，正文与接口均无「未见异常」顶替 |
| N-3 精度不变 | 单元用例 | `3` 不变 `3.0` |
| N-4 左右/否定词 | 单元用例 + `NEGATION_UNPRESERVED` | 改写后左右侧与否定词一致 |
| N-11 不读图片内容 | 代码搜索 + `PHOTO_CONTENT_READ` + 用例 | 照片材料 `ocr_blocks` 恒为 0；无任何照片→文本/结论的代码路径 |
| N-12 空栏位 | 生成文稿检查 | 写“（未提供照片）”，不删栏位、不顶替 |
| R-15 八项来源 | API 用例 + 前端面板 | 原文/来源文件/页码/bbox/识别结果/确认值/规范化文字/规则/复核状态全部可取 |
| R-16 损伤逐条 | 单元用例 | 一行 OCR → 至多一条；多行不合并 |
| R-28 阻断 | API 用例 | 存在 open 的 blocking 项时生成 `pending_issue` → 409 |
| R-54～R-58 照片 | 单元 + API + 生成文稿 | 位置只能来自目录；未指派不进文稿；`.docx` 内图片落在正确栏位 |

---

## 5. 风险与记账

| 风险 | 等级 | 处置 |
| --- | --- | --- |
| 前端 npm 依赖不可离线安装（已实测） | **高** | `frontend/static/` 无构建工作台作为当前可运行前端；Vue 源码仅静态核对，记账 TODO-DL-3 |
| 照片被误读（违反 N-11） | **高** | `OCR_MATERIAL_KINDS` 不含 photo、`run_ocr` 跳过、`PHOTO_CONTENT_READ`（blocking）兜底；全部提示词写入禁令 |
| 照片插图导致排版错乱 | 中 | 固定栏位宽度上限 + 保持纵横比 + 居中 + 说明行小字号；导出 PDF 后按几何校验 |
| 子智能体并行写同一文件 | 中 | §2.1 独占清单；主 Agent 收口时核对 `git status`/时间戳 |
| `docgen` 渲染器复杂度高（Jinja 跨段落 + 图片锚点） | 中 | 由 W3 专项自测（inline_shapes、PDF 页数、unresolved 列表） |
| 真实手写 OCR 未验证（TODO-DL-6/7） | 中 | 不承诺识别率；sidecar 引擎打通流程，paddle 保留接口 |

**TODO-DL 记账（本计划不解除任何一条）**：DL-1 `docxtpl` → `builtin`；DL-2 `paddleocr` → `sidecar`；
DL-3 npm 依赖 → `frontend/static/` 工作台；DL-4 `pypdfium2` → `PyMuPDF`；DL-5 云端 OCR → 仅扩展点；
DL-6 真实样本 → 演示案件；DL-7 真实部位照片 → 程序化占位图。

**全程禁止**：`pip install`、`npm install`、`git clone`、任何联网下载。

---

## 6. 里程碑

| 里程碑 | 内容 | 出口条件 |
| --- | --- | --- |
| **M0** | Wave 0 | docgen 可导入；契约扩展落地；目录/配置就位 |
| **M1** | Wave 1 | `compileall` 通过；`create_app` 成功；`smoke` 产出真实 `.docx`/`.pdf` 且照片在正确栏位 |
| **M2** | Wave 2 | `python -m pytest` 全绿且用例全部来自 `backend/tests/` |
| **M3** | Wave 3 | 三份评审报告落盘；`blocking` 清零或豁免；工作台可通过 HTTP 打开并调用 API |
| **M4** | 第一版完成标准 | **一套材料得到事实有来源、术语有依据、疑点明确、模板正确、照片按指定位置插入、可由专业人员复核的完整文稿**（Word + PDF） |
