# 法医尸表检验文书整理 Agent

一套「委托书 + 手写记录本 → 规范检验文书」的整理工作台。前后端分层、乐观锁与审计设计
复用自 [`Tendertale/hotspot-workbench`](https://github.com/Tendertale/hotspot-workbench)。

> 导入委托书和记录本 → 识别原文 → 提取结构化信息 → 提示疑点 → 按认可的术语整理检验描述
> → 填入既有模板 → 检查内容与排版 → 输出供专业人员复核的文书。

**系统只做记录整理、规范表达和模板填充。检验事实来自本案材料或用户补充；需要写入的鉴定意见
由专业人员提供。系统不会自行形成鉴定结论。**

## 怎么用（普通用户：只有「上传」和「结果」两件事）

打开工作台后默认就是**整理**页：

1. **上传**：把委托书/记录本扫描件（PDF/图片）、部位照片、Word 模板等一起拖进去；
   每份文件的类型默认「自动判断」（只按文件名与扩展名推断，**系统不读取照片内容**），可一键改；
   选「部位照片」时可指定它属于哪个部位（不选则按文件名推测，之后可在案件记录里改）。
2. **选输出格式**：Word / PDF / 两者都要。
3. **点「开始整理」**——系统在后台完成建案 → 识别 → 提取 → 术语整理 → 填充模板 → 校核，
   然后**直接给出结果**：成品下载按钮（Word/PDF）、字段/损伤/疑点/阻断摘要、自动归类说明。

想核对细节（原文来源、逐条损伤、照片位置、校核清单、修正记录）就点结果页的
**「查看案件记录」**进入次级工作台；日常使用不需要先填这些。

命令行等价物：`POST /api/quick-run`（一次调用跑完整条链路，见 [`docs/architecture.md`](docs/architecture.md) 第 4 节）。

<details>
<summary>进阶：四步复核流程（次级界面「案件记录」里的能力）</summary>

1. **选择材料** —— 登记委托书、记录本与模板；**为每张部位照片选择目标位置**
2. **核对疑点** —— 逐字段核对原文、来源、识别结果与规范文字，处理校核清单
3. **查看文档** —— 生成 Word 草稿与 PDF 预览，检查排版与照片栏位
4. **确认修改** —— 录入识别/术语/格式/顺序四类修正，重新生成并保留版本

</details>

## 部位照片（新增能力）

用户可上传针对具体部位的照片（全身、头、面部、颈、胸、腹、背、腰臀、会阴、左右上肢/手/下肢/足、其他），
**并为每张照片指定目标位置**；生成文稿时按 `{{ photo_<位置> }}` 锚点插入模板对应栏位。

> **系统全程不读取照片内容**：不做 OCR、不做图像识别/描述/推断、不调用任何视觉模型。
> 照片只当文件路径处理，按你选的位置放到对应栏位（N-11/INV-11）。
> 模板声明了某个栏位而本次没有照片时，正文写“（未提供照片）”，不删栏位、不挪用其它照片（N-12）。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `PATCH` | `/api/cases/{id}/materials/{material_id}/placement` | 为照片指定/调整目标位置与顺序 |
| `GET` | `/api/cases/{id}/photo-slots` | 照片栏位视图（已指派/未指派、`missing`） |
| `GET` | `/api/cases/{id}/materials/{material_id}/file` | 原件预览（字段核对原图、照片缩略图） |

## 技术栈

| 层 | 技术 |
| --- | --- |
| 前端 | Vue 3 组合式 API、Vite、Tailwind CSS、lucide-vue-next |
| 后端 | Python 3.10+、FastAPI、Pydantic v2 |
| 存储 | SQLite（唯一持久化），原件/副本/文稿分目录 |
| 文档 | python-docx + Jinja2（内置渲染器）；docxtpl 为可选增强（TODO-DL-1） |
| PDF | 本机 LibreOffice 导出；PyMuPDF / pypdfium2 读取 |
| 识别 | 可插拔 OCR 引擎：`sidecar`（离线，默认）/ `paddle`（TODO-DL-2） |

## 启动（当前环境即可运行）

```powershell
# 1) 可选：把演示案件跑完整（字段/损伤/Word/PDF/校核清单/部位照片），便于立刻看到成品
python -m backend.scripts.demo_pipeline          # 加 --force 可重新生成一版

# 2) 启动服务（uvicorn，已装；默认端口 9765）
python -m backend.scripts.serve                   # 等价：--port 9765
#    python -m backend.scripts.serve --port 8123   # 需要别的端口时显式指定

# 3) 浏览器打开工作台
#    http://127.0.0.1:9765          ← 无构建依赖的原生工作台（frontend/static/**）
#    http://127.0.0.1:9765/api/health → {"status":"ok","service":"forensic-doc-agent"}
```

**前端说明（重要）**：本机**无法离线安装 npm 依赖**（`pnpm install --offline` 报
`ERR_PNPM_NO_OFFLINE_META`：缓存中没有 vue@3/vite/@vitejs/plugin-vue/tailwindcss@3 等），
且本项目禁止联网下载，因此 `frontend/src/**` 的 Vue 3 + Vite 源码**无法构建**（TODO-DL-3）。
后端在 `frontend/dist/index.html` 不存在时会直接提供 `frontend/static/**` —— 一份
**零依赖、零构建、纯 ES 模块 + fetch** 的工作台，覆盖同一套四步主流程与全部契约端点；
有网络后执行下面的构建命令，Vue 版本会自动接管 `/`。

```powershell
Set-Location frontend; npm install; npm run build; Set-Location ..   # 需要联网（TODO-DL-3）
```

Vue 开发模式：先启动后端，再 `cd frontend; npm run dev`（<http://127.0.0.1:5173>，已配置 `/api` 代理）。

## 配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `FORENSIC_DB_PATH` | `data/cases.db` | SQLite 路径，便于隔离测试数据库 |
| `FORENSIC_STORAGE_ROOT` | `storage` | 案件材料与文稿根目录 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容接口地址 |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | 默认模型 |
| `LIBREOFFICE_BIN` | 自动探测 | LibreOffice 可执行文件路径 |
| `FORENSIC_BASIC_AUTH` | 空（不启用） | 形如 `用户名:口令`；设置后全站要求 HTTP Basic 凭据（`/api/health` 免校验）。对外暴露前必须设置，详见 [`docs/DEPLOY.md`](docs/DEPLOY.md) |

模型密钥只在当前页面内存中保存，通过 `X-DeepSeek-API-Key` 请求头传给后端；
**不写入数据库、日志、源码、浏览器持久存储或构建产物**。

## 主要 API

完整契约见 [`docs/architecture.md`](docs/architecture.md) 第 4 节。核心端点：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/health` | 健康检查 |
| `GET` | `/api/config` | 字段目录、枚举字典、引擎与 LibreOffice 状态 |
| `GET` | `/api/stats` | 统计 |
| `GET` | `/api/cases` | 服务端分页、搜索、筛选案件 |
| `POST` | `/api/cases` | 建案 |
| `GET` | `/api/cases/{id}` | 详情（材料/字段/损伤/文稿/清单/事件/修正） |
| `POST` | `/api/cases/{id}/materials` | 登记材料 |
| `POST` | `/api/cases/{id}/ocr` | 运行识别 |
| `POST` | `/api/cases/{id}/extract` | 字段与损伤提取 |
| `PATCH` | `/api/cases/{id}/fields/{key}` | 核对/确认字段 |
| `POST` | `/api/cases/{id}/normalize` | 术语整理 |
| `POST` | `/api/cases/{id}/documents` | 生成文稿（`.docx` + `.pdf`） |
| `POST` | `/api/cases/{id}/check` | 运行校核 |
| `POST` | `/api/cases/{id}/corrections` | 记录并应用用户修正 |
| `POST` | `/api/cases/{id}/deliver` | 保存交付 |

所有写操作都必须带 `expected_version`。版本过期返回 **409**，避免旧结果覆盖新操作。

## 测试与验证

```powershell
python -m pytest                                   # 后端自动测试（全部离线，用例在 backend/tests/）
python -m compileall -q backend                    # 静态语法检查
python -m backend.scripts.smoke                    # 离线端到端演练（含照片栏位与排版断言）
python -m backend.scripts.demo_pipeline            # 把演示案件跑完，写入 data/cases.db
python -c "from backend.app.docgen import docx_engine_status, detect_libreoffice; print(docx_engine_status(), detect_libreoffice())"
python -c "from backend.app.ocr import engine_status; print(engine_status())"
```

`smoke.py` 会真实生成 `.docx`/`.pdf` 并断言：内联图片数 == 已指派且文件存在的照片数、
正文无占位符残留、空白字段一律“未记录”、正文不含“未见异常”。评审报告见 `docs/reviews/`。

## 文档索引

| 文件 | 内容 |
| --- | --- |
| [`docs/requirements.md`](docs/requirements.md) | 需求规格与验收编号（`R-*` / `N-*` / `A-*`） |
| [`docs/architecture.md`](docs/architecture.md) | 架构、数据模型、不变量、API 契约、模块签名、文件所有权 |
| [`docs/agent-prompts.md`](docs/agent-prompts.md) | 开发 / Review / 测试子智能体提示词设计 |
| [`docs/TODO_DOWNLOADS.md`](docs/TODO_DOWNLOADS.md) | 需要下载才能启用的能力（`TODO-DL-*`） |
| [`docs/reviews/`](docs/reviews/) | 评审与独立验证报告 |
| [`AGENTS.md`](AGENTS.md) | 项目开发规则与硬性约束 |

## 待办（需要联网下载）

见 [`docs/TODO_DOWNLOADS.md`](docs/TODO_DOWNLOADS.md)。当前环境**未安装**：
`docxtpl`（Word 模板填充，有内置等价回退）、`paddleocr`（真实手写识别）、
前端 npm 依赖（前端源码完整但未构建）、`pypdfium2`（已有 PyMuPDF 替代）。

在完成实样测试之前，**不承诺识别率或节省时间**。
