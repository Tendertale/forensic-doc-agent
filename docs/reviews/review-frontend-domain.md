# 评审报告（W6 评审 B）· 前端与领域契约

- 评审对象：`frontend/static/**`（W4b，当前唯一可实际运行的前端）与 `frontend/src/**`（W4，Vue 3 源码）
- 评审基线：`docs/architecture.md` §4 / §7.1 / §7.2 / §7.3、`docs/requirements.md`（R-1、R-15、R-44、N-1、N-5、N-11、R-44、R-54～R-58）、`AGENTS.md`
- 评审方式：**只读**静态核对 + 实际启动后端并用 `Invoke-WebRequest` 验证静态资源
- **构建与浏览器验证未执行（TODO-DL-3）**：Vue 3 源码未经 `npm install` / `npm run build`，未在任何浏览器中渲染或点击验证
- 评审日期：2026-10-13

---

## 1. 方法与证据来源

### 1.1 实际执行过的命令

| # | 命令 | 结果 |
| --- | --- | --- |
| 1 | `node --check frontend/static/api.js` | 退出码 0 |
| 2 | `node --check frontend/static/workbench.js` | 退出码 0 |
| 3 | `node --check frontend/src/api.js` | 退出码 0 |
| 4 | `node --check frontend/src/main.js` | 退出码 0 |
| 5 | `node --check frontend/vite.config.js` / `tailwind.config.js` / `postcss.config.js` | 退出码 0（均为根目录配置文件） |
| 6 | `python -m backend.scripts.serve --port 8021 --db %TEMP%\w6_serve\serve.db --storage %TEMP%\w6_serve\storage --no-seed` | 服务正常启动（日志见 `%TEMP%\w6_serve.log`） |
| 7 | `Invoke-WebRequest`：`GET /` | **200**，4452 B（= `frontend/static/index.html` 字节数），`text/html; charset=utf-8` |
| 8 | `Invoke-WebRequest`：`GET /static/workbench.js` | **200**，173906 B（与磁盘字节数一致） |
| 9 | `Invoke-WebRequest`：`GET /static/api.js` / `GET /static/workbench.css` | **200**，6888 B / 19423 B |
| 10 | `Invoke-WebRequest`：`GET /api/health` | **200**，`{"status":"ok","service":"forensic-doc-agent"}`（46 B） |
| 11 | `Invoke-WebRequest`：`GET /api/config` | **200**，15310 B（含 `photo_positions`、`photo_sections`、`engines`、`todo_downloads`、`demo_data`） |
| 12 | `Invoke-WebRequest`：`GET /` 关键字断言 | `选择材料=True 核对疑点=True 查看文档=True 确认修改=True 系统不读取照片内容=True 未记录=True`；**`部位照片=False`**（见缺陷 F3） |
| 13 | `Invoke-WebRequest`：`POST /api/cases/1/materials`（体带 `expected_version`） | **422** `{"detail":"expected_version Field required"}`（后端缺陷，见报告 `review-backend-contract.md` B1；前端请求本身符合契约） |
| 14 | `grep`（端点清单 / `localStorage` / 外链 / `未见异常` / `未记录` / `不读取照片` / `专业人员` / `409` / `expected_version` / 图像 API / `import`） | 见各节 |
| 15 | `Select-String` 提取 15 个 `.vue` 的 `defineProps`/`defineEmits` 键名，并提取 `App.vue` / `CaseWorkspace.vue` 的组件标签属性逐一比对 | 见 §3.2 |
| 16 | 对比 `frontend/src/api.js` 与 `frontend/static/api.js` 的方法名集合 | 两者均为 **42 个方法，集合完全相同** |

> 端口 8021 的服务已在验证后停止：`job_kill` 后 `TCP connect 127.0.0.1:8021` 失败（端口已释放）。

### 1.2 未执行的验证（明确声明）

- ❌ `npm install` / `npm run build` / `npm run dev`（**TODO-DL-3**，禁止联网下载）
- ❌ 浏览器渲染、点击、键盘操作、可访问性实测（无构建产物 + 本次为只读评审，未启动浏览器）
- ❌ 真实 409 的前端交互（需要浏览器触发；本次用后端 `Invoke-WebRequest` 得到 409，再静态核对前端分支）
- ❌ `.vue` 单文件组件的编译校验（`node --check` 不适用于 `.vue`，改用「逐文件人工静态核对」：模板/脚本/样式块结构、`import` 白名单、`defineProps`/`defineEmits` 与父模板属性比对）

