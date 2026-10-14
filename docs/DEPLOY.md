# 部署到服务器（Ubuntu / Debian）

## 0. 实测记录（2026-10-14，阿里云 VPS 47.251.245.125，Ubuntu 22.04 / 1.6 GB 内存）

按第 1 节脚本部署并逐项验证通过：

| 验证项 | 结果 |
| --- | --- |
| 系统依赖 | `python3-venv`、`libreoffice-writer`、`fonts-noto-cjk` 安装成功，`soffice=/usr/bin/soffice` |
| Python 环境 | venv + `requirements.txt`（含 `PyMuPDF`）安装成功，Python 3.10.12 |
| systemd | `forensic-doc-agent` 已 enable + running，只监听 `127.0.0.1:9765`，常驻内存约 41 MB |
| 演示案件 | `demo_pipeline` 生成 `FY-2026-0002-report-v1.docx` + `.pdf`：3 张部位照片按栏位插入、18 个照片栏位、15 处「（未提供照片）」、0 占位符残留 |
| PDF 中文渲染 | 5 页、595 个中文字符、PDF 内嵌 `NotoSansCJKjp-*` 字体（证明中文未变方框） |
| PDF 材料处理（R-18） | `probe()` / `extract_pdf_text()` / `render_pdf_pages()` 对上传的 PDF 正常，页面图像渲染成功 |
| 红线 N-11 | 照片材料 8 张，`ocr_blocks` 恒为 0 |
| 隧道访问 | `ssh -L 9765:127.0.0.1:9765` 后 `GET /` 200（工作台）、`/api/health` ok、`/api/config` 18 个位置、`GET /api/documents/1/download?fmt=docx` 200（58 KB，正确 MIME） |

部署时对基础设施做的两处改动（如需回退）：

1. **新增 1 GB swap**（1.6 GB 内存 + LibreOffice 出 PDF 时有 OOM 风险）：
   回退 `swapoff /swapfile && rm /swapfile`，并删掉 `/etc/fstab` 里的 `/swapfile` 行。
2. 未改动 Docker/nginx/防火墙/安全组：服务只监听回环，对外靠 SSH 隧道。

另外本次部署暴露并已修复两处工程问题（见提交记录）：
`requirements.txt` 补 `PyMuPDF`（否则服务器上 PDF 材料无法渲染/读取文字层）；
`deploy/install.sh` 修 git ownership 与工作目录问题。

---

本文件是「把服务放到服务器上并让浏览器访问」的完整说明。核心约束先写在最前面：

> **应用自身没有登录鉴权。** `expected_version` 是乐观锁（防止旧结果覆盖新操作），不是权限控制；
> 密钥只走 `X-DeepSeek-API-Key` 请求头。因此默认部署**只监听 `127.0.0.1`**，对外一律通过
> SSH 隧道访问；确需公网访问时，用 `deploy/nginx-basic-auth.conf` 加 Nginx + TLS + Basic Auth。

---

## 1. 一键安装（Ubuntu 22.04 / Debian 12 实测）

```bash
sudo apt-get update && sudo apt-get install -y --no-install-recommends git
sudo git clone https://github.com/Tendertale/forensic-doc-agent.git /opt/forensic-doc-agent
sudo bash /opt/forensic-doc-agent/deploy/install.sh
```

脚本会（幂等，可重复执行）：

1. 装系统依赖：`python3-venv`、`git`、`curl`、**`libreoffice-writer`**（PDF 导出）、**`fonts-noto-cjk`**（中文字体，缺了 PDF 中文会变方框）；
2. 建运行用户 `forensic`、代码目录 `/opt/forensic-doc-agent`、数据目录 `/var/lib/forensic-doc-agent`（代码与数据分离）；
3. `git clone`/`git pull` 取代码，建 `.venv` 并 `pip install -r requirements.txt`；
4. 生成并启用 systemd 服务 `forensic-doc-agent`（只监听 `127.0.0.1:9765`）；
5. 首次运行 `demo_pipeline` 生成演示案件（可用 `--no-seed` 跳过）；
6. 自检 `/api/health` 与 `GET /`。

常用参数：

