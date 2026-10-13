# 交付说明（Delivery Notes）

版本：v1（本轮回合的收口记录）
日期：2026-10-13
交付范围：需求整合稿（`法医尸表检验文书Agent_整合稿.md`）的全部执行链路 + 用户新增的三项要求：
**前端可调用并取回文件**、**文档内文本与图片排版合理**、**部位照片按指定位置插入且全程不读取图片内容**。

---

## 1. 怎么开始用（本机实测可用）

```powershell
# 1) 准备演示案件（可跳过；也可 --force 重新生成一版）
python -m backend.scripts.demo_pipeline

# 2) 启动服务（默认端口 9765；后台运行中：http://127.0.0.1:9765）
python -m backend.scripts.serve

# 3) 浏览器打开工作台，按四步走：选择材料 → 核对疑点 → 查看文档 → 确认修改
#    http://127.0.0.1:9765/
```

四步都在同一个页面完成，产物通过「文稿」区块的下载按钮取回：
`GET /api/documents/{id}/download?fmt=docx|pdf`（文件名 `<案件编号>-report-v<N>.docx/.pdf`）。

**前端说明**：本机无法离线安装 npm 依赖（实测 `pnpm install --offline` → `ERR_PNPM_NO_OFFLINE_META`，
缓存中没有 `vue@3`/`vite`/`@vitejs/plugin-vue`/`tailwindcss@3` 等），且约束禁止联网下载，
因此后端在 `frontend/dist` 不存在时直接提供 `frontend/static/**`——**零依赖、零构建**的原生
ES 模块工作台（42 个端点全覆盖）。Vue 3 + Vite 源码按契约补全（15 个 `.vue`），
有网络后 `cd frontend; npm install; npm run build` 即自动接管 `/`（TODO-DL-3）。

---

## 2. 本次交付的产物（均已实际生成并检查）

| 文件 | 说明 |
| --- | --- |
| `storage/cases/FY-2026-0002/documents/FY-2026-0002-report-v9.docx` | 演示案件报告草稿（Word）：5 页、18 个照片栏位、3 张已指派照片按位插入、15 个空栏位写“（未提供照片）” |
| `storage/cases/FY-2026-0002/documents/FY-2026-0002-report-v9.pdf` | 由同一版 `.docx` 经 LibreOffice 导出（同版本号，R-51/INV-8） |
| `storage/cases/FY-2026-0002/documents/FY-2026-0002-report-v9.render.txt` | 生成时正文快照（供 `DOCUMENT_TEXT_DIVERGED` 判定文稿是否被系统外改动） |
| `docs/reviews/*.md` | 三份评审报告（需求覆盖与红线 / 后端契约与并发 / 前端与领域） |
| `backend/tests/**` | 190 个自动化用例（183 passed + 7 已转 XPASS），全部离线、`tmp_path` 隔离 |
| `docs/DELIVERY_NOTES.md` | 本文件 |

---

## 3. 实际验证结果（全部为真实运行输出）