---

## 2. `frontend/static/**`（当前唯一可实际运行的前端）

### 2.1 启动与静态资源验证（实测）

| 项 | 结论 | 证据 |
| --- | --- | --- |
| 后端可启动并直接提供工作台 | ✅ | `GET /` **200 / 4452 B**（`backend/app/main.py:99-110`：`frontend/dist/index.html` 不存在 → 返回 `frontend/static/index.html`） |
| `/static` 资源可访问且字节数一致 | ✅ | `workbench.js` 200 / 173906 B、`api.js` 200 / 6888 B、`workbench.css` 200 / 19423 B |
| 无 CDN / 外链 | ✅ | `frontend/static/index.html` 只引用 `/static/workbench.css`（第 10 行）与 `/static/workbench.js`（第 78 行），图标为内联 SVG；`frontend/static/workbench.css:3` 与 `workbench.js:6` 声明不引用外部字体/CDN；`grep https?://` 在 `frontend/static/**` 无命中 |
| 语法正确 | ✅ | 命令 1/2 退出码 0 |

### 2.2 逐项核对表（§7.3 区块 + §4 端点 + 契约条款）

| 项 | 结论 | 文件:行号 | 证据 |
| --- | --- | --- | --- |
| 端点覆盖清单（§4 全 42 条） | ✅ | `frontend/static/workbench.js:26-69`（`ENDPOINT_MANIFEST`，42 条） | 人工逐条与 §4 表格比对：42/42 命中，含 `?expected_version=N` 的 DELETE/reorder 写法 |
| `expected_version` 位置符合 §4（POST/PATCH 在体、DELETE 在查询参数） | ✅ | `workbench.js:2358/2372/2404/2443/2482/2508/2530/2562/2587/2603/2625/2641/2662/2698/2733/2751/2772/2791/2808/2822/2843`（体）；`:36/52/53/58` 与 `frontend/static/api.js:78/110/114/126`（查询参数） | 逐项核对；DELETE 案件/损伤/规则与 `injuries/reorder` 均走 `buildQuery({expected_version})` |
| 规则类端点用**规则版本** | ✅ | `workbench.js:1966`（按钮文案「expected_version = 规则版本 N」）、`:2005`（「expected_version 是规则版本，不是案件版本」）、`:2909/2938`（`expected_version: num(rule.version, 1)`） | 代码核对；`frontend/static/api.js:123-126` 中 `updateRule`/`deleteRule` 由调用方给规则版本 |
| 案件列表（分页/搜索/筛选/建案含 intake 表/删除） | ✅ | `workbench.js:26-68` 端点、`:3099` `bootstrap()`、`:978`「建案不需要 expected_version」提示 | 代码核对 + `GET /` 200 |
| 材料（FileReader→base64 上传、质量标记、预处理、运行 OCR、原图预览、识别结果分页） | ✅（实现）但 ❌（端到端） | `workbench.js:1102/1109`（`FileReader.readAsDataURL`）、`:1243`（上传控件说明）、`:2431-2459`（`material-upload`）、`:2474-2489`（`page-save`）、`:1195`（`<img>` 原图预览）、`:1215-1226`（OCR 块分页含置信度/bbox） | **实测 422**：前端在请求体带 `expected_version`（`workbench.js:2443`）完全符合 §4，但后端 `routes.py:536-559` 恒返回 422（详见后端报告 B1）→ 材料/照片上传在真实环境不可用 |
| 照片区块（上传、缩略图、位置下拉取 `/api/config` 的 `photo_positions`、placement、顺序调整） | ✅ | `workbench.js:497-512/574`（从 `/api/config` 取 `photo_positions`）、`:1285/1292`（下拉分组）、`:1306`（缩略图 `<img>`）、`:2551-2599`（`photo-upload`/`photo-save`）、`:2043/2097`（配置页列出 18 项） | `PHOTO_POSITIONS_FALLBACK`（18 项，key/label/section/order/anchor/wide）与 `constants.PHOTO_POSITION_DEFINITIONS` 逐项一致（`body_overall` 是唯一 `wide:true`） |
| 核对疑点：字段表 9 项来源同屏 | ✅ | `workbench.js:1404`（注释明确列出 9 项）、`:1447`（① 原文 raw_text）、`:1448-1452`（② 来源文件、③ 页码、识别块 `source_block_id`）、`:1453`（④ 原页位置 bbox）、`:1454`（⑤ 识别结果 confidence）、`:1455`（⑥ 确认值）、`:1456`（⑦ 规范化文字 normalized_text）、`:1457-1459`（⑧ 使用的规则）、`:1460`（⑨ 复核状态 review_state） | 逐行核对，9 项齐全（R-15） |
| `unclear`/冲突双方来源同屏（R-44） | ✅ | `workbench.js:1420-1427`（`conflict-box`：表头「取值/来源文件/页码/原页位置 bbox/识别结果/块」，标题「⚠ 冲突：不同来源取值不一致（R-44，未静默选择任何一方）」）、`:1432`（`badge-unclear`「待辨认/冲突」） | 代码核对；不静默择一 |
| 损伤逐条编辑 + 排序 | ✅ | `workbench.js:1549-1589`（字段编辑表）、`:2698-2703`（新增，`review_state: 'pending'`）、`:2625/2641`（更新/删除）、`:2662`（`reorder` 另见 `:53`） | 代码核对（R-16/R-36） |
| 术语整理：原文 ↔ 规范文字对应 | ✅ | `workbench.js:1633-1676`（表头「对象/原文/规范文字（当前）/替代规则/状态」） | 代码核对（R-4/R-30～R-37） |
| 校核清单处理/豁免 | ✅ | `workbench.js:1700+`（校核项）、`:2733/2751/2772`（`PATCH /api/checklist/{id}` 带案件版本） | 代码核对 |
| 查看文档（生成、版本列表、docx/pdf 下载、approve） | ✅ | `workbench.js:1797-1799`（版本行）、`:1860`（`expected_version = 案件版本` 提示）、`:2808/2822`（生成/审核）、`:144`（`downloadUrl`） | 代码核对 |
| 确认修改（四类修正 + `opinion` 手工录入 + 标注） | ✅ | `workbench.js:2843`（`createCorrection`）、`:1880`（「由专业人员提供」徽标）、`:1883-1885`（`data-opinion-input` textarea + 「只能由专业人员手工输入…系统不会从识别文本…」）、`:2404-2421`（`opinion_text` 保存 + toast「系统未做任何推断」） | 代码核对（N-1/R-38～R-41） |
| 409 统一提示 + 自动刷新 | ✅ | `workbench.js:391-406`（`handleApiError`：识别版本冲突 → toast「409 版本冲突」、`pushBanner` 附「立即刷新」按钮、`void refreshAfterConflict()`）、`:427`（`refreshAfterConflict`） | 代码核对；与 `architecture.md:858` 一致 |
| N-5 空白只显示「未记录」 | ✅ | `workbench.js:76`（`NOT_RECORDED='未记录'`）、`:238-290`（`valHtml`：空 → `<span class="value-empty">未记录</span>`）、`:1415/1449/1452/1584/1586/1797-1799` | 代码核对；`grep 未见异常` 命中的 4 处**全部是「不会写成『未见异常』」说明文字**（`:1507/1528` 等），无任何代码产出「未见异常」 |
| N-1 `opinion` 只手工输入 | ✅ | `workbench.js:1885`（唯一输入控件为 textarea）、`:1880/1883`（标注） | 代码核对；无「用识别结果填充 opinion」按钮（`:1418` 的批量填充排除 opinion —— 见 `:1441` 附近的 opinion 特判） |
| N-11/R-58 照片：位置只从目录选、标注、无图像识别 | ✅ | `workbench.js:1285/1292`（下拉项来自 `cfg.photo_positions`，无自由输入）、`:1161/1310/1371/2067/2101`（「系统不读取照片内容」）、`:1306`（缩略图仅 `<img>`） | `grep createImageBitmap\|getImageData\|canvas\|tesseract\|recognizeImage` **零命中**；`import` 只有 `./api.js`（`:18`） |
| 密钥仅内存、不进 `localStorage` | ✅ | `workbench.js:216`（`apiKey: ''  // 仅内存，绝不写 localStorage`）、`:3062`（「只写入内存变量；不落 localStorage，也不写 DOM 属性」）、`:2109`（配置页说明） | `grep localStorage\|sessionStorage` 在 `frontend/static/**` 只命中注释与说明文字，无赋值/读取调用 |
| 静态/源码两份 `api.js` 一致 | ✅ | `frontend/static/api.js:64-145` vs `frontend/src/api.js` | 命令 16：方法名集合完全相同（42 个） |
| §7.3「加载态 / 错误横幅」 | ✅ | `workbench.js:402-404`（banner + 立即刷新）、`index.html:29-30/63/76`（`conn-badge`/`loading-slot`/`banner-slot`/`#toasts`） | 代码核对 |

