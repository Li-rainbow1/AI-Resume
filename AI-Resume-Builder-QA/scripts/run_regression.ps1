# author: jf
[CmdletBinding()]
param(
    [ValidateSet('autosave', 'rag_query', 'file_upload', 'image_worker_comparison', 'interview_sse')]
    [string[]]$Performance,
    [switch]$Quality
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$root = Split-Path $PSScriptRoot -Parent
$python = Join-Path $root '.venv\Scripts\python.exe'
$runId = 'reg-' + [guid]::NewGuid().ToString('N').Substring(0, 24)
$report = Join-Path $root "reports\regression\$runId"
New-Item -ItemType Directory -Path $report -Force | Out-Null
$oldRunId = $env:QA_RUN_ID
$oldUtf8 = $env:PYTHONUTF8
$code = 1
try {
    $env:QA_RUN_ID = $runId
    $env:PYTHONUTF8 = '1'
    if (-not (Test-Path -LiteralPath $python)) {
        throw '缺少 QA .venv，请先安装 README 中的 test 依赖；清理校验无法启动。'
    }
    $arguments = @((Join-Path $PSScriptRoot 'regression_runner.py'), '--report', $report)
    if ($Performance) { $arguments += @('--performance', ($Performance -join ',')) }
    if ($Quality) { $arguments += '--quality' }
    & $python @arguments
    $code = $LASTEXITCODE
} catch {
    Write-Host ('回归入口失败：' + $_.Exception.Message)
} finally {
    # Python 在导入依赖时失败也尝试既有只读清理校验。
    if ((Test-Path -LiteralPath $python) -and -not (Test-Path -LiteralPath (Join-Path $report 'summary.json'))) {
        Push-Location $root
        try {
            & $python scripts/verify_run_cleanup.py *> $null
            $cleanupCode = $LASTEXITCODE
            @{ author = 'jf'; run_id = $runId; exit_code = 1; cleanup_exit_code = $cleanupCode; status = '运行器启动失败' } |
                ConvertTo-Json | Set-Content -LiteralPath (Join-Path $report 'summary.json') -Encoding UTF8
        } finally { Pop-Location }
        $code = 1
    }
    if (-not (Test-Path -LiteralPath (Join-Path $report 'summary.json'))) {
        @{ author = 'jf'; run_id = $runId; exit_code = 1; status = '缺少运行时，清理校验无法执行' } |
            ConvertTo-Json | Set-Content -LiteralPath (Join-Path $report 'summary.json') -Encoding UTF8
    }
    $env:QA_RUN_ID = $oldRunId
    $env:PYTHONUTF8 = $oldUtf8
    Write-Host "运行 ID：$runId"
    Write-Host "汇总报告：$report\summary.json"
}
exit $code