| # | 命令 | 结果 |
| --- | --- | --- |
| 1 | `python -m compileall -q backend` | exit 0 |
| 2 | `python -m pytest -o addopts="" --no-header -q` | **183 passed, 7 xpassed, 0 failed**（1 warning 为 starlette 自带 httpx 弃用提示；用例全部来自 `backend/tests/`，`_ref_hotspot` 命中 0） |
| 3 | `python -m backend.scripts.smoke` | exit 0「演练全部通过」：OCR 18 块 / 照片跳过 4 张且 `ocr_blocks=0`；字段 11、损伤 2、冲突 1；`.docx` `inline_shapes=3` == 已指派可用照片数；18 个照片栏位、占位 15 处 == `/photo-slots` missing_count；正文无 `{{`/`{%` 残留、无“未见异常”；PDF 5 页；**含经 HTTP + 真实版本号的写操作全链路（13/13 步 200）与“过期版本必须 409”负向校验** |
| 4 | `python -m backend.scripts.demo_pipeline --force` | exit 0；生成 `report v9`（段落 97、表格 1、内联图片 3、照片栏位 18、占位 15） |
| 5 | `python _probe_out/verify_layout.py <docx> <pdf> --slots-json ...` | PASS：`image_count=3`、`image_paragraphs=3`、`photo_placeholder_count=15`、`legacy_placeholder_found=[]`、`mislabel_found=false`、`layout_warnings=[]`、`problems=[]`、PDF `page_count=5`；图片宽度 `[11.0, 7.0, 7.0] cm`（宽幅/常规上限），居中（x0+x1=页宽） |
| 6 | `python _probe_out/verify_docx_content.py` | PASS：18 个照片栏位、15 处占位、1 处草稿标记、损伤行序 `1. 左前臂 → 2. 右小腿`、`尸长172 cm。`、`体重：未记录`、`角膜：未记录`、无“未见异常”、无占位符残留、**正文不含校核清单区块**（R-46） |
| 7 | `python _probe_out/verify_w2.py` | PASS：状态推断五档、逐条损伤不合并、跨来源冲突、术语硬性规则、规则优先级、`3` 不变 `3.0`、照片内核兜底 |
| 8 | `python _probe_out/final_acceptance.py` | PASS：`/api/config` 含 `stage_transitions`/`reference_standards(3)`/`standard_config_confirmed=false`/`photo_positions(18)`/`engines`/`todo_downloads`；`engine="paddle"` 显式选择 → **409** 且 detail 提示改用 sidecar；连续 `run_check` 后 open 项 **30 → 30 → 30**（不再累积）；照片 `ocr_blocks=0` |
| 9 | `Invoke-WebRequest http://127.0.0.1:9765/` | 200 `text/html` 4619 B（工作台，含“部位照片”红线提示）；`/api/health` → `{"status":"ok","service":"forensic-doc-agent"}`；`/static/workbench.js` 200 173716 B；`/api/cases` 返回 2 个演示案件 |

> 所有图片相关的检查都只读取**几何信息**（图片数量、段落顺序、宽高、PDF 图片矩形），
> **全程没有查看、识别或推断任何照片像素内容**（N-11）。

---

## 4. 三轮评审缺陷的处置结果

评审 A（需求覆盖与红线）：`docs/reviews/review-requirements-coverage.md`；
评审 B（后端契约 + 前端）：`docs/reviews/review-backend-contract.md`、`docs/reviews/review-frontend-domain.md`；
测试（W5）：`backend/tests/W5_TEST_REPORT.md`。

| 编号 | 缺陷 | 处置 |
| --- | --- | --- |
| D1 | `POST /extract` 恒定 409（入口 claim 后又用过期版本写字段） | **已修复**：入口 claim 之后的内部写一律传 `expected_version=0`；HTTP 真实版本号 13/13 步 200 |
| D2/B1 | `POST /materials` 恒定 422（先 pop 版本再交必填模型校验） | **已修复**：先带版本一起校验；`commission`/`record_book`（体/查询参数）/`photo` 三种上传实测 200 并落库 |
| D3 | 跨段 `{% for %}` 渲染整体倒序（损伤顺序被颠倒） | **已修复**：锚点在整次循环内贯穿；正文顺序与 `seq` 一致（含嵌套/表格/内联边界实测） |
| D4 | `pending_issue` 必然携带【待核对】与自家 checker 冲突 | **已修复**：只有 `internal_draft` 写草稿标记；`DOCUMENT_DRAFT_MARKER` 仍为 blocking 且互斥自洽 |
| D5 | approve/deliver 绕过 R-28 阻断门 | **已修复**：有 open blocking 时 `approve` → 409（豁免后才可审核）；`deliver` 只接受 `approved` |
| D6 | 模板声明但无字段行的栏目渲染空白（N-5 缺口） | **已修复**：标量栏目空值写“未记录”，`negative` 保留否定词 |
| D7 | 演示模板 14 个照片锚点 vs 位置目录 18 项（口径不闭合） | **已修复**：模板改为按 `PHOTO_POSITION_KEYS` 生成 18 个栏位；文稿占位 15 处 == `missing_count=15`；演示案件模板副本由 `demo_pipeline` 补登新副本（原件只读，N-8） |
| D8 | 默认 `sidecar` 引擎未走 N-11 内核兜底 | **已修复**：兜底移到引擎注册表出口（`GuardedOcrEngine`），任意引擎（含第三方）对 `kind="photo"` 一律抛 `OcrEngineUnavailable` 且**不读取任何文件** |
| D9 | smoke 全传 `expected_version=0`，掩盖装配缺陷 | **已修复**：smoke 新增真实 HTTP + 真实版本号全链路段与过期版本 409 负向校验 |
| D10 | R-49 标准配置未落地 | **已修复**：新增 `constants.REFERENCE_STANDARDS` / `STANDARD_CONFIG_CONFIRMED`，`/api/config` 报告并注明“采用与否由业务方确认”，系统不得据标准生成内容 |
| D11 | 建案写入的 `confirmed_value` 永久压过识别值且静默 | **已修复**：确立单一优先级（`confirmed_value` → `normalized_text` → `raw_text`），识别结果不覆盖用户已确认值、原文完整保留，并写 `field_value_priority` 事件 |
| D12/D13/D15 | claim 顺序、死分支、标签叠字 | **均已修复**（`尸长：尸长172 cm。` → `尸长172 cm。`） |
| D14 | 前端误报“opinion 无法落库” | **已修复**：`CaseUpdate.opinion_text` 已声明，前端改为正常 PATCH + 真实错误提示 |
| D16 | `CorrectionCreate.target_id=0` 返回 404 | **保留**：契约 §4.2 将 `LookupError` 映射为 404（“资源不存在”），语义自洽，已在报告中说明，不视为缺陷 |
| F1/F3 | 前端两处写操作漏 `expected_version`；`GET /` 缺“部位照片”字样 | **已修复**：全组件写调用自查 0 违规；静态页补充红线提示 |
| W5-③ | `run_check` 不加载 `ocr_blocks` → `PHOTO_CONTENT_READ`/`OPINION_FROM_SOURCE` 在真实链路不可达 | **已修复**：`run_check` 现把 `ocr_blocks` 交给 checker，两条安全网用例转 XPASS |

