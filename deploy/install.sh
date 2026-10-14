#!/usr/bin/env bash
# 法医尸表检验文书整理 Agent —— 服务器安装/升级脚本（Ubuntu / Debian，幂等可重复执行）
#
#   sudo bash deploy/install.sh                       # 默认：装依赖 + venv + systemd + 演示数据
#   sudo bash deploy/install.sh --no-libreoffice      # 不装 LibreOffice（PDF 导出降级为缺失告警）
#   sudo bash deploy/install.sh --no-seed             # 不生成演示案件
#   sudo bash deploy/install.sh --user forensic --port 9765 --dir /opt/forensic-doc-agent
#
# 设计要点：
#   * 服务只监听 127.0.0.1（对外用 SSH 隧道或 Nginx 反代 + Basic Auth，应用本身没有登录）；
#   * 代码与数据分离：代码在 --dir，SQLite 与案件附件在 --data（备份只需要备份后者）；
#   * 幂等：重复执行只做“补齐”，不覆盖已有数据库与案件文件（N-8 原件不可覆盖）。
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Tendertale/forensic-doc-agent.git}"
APP_USER="forensic"
APP_DIR="/opt/forensic-doc-agent"
DATA_DIR="/var/lib/forensic-doc-agent"
PORT="9765"
WITH_LIBREOFFICE=1
WITH_SEED=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) APP_USER="$2"; shift 2 ;;
    --dir) APP_DIR="$2"; shift 2 ;;
    --data) DATA_DIR="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --repo) REPO_URL="$2"; shift 2 ;;
    --no-libreoffice) WITH_LIBREOFFICE=0; shift ;;
    --no-seed) WITH_SEED=0; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

log() { printf '\n==> %s\n' "$*"; }

if [[ "$(id -u)" -ne 0 ]]; then
  echo "请用 root（sudo bash deploy/install.sh）" >&2
  exit 1
fi

log "1/7 系统依赖"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends python3-venv python3-pip git curl ca-certificates >/dev/null
if [[ "$WITH_LIBREOFFICE" -eq 1 ]]; then
  # PDF 导出靠 LibreOffice；中文字体缺失会导致 PDF 中文变方框、分页错乱。
  apt-get install -y --no-install-recommends libreoffice-writer fonts-noto-cjk >/dev/null
  echo "  LibreOffice：$(which soffice || which libreoffice || echo 未找到)"
else
  echo "  已跳过 LibreOffice（PDF 导出会记 DOCUMENT_PDF_MISSING 告警，docx 正常）"
fi

log "2/7 运行用户与目录"
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  useradd -m -s /bin/bash "$APP_USER"
fi
mkdir -p "$APP_DIR" "$DATA_DIR" "$DATA_DIR/storage" "$DATA_DIR/home"
chown -R "$APP_USER:$APP_USER" "$APP_DIR" "$DATA_DIR"

log "3/7 代码（clone / pull）"
if [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" fetch --depth 1 origin >/dev/null 2>&1 || true
  git -C "$APP_DIR" reset --hard origin/HEAD >/dev/null 2>&1 || git -C "$APP_DIR" pull --ff-only || true
  echo "  已更新到 $(git -C "$APP_DIR" rev-parse --short HEAD)"
else
  git clone --depth 1 "$REPO_URL" "$APP_DIR"
  echo "  已克隆 $(git -C "$APP_DIR" rev-parse --short HEAD)"
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

log "4/7 Python 虚拟环境"
if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
fi
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -q --upgrade pip
# 国内机器可先 export PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple 再执行本脚本
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
echo "  $(sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" -V)"

log "5/7 systemd 服务"
sed -e "s#@APP_USER@#$APP_USER#g" -e "s#@APP_DIR@#$APP_DIR#g" \
    -e "s#@DATA_DIR@#$DATA_DIR#g" -e "s#@PORT@#$PORT#g" \
    "$APP_DIR/deploy/forensic-doc-agent.service" > /etc/systemd/system/forensic-doc-agent.service
systemctl daemon-reload
systemctl enable forensic-doc-agent >/dev/null

log "6/7 演示案件（首次）"
if [[ "$WITH_SEED" -eq 1 ]]; then
  sudo -u "$APP_USER" env \
    FORENSIC_DB_PATH="$DATA_DIR/cases.db" \
    FORENSIC_STORAGE_ROOT="$DATA_DIR/storage" \
    HOME="$DATA_DIR/home" \
    "$APP_DIR/.venv/bin/python" -m backend.scripts.demo_pipeline --force || true
else
  echo "  已跳过"
fi

log "7/7 启动与自检"
chown -R "$APP_USER:$APP_USER" "$DATA_DIR"
systemctl restart forensic-doc-agent
sleep 3
systemctl --no-pager --lines=0 status forensic-doc-agent || true
curl -fsS "http://127.0.0.1:$PORT/api/health" && echo
curl -fsS -o /dev/null -w "GET /  -> %{http_code}\n" "http://127.0.0.1:$PORT/"

cat <<EOF

部署完成。服务只监听 127.0.0.1:$PORT（应用自身没有登录，不要直接暴露公网）。

本机浏览器访问（SSH 隧道，把下面命令在你自己电脑上执行）：
  ssh -N -L $PORT:127.0.0.1:$PORT $APP_USER@<服务器IP>
  然后打开 http://127.0.0.1:$PORT/

常用运维命令：
  systemctl status|restart|stop forensic-doc-agent
  journalctl -u forensic-doc-agent -f
  升级： sudo bash $APP_DIR/deploy/install.sh --no-seed
  数据： SQLite + 案件附件都在 $DATA_DIR（备份这一目录即可）

需要公网访问时见 docs/DEPLOY.md（Nginx + TLS + Basic Auth）。
EOF