### 2.3 `frontend/static` 缺陷

#### F2 · blocking（成因在后端）· 材料上传/照片上传在真实后端上 100% 失败

- **所有者**：**W1 后端核心（子智能体 A）** → `backend/app/routes.py:536-559`（前端请求正确，无需改动前端；如需防御可让前端同时带上 `?expected_version=`，但那只是掩盖问题）。
- **证据**（实测，端口 8021）：
  ```
  POST /api/cases/1/materials
  body {"expected_version":1,"kind":"commission","filename":"live.pdf","media_type":"pdf","page_count":1}
  → 422 {"detail": "expected_version Field required"}
  GET /api/cases/1/materials → []（材料数 0）
  ```
  前端调用点：`frontend/static/workbench.js:2442-2450`（材料）与 `:2561-2570`（照片），两处都在请求体携带 `expected_version: currentVersion()`，**完全符合 `docs/architecture.md:194`**。
- **影响**：R-1 四步主流程第 1 步「选择材料」与 R-54/R-58 的「照片位置指派」在真实运行环境不可用（工作台 UI 会弹出 422 错误横幅）。用户看到的是前端报错，但根因在后端。
- **建议最小修复**：修后端（见 `review-backend-contract.md` B1）；前端不需要改。

#### F3 · minor · `docs/architecture.md:918` 的验收描述「`GET /` 应返回静态工作台页面（含“部位照片”区块）」与实际不符