修复后复评结论：**blocking 全部关闭**；测试 `183 passed / 7 xpassed / 0 failed`；
`smoke` 与 `demo_pipeline` 端到端产出真实 `.docx`/`.pdf`。

---

## 5. 契约变更记录（主 Agent 所有，已同步文档）

| 文件 | 变更 |
| --- | --- |
| `backend/app/constants.py` | `MATERIAL_KINDS` 增 `photo`；新增 `OCR_MATERIAL_KINDS`、`PHOTO_MATERIAL_KIND`、`PhotoPosition` 与 18 项 `PHOTO_POSITION_DEFINITIONS`（含 `anchor`/`wide`）、`PHOTO_POSITION_KEYS/LABELS/ANCHORS`、`PHOTO_SECTIONS`、`PHOTO_MAX_WIDTH_CM=7.0`、`PHOTO_WIDE_MAX_WIDTH_CM=11.0`、`PHOTO_MAX_PER_POSITION=8`、`PHOTO_PLACEHOLDER_TEXT="（未提供照片）"`、`REFERENCE_STANDARDS`、`STANDARD_CONFIG_CONFIRMED`；`DEFAULT_SECTION_ORDER` 增 `photos` |
| `backend/app/schemas.py` | `MaterialCreate` 增 `expected_version`（≥1）、`position_key`、`photo_caption`（位置合法性校验：非照片材料不得指定位置）；新增 `PhotoPlacementUpdate`；`CaseUpdate` 增 `opinion_text`（N-1 唯一写入通道） |
| `frontend/src/api.js` | 新增 `materialFileUrl`、`updatePhotoPlacement`、`photoSlots` |
| `docs/requirements.md` | 新增红线 N-11（不读图片内容）、N-12（空栏位写占位且不顶替）、R-54～R-58（照片链路与前端入口） |
| `docs/architecture.md` | §1 目录与存储布局（`frontend/static/**`、`*.render.txt`）、§2 `materials.position_key/photo_caption` 与 `case_fields.source_kind`、INV-11、§4 新增 3 个端点与 `/api/config`/`photo-slots` 实测形状、§4.2 增 `ValueError→400`、§5.2 新增 4 个方法、§5.4 引擎选择语义裁决、§5.7 新增 4 个 `PHOTO_*` code 与 `check_format(rendered_snapshot=)`、§5.8 照片锚点渲染与快照、§5.10 阶段口径、§6 所有权（W4b）、§7.3 无构建工作台、§9/§10 验收与 DoD |
| `docs/TODO_DOWNLOADS.md` | 新增 TODO-DL-7（真实部位照片）；补记 TODO-DL-3 的**实测**离线安装结论 |
| `docs/IMPLEMENTATION_PLAN.md` | 重写为 v2（Wave 0—3、文件所有权、验收标准、风险与 TODO 记账） |
| `docs/agent-prompts.md` | 新增 §6 附录（N-11/N-12 禁令与可运行前端说明） |
| `README.md` | 新增快速开始（demo_pipeline + serve + 工作台 URL）、部位照片说明、前端离线结论 |

