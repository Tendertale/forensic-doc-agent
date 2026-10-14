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

### 0.1 第二阶段：改为服务器常驻访问（子域名 + 鉴权）

在同一台 VPS 上继续做了「不依赖本机隧道」的常驻访问配置，实测结果：

| 验证项 | 结果 |
| --- | --- |
| 监听与鉴权 | `--bind 0.0.0.0 --basic-auth 'forensic:***'` 重部署成功；`/api/health` 200（免鉴权）、`/api/config` 无凭据 **401**、带凭据 **200** |
| 新增站点 | `/opt/proxy/nginx/conf.d/forensic-doc-agent.conf` 已安装；`docker exec nginx nginx -t` 通过、`nginx -s reload` 平滑生效 |
| 容器→宿主 | nginx 容器访问 `http://172.19.0.1:9765/api/config`：无凭据 401、带凭据 200 |
| 完整链路 | `nginx`(443, SNI `forensic.sekaitree.xyz`)：`/api/health` 200、`/api/config` 401、`/` 带凭据 200、`/api/cases` 返回真实 JSON；80 端口 → 301 |
| 既有站点 | `api.sekaitree.xyz` 仍 200（未改动其配置）；仅有 nginx 自身的 http2 弃用告警 |
| 公网暴露面 | 直连 `47.251.245.125:9765` **仍然超时**（安全组未放行）——唯一公网入口是 443 上的站点，且需凭据 |
| 待用户完成 | Cloudflare 添加 A 记录 `forensic` → 服务器公网 IP（可开代理）后，`https://forensic.sekaitree.xyz/` 即可直接访问 |

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

### 方式一：SSH 隧道（最快，不暴露端口，适合自己用）

```powershell
# 在你自己的电脑上执行（Windows PowerShell 同样可用）
ssh -N -L 9765:127.0.0.1:9765 forensic@<服务器IP>
# 然后浏览器打开  http://127.0.0.1:9765/
```

缺点是**必须有一台电脑开着隧道**；服务本身仍在服务器上跑（systemd 托管），隧道只是访问通道。

### 方式二：服务器常驻可访问（子域名 + HTTPS，推荐长期使用）

适合「服务在 VPS 上、任何设备打开网址就能用」的场景。三步：

```bash
# 1) 让应用监听 0.0.0.0（同机 nginx 容器才访问得到），并开启 HTTP Basic 鉴权
sudo bash /opt/forensic-doc-agent/deploy/install.sh \
     --bind 0.0.0.0 --basic-auth 'forensic:<自己设一个强口令>' --no-seed

# 2) 查 nginx 容器的 docker 网桥网关（容器访问宿主机走网关，不是 127.0.0.1）
docker inspect nginx --format '{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}'

# 3) 新增一个站点（不要改动既有站点的配置文件）
sudo cp /opt/forensic-doc-agent/deploy/nginx-forensic-doc-agent.conf \
        /opt/proxy/nginx/conf.d/forensic-doc-agent.conf
sudo sed -i 's/forensic.example.com/<你的子域名>/g; s#172.19.0.1#<上一步的网关>#' \
        /opt/proxy/nginx/conf.d/forensic-doc-agent.conf
docker exec nginx nginx -t && docker exec nginx nginx -s reload   # reload 是平滑的
```

`deploy/nginx-forensic-doc-agent.conf` 里已经包含：80→301、TLS、`noindex`、
`client_max_body_size 64m`、`proxy_read_timeout 300s`（导出 PDF 慢）、以及把
`Authorization` 头透传给应用层 Basic Auth。

**然后加一条 DNS 记录**（这一步只能你自己做）：

| 类型 | 名称 | 值 | 说明 |
| --- | --- | --- | --- |
| A | `forensic`（子域名前缀） | 服务器公网 IP | Cloudflare 可开代理（橙云）；源站证书用 Origin CA 通配证书即可 |

**安全组/防火墙**：只需放行 `80`/`443`。**不要把 9765 对公网放行**——应用层 Basic Auth 只是兜底，
真正的边界是「9765 不对外」。

验证：

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://<子域名>/api/health          # 期望 200（免鉴权）
curl -s -o /dev/null -w '%{http_code}\n' https://<子域名>/api/config          # 期望 401（需凭据）
curl -s -o /dev/null -w '%{http_code}\n' -u forensic:<口令> https://<子域名>/  # 期望 200
```

改口令/换监听地址：重跑第 1 步（`install.sh` 幂等，会重写 systemd 单元并重启服务）。

### 方式三（不推荐）：直接把端口暴露到公网

在云控制台安全组放行 `9765` 即可用 `http://<公网IP>:9765/` 访问，但**没有 TLS**（凭据与案件数据明文传输），
仅适合临时验证，用完请立即关闭端口。

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
