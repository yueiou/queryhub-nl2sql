# QueryHub 启动脚本（Windows PowerShell）
# 用法：
#   .\run.ps1
#   .\run.ps1 -LlmBaseUrl https://api.deepseek.com/v1 -LlmApiKey sk-xxx -LlmModel deepseek-chat
# 也可以把这三项写进 backend/.env，脚本会自动读取（真实环境变量优先于 .env）

param(
    [int]$Port = 8765,
    [string]$LlmBaseUrl = "",
    [string]$LlmApiKey = "",
    [string]$LlmModel = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path "data/demo_shop.db")) {
    Write-Host "首次运行，生成演示数据……" -ForegroundColor Cyan
    python scripts/seed_demo.py
}

# 只在显式传参时覆盖，避免默认值把 .env 里的配置顶掉
if ($LlmBaseUrl) { $env:LLM_BASE_URL = $LlmBaseUrl }
if ($LlmApiKey) { $env:LLM_API_KEY = $LlmApiKey }
if ($LlmModel) { $env:LLM_MODEL = $LlmModel }

python -c "from app.core import config as c; m=c.llm_missing(); print('模型：' + (c.LLM_MODEL or '-') + ' @ ' + (c.LLM_BASE_URL or '-') + ('  缺少：' + '/'.join(m) if m else '  配置齐全'))"

Write-Host "QueryHub 启动：http://127.0.0.1:$Port" -ForegroundColor Green
python -m uvicorn app.main:app --host 127.0.0.1 --port $Port