- **所有者**：**主 Agent**（改文档）或 **W4b 前端（子智能体 E）**（改 `index.html`）。
- **证据**：`GET /` 返回 4452 B 的 `frontend/static/index.html`；关键字断言 `部位照片=False`。该区块由 `workbench.js` 在运行期注入 `#view-case`（`index.html:65` 的空 `<section>`），因此「页面源文件」不含该字符串。
- **影响**：纯验收脚本若按 `architecture.md:918` 断言 `GET /` 正文含「部位照片」，会误报失败。
- **建议最小修复**：把 `docs/architecture.md:918` 改为「`GET /` 返回工作台外壳；「部位照片」区块由 `workbench.js` 在 `#view-case` 中动态渲染」；或在 `index.html:52-59` 的「红线提示」里补一条「部位照片只按你选择的位置插入」的静态文本（当前已有第 57 行，但措辞不含「部位照片」四字）。

#### F4 · minor · 静态工作台把「未记录」写成 badge 而非可区分的空值语义（一致性观察，非缺陷）

- 说明：`workbench.js:238-290` 对空值统一输出「未记录」，对 `bbox` 缺失输出「无定位」（`:273`）。这与 §7.3「空白字段显示『未记录』」一致；「无定位」用于 bbox 属合理细分。**无需修改**，记录以备后续评审统一口径。

---

## 3. `frontend/src/**`（Vue 3 源码，静态核对）

### 3.1 组件树与 §7.2 对齐

| 项 | 结论 | 证据 |
| --- | --- | --- |
| 组件数量 | ✅ | `frontend/src/App.vue` + `frontend/src/components/` 下 **15 个 `.vue`**，与 §7.2 列出的 15 个组件一一对应（Sidebar / StatsCards / CaseTable / CreateCaseModal / CaseWorkspace / WorkflowStepper / MaterialPanel / PhotoPanel / FieldReviewPanel / InjuryPanel / TerminologyRulesPanel / DocumentPanel / ChecklistPanel / CorrectionModal / AISettingsModal） |
| `App.vue` 直接子组件 | ✅ | `App.vue:16-22` 导入 Sidebar、StatsCards、CaseTable、CreateCaseModal、CaseWorkspace、TerminologyRulesPanel、AISettingsModal；模板中均有对应标签与 `@` 事件绑定 |
| `CaseWorkspace.vue` 子组件 | ✅ | `CaseWorkspace.vue:20-28` 导入 WorkflowStepper、MaterialPanel、PhotoPanel、FieldReviewPanel、InjuryPanel、TerminologyRulesPanel、DocumentPanel、ChecklistPanel、CorrectionModal；与 §7.2 完全一致 |
| 无循环依赖 | ✅ | 依赖方向单向（App → CaseWorkspace → 叶子组件）；`TerminologyRulesPanel` 被 App 与 CaseWorkspace 复用但自身无子组件 |
| 仅依赖 `vue` / `lucide-vue-next` / 相对路径 | ✅ | 全量 `import` 扫描（15 个组件 + App）：仅 `from 'vue'`、`from 'lucide-vue-next'`、`from './api.js'`、`from '../api.js'`、`from './Xxx.vue'`。**无第三方包、无 `http(s)://` 外链**（`grep https?://` 在 `.vue` 中零命中），符合 `AGENTS.md` 技术栈约束 |

