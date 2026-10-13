# 待办：需要联网下载的内容

本文件记录**所有需要下载才能启用**的能力。按当前约束（跳过一切需要等待下载的操作），
这些能力一律：

1. **不在本次执行安装**；
2. 在代码中以**预留接口 + 可用性探测（`available()`）** 的形式存在；
3. 缺少时系统**降级运行**，并在校核清单（`checklist_items`）或 `GET /api/config`
   中**显式报告**，不静默失败、不伪装成成功。

| 编号 | 内容 | 用途 | 安装命令 | 未安装时的行为 | 预留接口 |
| --- | --- | --- | --- | --- | --- |
| TODO-DL-1 | `docxtpl` | 需求 R-24 指定的 Word 模板填充方案（Jinja2 语义） | `python -m pip install docxtpl` | 自动回退到内置渲染器 `builtin`（python-docx + Jinja2），功能等价；`GET /api/config` 报告 `docx_engine: "builtin"` | `backend/app/docgen.py::render_document(..., engine="docxtpl")` |
| TODO-DL-2 | `paddleocr` + `paddlepaddle` | 需求 R-20/R-21：中文印刷与手写 OCR（PP-OCRv5） | `python -m pip install paddleocr paddlepaddle` | OCR 引擎 `paddle` 的 `available()` 返回 `False`；调用时抛 `OcrEngineUnavailable`，API 返回 409 + 提示改用 `sidecar` 引擎或配置引擎 | `backend/app/ocr/paddle.py::PaddleOcrEngine` |
| TODO-DL-3 | 前端 npm 依赖（`vue`、`vite`、`tailwindcss`、`lucide-vue-next`、`@vitejs/plugin-vue`、`autoprefixer`、`postcss`） | 前端构建与开发服务器 | `cd frontend; npm install` | Vue 3 源码完整可用但**无法执行 `npm run build` / `npm run dev`**；`frontend/dist` 不存在时后端在 `/` 提供 `frontend/static/` 的无构建工作台，API 全可用 | `frontend/src/api.js`（已冻结）；回退实现见 `frontend/static/**` |
| TODO-DL-4 | `pypdfium2` | 需求 R-18：PDF 文字读取与页面渲染为图像 | `python -m pip install pypdfium2` | 优先使用已安装的 `PyMuPDF`（`fitz`）；两者都不可用时 PDF 页面渲染跳过并记入校核清单 | `backend/app/imaging.py::render_pdf_pages()` |
| TODO-DL-5 | 云端 OCR（Azure Document Intelligence Read 等） | 需求第四章：与本地 PaddleOCR 对比选型 | 需要订阅与密钥 | 未接入；仅在 `docs/architecture.md` 保留 provider 扩展点 | `backend/app/ocr/__init__.py::register_engine()` |
| TODO-DL-6 | 真实样本与已审核文稿 | 需求 R-53：用代表性样本建立正确答案并配置模板/术语 | 由业务方提供 | 使用内置演示案件与程序化生成的演示模板；**不承诺识别率或节省时间** | `backend/app/seed.py::seed_demo_case()` |
| TODO-DL-7 | 真实部位照片样本 | 需求 R-54～R-58：验证照片栏位插入效果 | 由业务方提供 | 演示案件使用**程序化生成的纯色占位图**（不含真实人体内容）；系统只按用户指定的位置插入照片，不识别照片内容（N-11） | `backend/app/seed.py` 的照片占位图 + `frontend/static/` 照片区块 |

> **TODO-DL-3 实测结论（2026-10-12 探针，非文档推断）**：本机 Node v22.23.2 / npm 10.9.8 / pnpm 11 可用，
> 但 `pnpm install --offline` 失败（`ERR_PNPM_NO_OFFLINE_META`）：pnpm 元数据缓存中没有
> `vue@3`、`vite`、`@vitejs/plugin-vue`、`tailwindcss@3`、`postcss`、`autoprefixer`、`lucide-vue-next`；
> npm 缓存中只有 `vue@2.7.16` 与 `vite@8.x`，与 `frontend/package.json` 的依赖不匹配。
> 因此在“禁止联网下载”的约束下，Vue 3 源码既不能构建、也不能在浏览器中运行。
> 为此新增 `frontend/static/`：**无构建依赖、纯 ES 模块 + fetch** 的工作台，由 FastAPI 在 `/` 直接提供
> （见 `docs/architecture.md` 第 7.3 节），四步主流程与全部端点一致，是当前唯一可实际运行的前端。
> Vue 3 源码仍按契约补全，供将来有网络时执行 `npm install && npm run build`。


## 不受影响、当前即可完整运行的部分

以下能力**不需要下载**，本次交付已实现并可实际验证：

| 能力 | 依赖 | 本机状态 |
| --- | --- | --- |
| FastAPI 后端、SQLite 存储、版本乐观锁、审计事件 | `fastapi` / `pydantic` | 已安装 |
| 字段提取、术语规则引擎、校核规则 | 纯标准库 | 已安装 |
| Word 生成（内置渲染器） | `python-docx` + `Jinja2` | 已安装 |
| 图像预处理（旋转/纠偏/裁切/对比度） | `Pillow` | 已安装 |
| PDF 页面渲染 | `PyMuPDF` | 已安装 |
| PDF 导出 | 本机 LibreOffice（`C:\Program Files\LibreOffice\program\soffice.exe`） | 已安装 |
| 后端自动测试 | `pytest` / `httpx` | 已安装 |

## 解除待办后的验证步骤

```powershell
# TODO-DL-1
python -m pip install docxtpl
python -c "from backend.app.docgen import docx_engine_status; print(docx_engine_status())"   # 期望 engine=docxtpl

# TODO-DL-2
python -m pip install paddleocr paddlepaddle
python -c "from backend.app.ocr import get_engine; print(get_engine('paddle').available())"   # 期望 True

# TODO-DL-3
Set-Location frontend; npm install; npm run build; Set-Location ..
# 期望生成 frontend/dist/index.html，后端启动后 http://127.0.0.1:9765 返回工作台页面

# TODO-DL-4
python -m pip install pypdfium2

# 全量回归
python -m pytest
```
