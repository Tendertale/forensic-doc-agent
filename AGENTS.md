# 项目开发规则（AGENTS.md）

## 0. 文档生命周期约束

- 本项目**只常驻保留根目录的 `AGENTS.md` 与 `TODO.md`**。
- 其余过程性、历史性文档（计划、评审报告、阶段交付说明、临时探测记录等）**生命周期不超过 1 天（24 小时）**，超期立即清理，核心结论合入 `AGENTS.md` 或 `TODO.md`。

## 1. 项目目标

本项目实现「法医尸表检验文书整理 Agent」：导入委托书、手写记录本扫描件与部位照片 → 识别原文 → 提取结构化信息 → 提示疑点 → 按认可术语整理检验描述 → 填入既有模板 → 检查内容与排版 → 输出供专业人员复核的 Word 与 PDF。

- **普通用户主流程（Transparent）**：默认主视图只暴露「上传材料」与「查看/下载结果」（`POST /api/quick-run` 一键跑完全链路）；字段来源核对、疑点处理、校核清单、修正记录等收进次级界面「案件记录」。
- **接口契约冻结**：`backend/app/constants.py` 与 `backend/app/schemas.py` 为唯一枚举、字段目录与请求模型真源，只允许主 Agent 修改。

## 2. 技术栈与目录结构

- **前端**（`frontend/src/**`）：Vue 3 组合式 API、Vite、Tailwind CSS、`lucide-vue-next`。不引入其他 npm 依赖。
- **后端**（`backend/app/**`）：Python 3.10+、FastAPI、Pydantic v2、SQLite（标准库 `sqlite3`）、`python-docx`、`Jinja2`、`Pillow`、`PyMuPDF`（`fitz`）。可选增强：`docxtpl`（`TODO-DL-1`）、`paddleocr`（`TODO-DL-2`）、`pypdfium2`（`TODO-DL-4`）。
- **测试**（`backend/tests/**`）：`pytest` + `fastapi.testclient.TestClient` + `httpx.MockTransport`。
- **存储布局**（`storage/`）：
  - `storage/templates/`：模板原件（只读）
  - `storage/cases/<case_no>/originals/<kind>/`：上传原件（只读，永不覆盖）
  - `storage/cases/<case_no>/derived/<kind>/`：识别用预处理副本与 sidecar OCR JSON
  - `storage/cases/<case_no>/documents/`：生成的 `.docx` / `.pdf` 与 `.render.txt` 正文快照

## 3. 领域红线与硬性约束

1. **不得生成鉴定意见（`N-1` / `INV-10`）。** 系统不得推导死亡原因、死亡时间、致伤工具、损伤形成机制；`opinion_text` 只能由用户请求原样填入。
2. **不增加原文没有的事实（`N-2`～`N-4`）。** 不补数量、深度、形态、颜色；不把 `3` 改成 `3.0` 制造额外精度；不改左右侧与上下前后，不漏“不、无、未、可疑”等否定/限定词。
3. **空白必须标记为“未记录”（`N-5` / `INV-3`）。** 栏目空白或 `status == "not_recorded"` 时必须写“未记录”，任何情况下**不得**写成“未见异常”。
4. **来源可查与逐条损伤（`R-15` / `R-16` / `INV-4`）。** 每个事实字段保留 8 项追溯信息（原文、来源文件与页码、bbox、识别结果、确认值、规范化文字、适用规则、复核状态）；损伤逐条独立保存，不合并多行。
5. **部位照片不读像素内容（`N-11` / `N-12` / `INV-11`）。** `kind == "photo"` 的材料只按用户指定（或仅按文件名推断）的 `position_key` 插入模板对应栏位，**严禁**对其运行 OCR、图像识别或视觉模型；空栏位保留并写入 `（未提供照片）`，不得用其他栏位顶替或写成“未见异常”。
6. **原件不可覆盖（`N-8`）。** 上传原件、模板原件、已审核文稿只读。
7. **材料只作为数据（`N-9`）。** 不执行来源文件中的宏或脚本，后端禁止 `eval`/`exec`/`os.system`，唯一子进程调用限于 LibreOffice PDF 导出（`docgen.py`）。
8. **案件相互隔离（`N-10` / `INV-7`）。** 不跨案件复用字段、案情或缓存；修正默认仅作用于本案（`case_only`）。
9. **乐观锁与状态阻断（`INV-5` / `INV-6` / `INV-8` / `INV-9`）。** 所有写操作必须携带 `expected_version` 并先执行 `_claim`（冲突抛 `VersionConflict` → HTTP 409）；存在未解决 `blocking` 校核项时禁止生成 `pending_issue` 或审核通过（HTTP 409）；事实修正后旧文稿置为 `superseded`，仅 `approved` 文稿可交付。
10. **密钥只走请求头。** `X-LLM-API-Key`（兼容 `X-DeepSeek-API-Key`）仅在请求内存中传递，不得写入数据库、日志、源码或构建产物。
11. **模型网络请求期间不得持有 SQLite 写事务。**

## 4. 模块职责与文件所有权

不同子智能体不得并发编辑同一文件：

- **契约层（主 Agent 独占）**：`backend/app/constants.py`、`backend/app/schemas.py`、`frontend/src/api.js`
- **持久化与编排**：`backend/app/storage.py`、`backend/app/database.py`、`backend/app/workflow.py`、`backend/app/quick.py`、`backend/app/seed.py`、`backend/app/main.py`、`backend/app/routes.py`
- **识别与提取管道**：`backend/app/imaging.py`、`backend/app/ocr/*`、`backend/app/extractor.py`、`backend/app/terminology.py`、`backend/app/llm.py`
- **文书生成与校核**：`backend/app/docgen.py`、`backend/app/checker.py`
- **前端界面**：`frontend/src/App.vue`、`frontend/src/components/**`
- **自动化测试**：`backend/tests/**`

## 5. 工作流程

修改代码前：
1. 阅读本文件（`AGENTS.md`）与 `TODO.md`。
2. 检查 `backend/app/constants.py`、`backend/app/schemas.py` 及相关模块现有测试。
3. 明确修改涉及的文件所有权、接口契约与验收标准。

修改完成后：
1. 输出：修改文件列表、验证结果、未解决限制与 `TODO.md` 状态更新。