### 3.2 props / emits 自洽性

| 组件 | `defineProps` 键 | `defineEmits` | 父模板是否逐一传参 | 证据 |
| --- | --- | --- | --- | --- |
| Sidebar.vue | `activeView, mobileOpen, counts, aiEnabled, aiStatus, demoData` | `navigate, close, configure` | ✅ 6 props + 3 事件 | `App.vue` 模板 `<Sidebar :active-view :mobile-open :counts :ai-enabled :ai-status :demo-data @navigate @close @configure>` |
| StatsCards.vue | `stats, loading, dict` | （无） | ✅ 3 props | `<StatsCards :stats :loading :dict />` |
| CaseTable.vue | `items, total, page, pageSize, loading, filters, dict` | `filters, select, create, delete, refresh` | ✅ 7 props + 5 事件 | `<CaseTable :items :total :page :page-size :loading :filters :dict @filters @select @create @delete @refresh>` |
| CreateCaseModal.vue | `open, saving, error, dict` | `close, submit` | ✅ 4 props + 2 事件 | `<CreateCaseModal :open :saving :error :dict @close @submit>` |
| AISettingsModal.vue | `open, status, busy` | `close, verify` | ✅ 3 props + 2 事件 | `<AISettingsModal :open :status :busy @close @verify>` |
| CaseWorkspace.vue | `caseDetail, loading, dict, photoGroups, config` | `close, refresh, notify, error` | ✅ 5 props + 4 事件 | `<CaseWorkspace :case-detail :loading :dict :photo-groups :config @close @refresh @notify @error>` |
| WorkflowStepper.vue | `stageKeys, stageLabels, current, transitions, pending, version, updatedAt` | `change` | ✅ 7 props + 1 事件 | `CaseWorkspace.vue` 模板 `<WorkflowStepper :stage-keys :stage-labels :current :transitions :pending :version :updated-at @change>` |
| MaterialPanel.vue | `caseId, caseVersion, materials, dict` | `refresh, notify, error` | ✅ 4 + 3 | `<MaterialPanel :case-id :case-version :materials :dict @refresh @notify @error>` |
| PhotoPanel.vue | `caseId, caseVersion, materials, photoGroups, dict` | `refresh, notify, error` | ✅ 5 + 3 | `<PhotoPanel :case-id :case-version :materials :photo-groups :dict @refresh @notify @error>`（`:case-version="version"`） |
| FieldReviewPanel.vue | `caseId, caseVersion, fields, materials, dict` | `refresh, notify, error` | ✅ 5 + 3 | `<FieldReviewPanel :case-id :case-version :fields :materials :dict ...>` |
| InjuryPanel.vue | `caseId, caseVersion, injuries, materials, dict` | `refresh, notify, error` | ✅ 5 + 3 | `<InjuryPanel :case-id :case-version :injuries :materials :dict ...>` |
| DocumentPanel.vue | `caseId, caseVersion, caseDetail, documents, dict` | `refresh, notify, error, open-correction` | ✅ 5 + 4 | `<DocumentPanel ... @open-correction="showCorrection = true">` |
| ChecklistPanel.vue | `caseId, caseVersion, items, dict` | `refresh, notify, error` | ✅ 4 + 3 | `<ChecklistPanel :case-id :case-version :items :dict ...>` |
| CorrectionModal.vue | `open, caseId, caseVersion, fields, injuries, documents, dict` | `close, done, notify, error` | ✅ 7 + 4 | `<CorrectionModal :open :case-id :case-version :fields :injuries :documents :dict @close @done @notify @error>` |
| TerminologyRulesPanel.vue | `dict` | `notify, error` | ✅ 1 + 2（App 与 CaseWorkspace 两处均传 `:dict`） | `<TerminologyRulesPanel :dict="dict" @error @notify />` |

