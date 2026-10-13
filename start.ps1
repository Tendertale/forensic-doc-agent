# 启动法医尸表检验文书整理工作台（后端 + 可选前端构建）
#
#   .\start.ps1                 启动后端（若 frontend/dist 已存在则一并提供页面）
#   .\start.ps1 -Build          先构建前端再启动（需要 TODO-DL-3：联网 npm install）
#   .\start.ps1 -Port 8123      指定端口
#   .\start.ps1 -Smoke          只运行离线端到端演练后退出

[CmdletBinding()]
param(
    [int]$Port = 9765,
    [switch]$Build,
    [switch]$Smoke
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

if ($Smoke) {
    Write-Step "运行离线端到端演练"
    python -m backend.scripts.smoke
    exit $LASTEXITCODE
}

if ($Build) {
    Write-Step "构建前端（需要联网下载 npm 依赖，见 docs/TODO_DOWNLOADS.md TODO-DL-3）"
    Push-Location frontend
    try {
        npm install
        npm run build
    }
    finally {
        Pop-Location
    }
}

Write-Step "检查依赖（本项目禁止联网下载；缺包见 docs/TODO_DOWNLOADS.md）"
python -c "import fastapi, pydantic, docx, jinja2, PIL, uvicorn; print('核心依赖已就绪')"

Write-Step "静态检查"
python -m compileall -q backend

Write-Step "探测可选能力"
python -c "from backend.app.docgen import docx_engine_status, detect_libreoffice; print('docx engine :', docx_engine_status()); print('libreoffice :', detect_libreoffice())"
python -c "from backend.app.ocr import engine_status; print('ocr engines :', engine_status())"

if (Test-Path (Join-Path $root 'frontend/dist/index.html')) {
    Write-Step "启动后端（含前端构建产物） http://127.0.0.1:$Port"
} else {
    Write-Step "启动后端（无构建依赖工作台） http://127.0.0.1:$Port"
    Write-Host "    提示：页面由 frontend/static/ 直接提供；如需 Vue 版本请先 .\start.ps1 -Build（TODO-DL-3）" -ForegroundColor Yellow
}

python -m uvicorn backend.app.main:app --host 127.0.0.1 --port $Port --reload
