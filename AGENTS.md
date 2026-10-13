# 项目开发规则

## 项目目标

本项目实现「法医尸表检验文书整理 Agent」：导入委托书与记录本扫描件 → 识别原文 →
提取结构化信息 → 提示疑点 → 按认可术语整理检验描述 → 填入既有模板 → 检查内容与排版
→ 输出供专业人员复核的 Word 与 PDF。

- 需求规格与验收编号：`docs/requirements.md`
- 架构、数据模型、API 契约与模块签名：`docs/architecture.md`
- 需要下载的待办事项：`docs/TODO_DOWNLOADS.md`
- 子智能体提示词设计：`docs/agent-prompts.md`

**接口契约是冻结的**：`backend/app/constants.py` 与 `backend/app/schemas.py` 只允许主 Agent 修改。
发现契约问题必须在交付说明中报告，不得自行改动。

## 技术栈

- 前端：Vue 3 组合式 API、Vite、Tailwind CSS、`lucide-vue-next`。不引入其他 npm 依赖。
- 后端：Python 3.10+、FastAPI、Pydantic v2、SQLite（标准库 `sqlite3`）、`python-docx`、`Jinja2`、`Pillow`。
- 可选：`docxtpl`（TODO-DL-1）、`paddleocr`（TODO-DL-2）、`pypdfium2`（TODO-DL-4）。见 `docs/TODO_DOWNLOADS.md`。
- 测试：`pytest` + `fastapi.testclient.TestClient` + `httpx.MockTransport`。

## 硬性约束

1. **不联网下载。** 禁止执行 `pip install`、`npm install`、`git clone` 或任何需要等待下载的命令。
   缺少的能力写成 `TODO-DL-x` 并保留接口。
2. **不生成鉴定意见。** 死亡原因、死亡时间、致伤工具、损伤形成机制只能由用户输入（`N-1`）。
3. **不增加原文没有的事实。** 不补数量、深度、形态、颜色；不改左右侧与否定词（`N-2`～`N-4`）。
4. **空白不等于正常。** `not_recorded` 一律写“未记录”，绝不可写“未见异常”（`N-5`）。
5. **原件不可覆盖。** 上传原件、模板原件、已审核文稿只读（`N-8`）。
6. **材料只作为数据。** 不执行来源文件中的宏或脚本（`N-9`）。
7. **案件相互隔离。** 不跨案件复用字段、案情或缓存（`N-10`）。
8. **乐观锁。** 所有写操作先 `_claim(case_id, expected_version)`，失败抛 `VersionConflict` → HTTP 409。
9. **密钥只走请求头。** `X-DeepSeek-API-Key` 不得写入数据库、日志、源码或构建产物。
10. **模型网络请求期间不得持有 SQLite 写事务。**
11. **文件所有权。** 见 `docs/architecture.md` 第 6 节；不同子智能体不得编辑同一文件。
12. **新增或修改 API 必须同步更新 `docs/architecture.md` 第 4 节的契约表。**

## 工作流程

修改代码前：

1. 读 `docs/requirements.md`。
2. 读 `docs/architecture.md`。
3. 检查相关模块的现有测试。
4. 输出 implementation plan，包含文件所有权、接口契约与验收标准。

修改完成后：

1. 运行 `python -m pytest`（离线可运行）。
2. 运行适用的静态检查：`python -m compileall -q backend`。
3. 检查接口兼容性：前后端字段名、枚举值、`expected_version` 与错误格式。
4. 输出：修改文件列表、实际验证结果、未解决限制与 `TODO-DL-x`。

## 验证边界

- **可离线验证**：`python -m pytest`、`python -m compileall -q backend`、模块导入、
  `python -m backend.scripts.smoke`（端到端演练）、LibreOffice PDF 导出（本机已安装）。
- **不可验证（记为 TODO-DL-3）**：`npm install`、`npm run build`、前端浏览器主流程。
  前端子智能体必须做到「源码完整 + 逐文件人工核对」，并在交付说明中明确说明未经构建验证。