**结论**：props/emits 自洽，无「父传了子未声明」「子 emit 了父未监听」的情况（未发现未使用的 emit 声明之外的不一致；`CaseTable` 的 `delete`/`refresh` 均在 App 有监听）。

### 3.3 `api.js` 方法名存在性

- 全量扫描 `.vue` 中的 `api.<method>(` 调用共 **37 个不同方法**（`config, stats, listCases, case, createCase, deleteCase, verifyAi, rules, createRule, updateRule, deleteRule, addMaterial, updatePhotoPlacement, materialFileUrl, photoSlots, updateMaterialPage, preprocess, runOcr, ocrBlocks, injuries, createInjury, updateInjury, deleteInjury, reorderInjuries, updateField, createDocument, documents, approveDocument, deliver, downloadUrl, createCorrection, runCheck, checklist, updateChecklistItem, updateStage, runNormalize, updateCase`）。
- 与冻结的 `frontend/src/api.js`（42 个方法）逐一比对：**全部存在**，无拼写偏差；未被使用的 5 个为 `health`、`listMaterials`、`corrections`、`fields`（均通过 App↔CaseWorkspace 的数据流间接覆盖）+ `updateStage` 已用。
- `frontend/src/api.js` 与 `frontend/static/api.js` 方法名集合**完全相同**（命令 16）。

### 3.4 领域条款核对（Vue）

| 条款 | 结论 | 文件:行号 | 证据 |
| --- | --- | --- | --- |
| R-15 字段核对 9 项来源同屏 | ✅ | `FieldReviewPanel.vue:427-500` | ① 原文（:427-428）② 来源文件（:430-432）③ 页码（:434-438）④ 原页位置 bbox（:440-442）⑤ 识别结果 + 置信度说明「仅用于安排核对优先级」（:444-447）⑥ 确认后的值（:449-471）⑦ 规范化文字（:473-482）⑧ 使用的规则（:484-487）⑨ 复核状态（:489-499）——**9 项齐全** |
| R-44 `unclear` 醒目标记 + 冲突双方来源同屏 | ✅ | `FieldReviewPanel.vue:397`（`unclear` 时 `border-amber-400 bg-amber-50`）、`:408-411`（「待辨认/冲突，必须人工处理」）、`:503-521`（冲突框「来源冲突：必须显式处理，不能静默选择其中一方（R-44）」，逐条列出 `item.source` + `item.value`） | 代码核对 |
| N-5 空白显示「未记录」 | ✅ | `FieldReviewPanel.vue:187-190`（`orNotRecorded`：空 → 「未记录」）、`:31`（`not_recorded: '未记录'`）、`:332`（标记为未记录时置 `normalized_text='未记录'`）、`:357`（说明「绝不写成『未见异常』」）；`InjuryPanel.vue:111/158/163`、`MaterialPanel.vue:477/482`、`PhotoPanel.vue:346/351/474`、`CaseTable.vue:80` | `grep 未见异常` 在 `.vue` 中**只在注释/说明文字**出现（如 `FieldReviewPanel.vue:9/357`），无任何渲染路径产出 |
| N-1 `opinion` 只手工输入且标注「由专业人员提供」 | ✅ | `FieldReviewPanel.vue:412`（`chip`「由专业人员提供（只手工输入，N-1）」）、`:456`（placeholder「由专业人员提供，系统不生成」）、`:465`（opinion 时隐藏「用识别结果填充」按钮）；`CaseWorkspace.vue:463`（「鉴定意见（由专业人员提供）」）、`:472`（「由专业人员在这里手工填写…不会自动填充任何内容」）、`:183`（保存后 toast「内容由专业人员提供」） | 代码核对；`isOpinionField()` 特判确保不把 OCR 结果填入 opinion |
| N-11 / R-58 照片只按位置插入、标注、无图像识别 | ✅ | `PhotoPanel.vue:264`（「系统不读取照片内容，只按你选择的位置插入（N-11 / R-55）…不会识别、描述或推断照片内容」）、`:108-111`（未选位置时拒绝上传并提示 R-57）、`:47-58`（位置项只来自 `props.photoGroups`，**无自由文本输入**）、`:70-72/205`（缩略图走 `api.materialFileUrl(..., 'original')`，仅交给浏览器显示）；`MaterialPanel.vue:258`（照片走 OCR → 前端直接拦截并提示 N-11）、`:435`（chip「系统不读取照片内容」）、`:539` | 代码核对；`PhotoPanel.vue` 全文无任何图像处理 API 调用 |
| 409 统一处理（刷新 + 提示） | ✅ | `App.vue:169-185`（`status === 409` → 提示「已被其他操作更新，请刷新后重试」+ `await refreshAll()`；404 也刷新）；`DocumentPanel.vue:120`（`error.status === 409` 分支提示阻断/冲突）；`DocumentPanel.vue:200`（「存在 N 项未解决阻断项，生成『待签发稿』会被拒绝（HTTP 409）」） | 代码核对 |
| 密钥仅内存（不进 `localStorage`） | ✅ | `AISettingsModal.vue:7`（「密钥只保存在组件内的 ref 中，不写入 localStorage / sessionStorage / cookie」）、`:17-23`（props/emits） | `grep localStorage\|sessionStorage` 在 `.vue` 中**只命中该注释**，无调用 |
| `PhotoPanel` 图片位置目录来自 `/api/config` | ✅ | `App.vue:274-315`（由 `raw.photo_positions` 构造 `photoPositionGroups`，兼容 `[{}]` 与对象两种形态）→ 通过 `:photo-groups` 传给 `CaseWorkspace` → `PhotoPanel` | 代码核对 |
| 无额外 npm 依赖 | ✅ | 15 个组件 + `App.vue` 的 import 白名单 | 见 §3.1 |
| 全中文文案 | ✅ | 逐文件抽查（表格、按钮、提示、错误） | 无英文界面文案残留（仅有枚举 key 与 `code` 等技术标识） |