```bash
sudo bash deploy/install.sh --port 8123          # 换端口
sudo bash deploy/install.sh --no-libreoffice     # 不装 LibreOffice（PDF 降级为 DOCUMENT_PDF_MISSING 告警，docx 正常）
sudo bash deploy/install.sh --no-seed            # 不生成演示案件
sudo bash deploy/install.sh --dir /srv/app --data /srv/data --user forensic
```

国内服务器若 pip 慢，先指定镜像：

```bash
sudo -E PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple bash deploy/install.sh
```

## 2. 访问方式

**推荐：SSH 隧道（默认，无需证书、不暴露端口）**

```powershell
# 在你自己的电脑上执行（Windows PowerShell 同样可用）
ssh -N -L 9765:127.0.0.1:9765 forensic@<服务器IP>
# 然后浏览器打开  http://127.0.0.1:9765/
```

**可选：公网访问** —— 见 `deploy/nginx-basic-auth.conf`（Nginx + TLS + Basic Auth + `noindex`），
需要域名与证书；服务器上 80/443 若已被其他 nginx 站点占用，请只新增一个站点，不要改动既有配置。

## 3. 运维

| 操作 | 命令 |
| --- | --- |
| 状态 / 日志 | `systemctl status forensic-doc-agent`、`journalctl -u forensic-doc-agent -f` |
| 重启 / 停止 | `systemctl restart forensic-doc-agent` / `stop` |
| 升级 | `sudo bash /opt/forensic-doc-agent/deploy/install.sh --no-seed`（会 `git pull` 到最新 main） |
| 数据位置 | `/var/lib/forensic-doc-agent/`：`cases.db`（SQLite）、`storage/cases/**`（材料原件、派生图、生成的 docx/pdf） |
| 备份 | 备份整个数据目录即可（SQLite + 附件必须一起备份，否则来源追溯会断）：`tar czf backup-$(date +%F).tgz -C /var/lib forensic-doc-agent` |
| 演示数据 | `sudo -u forensic env FORENSIC_DB_PATH=/var/lib/forensic-doc-agent/cases.db FORENSIC_STORAGE_ROOT=/var/lib/forensic-doc-agent/storage HOME=/var/lib/forensic-doc-agent/home /opt/forensic-doc-agent/.venv/bin/python -m backend.scripts.demo_pipeline --force` |

## 4. 资源与容量

| 项 | 建议 |
| --- | --- |
| CPU / 内存 | 2 核 2 GB 可跑（LibreOffice 出 PDF 时进程峰值约 300–500 MB；内存紧张时加 1–2 GB swap，或先用 `--no-libreoffice` 只出 docx） |
| 磁盘 | 代码约 2 MB；数据随案件材料增长（原件 + 派生图 + 每版 docx/pdf，建议按案卷大小预留） |
| 并发 | 单个 uvicorn 进程 + SQLite，适合单机构小团队；导出 PDF 会占用几秒 CPU，建议不要在 1 核小机上并发导出 |

## 5. 需要联网下载的能力（服务器上按需解除）

部署到服务器后，原先记在 `docs/TODO_DOWNLOADS.md` 的条目可以按需安装：

```bash
# TODO-DL-1：模板填充增强（不装则用内置 builtin 渲染器，功能等价）
/opt/forensic-doc-agent/.venv/bin/pip install docxtpl

# TODO-DL-2：真实手写/印刷 OCR（体积大、CPU 推理慢；先确认内存 ≥4 GB）
/opt/forensic-doc-agent/.venv/bin/pip install paddleocr paddlepaddle

# TODO-DL-4：PDF 渲染优先引擎（不装则用 PyMuPDF）
/opt/forensic-doc-agent/.venv/bin/pip install pypdfium2
```

装完 `systemctl restart forensic-doc-agent`，`GET /api/config` 会如实报告可用性变化。
**没有真实样本测试前，不承诺识别率或节省时间。**

## 6. 前端说明

`frontend/static/**` 是零依赖、零构建的原生工作台，由 FastAPI 在 `/` 直接提供，
服务器上**不需要 Node.js**。`frontend/src/**` 的 Vue 3 + Vite 源码是可选的：
有网络时在服务器（或本地）执行 `cd frontend && npm install && npm run build`，
产物 `frontend/dist` 存在后 `/` 会自动改为提供该构建版本。