---

## 6. TODO-DL 记账（未解除任何一条）

| 编号 | 内容 | 当前降级路径 |
| --- | --- | --- |
| TODO-DL-1 | `docxtpl` | 内置 `builtin` 渲染器（python-docx + Jinja2）；`/api/config.engines.docx.preferred="builtin"`、`todo="TODO-DL-1"` |
| TODO-DL-2 | `paddleocr`+`paddlepaddle` | 离线 `sidecar` 引擎；**显式选择 `paddle` → 409 + 提示改用 sidecar**（不再静默回退）；默认/`auto` 才回退并在 payload 报告 |
| TODO-DL-3 | 前端 npm 依赖 | `frontend/static/**` 无构建工作台（已 HTTP 实测）；Vue 源码完整但**未构建、未浏览器验证** |
| TODO-DL-4 | `pypdfium2` | PyMuPDF 渲染 PDF 页面 |
| TODO-DL-5 | 云端 OCR | 仅保留 `register_engine()` 扩展点 |
| TODO-DL-6 | 真实样本与已审核文稿 | 内置演示案件 + 程序化演示模板（`demo_data: true`），**不承诺识别率或节省时间** |
| TODO-DL-7 | 真实部位照片 | 演示用程序化纯色占位图（合成图，非证据） |

---

## 7. 未解决限制与已知遗留（如实记录）

1. **Vue 3 源码未构建、未在浏览器运行**（TODO-DL-3）：`frontend/src/**` 只做了 `<script setup>` 抽取后
   `node --check`（16/16）、组件结构/props/emits/api 方法静态自查与接口形状核对；SFC 模板编译、
   Tailwind 类名生成、`lucide-vue-next` 图标名、运行时交互、可访问性均未验证。
2. **浏览器交互未端到端实测**：环境无 npm 依赖与无头浏览器。工作台已做 HTTP 级验证（`GET /`、静态资源、
   `/api/health`、`/api/config`、`/api/cases`、`/api/cases/{id}/photo-slots`）与后端真实写链路验证（smoke [10] 段），
   但“点击上传文件 → 生成 → 下载”的人工点击流程需使用者确认。
3. **乐观锁是约定级而非事务级**（评审 B3/B4）：入口 `claim()` 与后续写各用独立连接，属 best-effort；
   `expected_version=0` 表示“系统写、跳过校验”，新增路由若漏传版本会静默通过。彻底收敛需引入
   `Database.transaction()` / 显式哨兵（属契约变更，本轮未做）。
4. **`PATCH /fields/{key}` 修改 `confirmed_value` 时未把 `review_state` 打回 `pending`**（评审 B5），
   与 D11 优先级规则需一起定语义，本轮未改。
5. **`_migrate` 只覆盖 `materials` 两列**（评审 B9）；其他表将来加列需要各自 DDL。
6. **校正项 `target_id=0` 返回 404**（评审 D16）：按契约 §4.2 属“资源不存在”，保留现状。
7. **无真实手写样本**（TODO-DL-6/7）：`sidecar` 引擎打通的是流程与红线；真实字迹识别率未经实测，
   **不承诺识别率**。`paddle` 选型需在实样上比较后再定。
8. `docs/reviews/**` 中的行号以评审当时快照为准；本说明第 3、4 节的结论均为修复后重跑所得。

---

## 8. 评审报告索引

| 文件 | 内容 |
| --- | --- |
| `docs/reviews/review-requirements-coverage.md` | 逐条 `R-*`/`N-*` 覆盖矩阵、红线证伪、缺陷清单（含复现命令） |
| `docs/reviews/review-backend-contract.md` | 11 张表列名、INV-1～INV-11、42 个端点与状态码、乐观锁覆盖、密钥边界、签名一致性（§5 签名 123/123、§4 端点 42/42） |
| `docs/reviews/review-frontend-domain.md` | `frontend/static/**` 与 Vue 源码逐项核对、N-5/N-11/N-12/R-15 合规、TODO-DL-3 未验证项 |
| `backend/tests/W5_TEST_REPORT.md` | 190 个用例的 R-/N- 覆盖矩阵与实现缺陷记录 |