### 3.5 `frontend/src` 缺陷

#### F1 · blocking · `MaterialPanel.vue` 与 `PhotoPanel.vue` 调 `api.addMaterial` 时**未携带 `expected_version`**

- **所有者**：**W4 前端 Vue（子智能体 D）** → `frontend/src/components/MaterialPanel.vue:181-189`、`frontend/src/components/PhotoPanel.vue:116-125`。
- **位置与证据**：
  ```js
  // MaterialPanel.vue:181-191
  const payload = {
    kind: registerForm.kind,
    filename: registerForm.filename.trim(),
    media_type: registerForm.media_type,
    page_count: 0,
    clarity_note: registerForm.clarity_note.trim(),
    quality_flags: registerForm.quality_flags.slice(),
    sort_order: props.materials.length * 10 + 10,
  }
  if (contentBase64) payload.content_base64 = contentBase64
  await api.addMaterial(props.caseId, payload)      // ← 无 expected_version

  // PhotoPanel.vue:116-126
  const payload = {
    kind: 'photo', filename: ..., media_type: ..., page_count: 1,
    position_key: ..., photo_caption: ..., sort_order: ...,
  }
  if (contentBase64) payload.content_base64 = contentBase64
  await api.addMaterial(props.caseId, payload)      // ← 无 expected_version
  ```
- **违反条款**：`docs/architecture.md:166`「所有写操作（POST/PATCH/DELETE）必须携带 `expected_version`」与 `:194`（该端点版本在请求体，`MaterialCreate` 已声明并要求 `ge=1`）。
- **复现路径（静态推导 + 后端实测对照）**：两个组件都有 `caseVersion` prop（`MaterialPanel.vue:211`、`PhotoPanel.vue:144` 已在别处使用），但构造 `addMaterial` 载荷时漏了它。后端当前因独立缺陷恒返回 **422**（`expected_version Field required`，实测）；即使后端 B1 按建议修复（把完整请求体交给 Pydantic），这两条路径仍会因缺字段返回 **422/400** → 材料与照片上传仍然不可用。
- **影响**：R-1 第一步「选择材料」、R-54/R-58 照片上传在 Vue 前端同样不可用；这是与后端 B1 **互为独立**的缺陷（修掉任一方都不足以恢复功能）。
- **建议最小修复**（两处各加一行）：
  ```js
  const payload = { expected_version: props.caseVersion, kind: ..., ... }
  ```
  （`PhotoPanel.vue:120` 的 `page_count: 1` 可保留，后端对 `kind=photo` 会强制为 1。）
- **验证方式**：修复后由 W5 用 `npm run build`（TODO-DL-3 解锁后）或按源码逐行复核；当前无法构建验证。

---

## 4. 无法验证项

1. **TODO-DL-3：Vue 3 前端未经 `npm install` / `npm run build`，未做浏览器验证。** 依据 `docs/TODO_DOWNLOADS.md:21-28` 的实测结论（`pnpm install --offline` → `ERR_PNPM_NO_OFFLINE_META`；npm 缓存中只有 `vue@2.7.16`、`vite@8.x`，与 `frontend/package.json` 不匹配），在「禁止联网下载」约束下 Vue 3 源码**既不能构建也不能在浏览器运行**。因此 `frontend/src/**` 的全部结论均为**静态核对**，未经验证：
   - 模板编译是否通过（`.vue` 的 SFC 解析、`<script setup>` 与 `defineProps` 的编译产物）
   - 运行时行为（响应式、事件、`v-model`、`watch` 触发时机、切换视图时的状态残留）
   - 样式（Tailwind 类名是否被正确生成；`tailwind.config.js` 的 `content` 是否覆盖 `src/**/*.vue`）
   - 可访问性（焦点管理、键盘操作、对比度）
2. **`frontend/static/**` 的真实浏览器交互未执行**：本次只验证了「HTTP 200 + 字节数 + 关键字」，未在浏览器中点击上传、核对、生成、修正。上传控件依赖 `FileReader`，无法用 `Invoke-WebRequest` 覆盖。
3. **端到端上传链路**：因后端 `POST .../materials` 恒 422（后端报告 B1），`frontend/static` 的材料/照片上传在本机无法走通，只能核对「请求构造正确」。
4. **TODO-DL-2 相关的 OCR 引擎状态显示**：`workbench.js:2027` 的提示与 `OCR_ENGINE_FALLBACK` 仅静态核对；`paddle` 引擎未安装。
5. **`lucide-vue-next` 图标名是否存在**：未执行构建，无法确认每个导入的图标组件名都在该库中（仅核对了导入路径合法性）。风险登记：若某个图标名不存在，构建期会报错——属 TODO-DL-3 解锁后的第一项检查。

---

## 5. 结论

**`frontend/static/**`（W4b）质量高、可作为当前交付的可运行前端**：`node --check` 通过；实测 `GET /`（4452 B）、`/static/workbench.js`（173906 B）、`/static/api.js`、`/static/workbench.css`、`/api/health`、`/api/config` 全部 200；`ENDPOINT_MANIFEST` 覆盖 §4 全 42 条端点且 `expected_version` 位置（POST/PATCH 在体、DELETE 在查询参数、规则类用规则版本）全部正确；R-15 九项来源、R-44 冲突双来源、N-5「未记录」、N-1 opinion 手工 + 标注、N-11「不读取照片内容」+ 位置仅从目录选、409 统一提示与自动刷新、无 CDN/外链、密钥仅内存——**逐条有代码位置证据**。

**`frontend/src/**`（W4）源码完整、结构良好**：15 个组件与 §7.2 完全对齐；import 白名单严格（仅 `vue`/`lucide-vue-next`/相对路径）；props/emits 与父模板传参逐项自洽；所有 `api.*` 调用都在冻结的 `api.js` 中存在；FieldReviewPanel 九项来源、PhotoPanel 的 N-11/R-58 约束、App.vue 的 409 全局处理均达标。**但未经任何构建/浏览器验证（TODO-DL-3）。**

**缺陷统计（本报告）**：blocking **2 条**、minor **2 条**。

- **F1（blocking）**：`frontend/src/components/MaterialPanel.vue:181-189` 与 `PhotoPanel.vue:116-125` 的 `api.addMaterial` 载荷缺少 `expected_version`（应派回 **W4 前端 Vue / 子智能体 D**）。
- **F2（blocking，根因在后端）**：材料/照片上传在真实后端 100% 422（应派回 **W1 后端核心 / 子智能体 A**，见 `review-backend-contract.md` B1）。
- **F3、F4（minor）**：`GET /` 源文件不含「部位照片」字样，与 `architecture.md:918` 的验收断言表述不符（归 **主 Agent / W4b**）。

> ⚠️ **两条 blocking 相互叠加**：F1 与 F2 都会让「材料登记 / 照片上传」失败，且**修掉任一方都不足以恢复功能**——必须同时修 `routes.py:545-547`（后端）与两处 `addMaterial` 载荷（前端）。这是本次评审对交付影响最大的一条组合结论。
