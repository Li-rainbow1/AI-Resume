[CmdletBinding()]
param(
    [switch]$Build,
    [ValidateRange(30, 300)]
    [int]$TimeoutSeconds = 120
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$root = Split-Path $PSScriptRoot -Parent
$envFile = Join-Path $root '.env.test'
$composeFile = Join-Path $root 'compose.qa.yml'
$python = Join-Path $root '.venv\Scripts\python.exe'
$requiredServices = @('mysql', 'redis', 'pgvector', 'minio', 'mock-ai', 'backend', 'image-worker', 'frontend')

function Invoke-Compose {
    param([Parameter(Mandatory = $true)][string[]]$ComposeArguments)

    & docker compose --env-file $envFile -f $composeFile @ComposeArguments
    if ($LASTEXITCODE -ne 0) {
        throw ('Docker Compose 执行失败：' + ($ComposeArguments -join ' '))
    }
}

function Get-ServiceState {
    param([Parameter(Mandatory = $true)][string]$Service)

    $containerId = (& docker compose --env-file $envFile -f $composeFile ps -q $Service).Trim()
    if ([string]::IsNullOrWhiteSpace($containerId)) {
        return 'missing'
    }

    return (& docker inspect --format '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' $containerId).Trim()
}

function Get-ServiceUrl {
    param(
        [Parameter(Mandatory = $true)][string]$Service,
        [Parameter(Mandatory = $true)][int]$ContainerPort,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $address = (& docker compose --env-file $envFile -f $composeFile port $Service $ContainerPort).Trim()
    if ([string]::IsNullOrWhiteSpace($address)) {
        return $null
    }
    return "http://$address$Path"
}

function Test-HttpReady {
    param([Parameter(Mandatory = $true)][string]$Url)

    try {
        $response = Invoke-WebRequest -Uri $Url -TimeoutSec 3 -UseBasicParsing
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 400
    } catch {
        return $false
    }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw '未找到 Docker 命令，请先启动 Docker Desktop。'
}

& docker info --format '{{.ServerVersion}}' *> $null
if ($LASTEXITCODE -ne 0) {
    throw 'Docker Desktop 当前不可用，请启动后重试。'
}

if (-not (Test-Path -LiteralPath $envFile)) {
    if (-not (Test-Path -LiteralPath $python)) {
        throw '缺少 .env.test 和 QA .venv，请先按 README 安装 test 依赖。'
    }
    Write-Host '未找到 .env.test，正在生成本机隔离 QA 配置。'
    Push-Location $root
    try {
        & $python scripts/create_local_env.py
        if ($LASTEXITCODE -ne 0) {
            throw '生成 .env.test 失败。'
        }
    } finally {
        Pop-Location
    }
}

Invoke-Compose -ComposeArguments @('config', '--quiet')
$endpoints = @(
    @{ Name = '后端'; Url = Get-ServiceUrl -Service 'backend' -ContainerPort 8999 -Path '/health' },
    @{ Name = 'Mock AI'; Url = Get-ServiceUrl -Service 'mock-ai' -ContainerPort 8000 -Path '/health' },
    @{ Name = '前端'; Url = Get-ServiceUrl -Service 'frontend' -ContainerPort 80 -Path '/' }
)
$initialStates = @{}
foreach ($service in $requiredServices) {
    $initialStates[$service] = Get-ServiceState -Service $service
}
$servicesAlreadyReady = @($initialStates.Values | Where-Object { $_ -notmatch '^running\|(healthy|none)$' }).Count -eq 0
$endpointsAlreadyReady = @($endpoints | Where-Object { -not $_.Url -or -not (Test-HttpReady -Url $_.Url) }).Count -eq 0
if (-not $Build -and $servicesAlreadyReady -and $endpointsAlreadyReady) {
    Write-Host 'QA 环境已在运行，可以在 PyCharm 中运行 pytest。'
    Invoke-Compose -ComposeArguments @('ps')
    exit 0
}

$upArguments = @('up', '-d')
if ($Build) {
    $upArguments += '--build'
}
Invoke-Compose -ComposeArguments $upArguments

$deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
$lastStatus = ''

while ([DateTime]::UtcNow -lt $deadline) {
    $states = @{}
    foreach ($service in $requiredServices) {
        $states[$service] = Get-ServiceState -Service $service
    }
    $servicesReady = @($states.Values | Where-Object { $_ -notmatch '^running\|(healthy|none)$' }).Count -eq 0
    $endpointsReady = @($endpoints | Where-Object { -not $_.Url -or -not (Test-HttpReady -Url $_.Url) }).Count -eq 0
    if ($servicesReady -and $endpointsReady) {
        Write-Host 'QA 环境已就绪，可以在 PyCharm 中运行 pytest。'
        Invoke-Compose -ComposeArguments @('ps')
        exit 0
    }

    $status = ($requiredServices | ForEach-Object { "$_=$($states[$_])" }) -join ', '
    if ($status -ne $lastStatus) {
        Write-Host "等待 QA 服务就绪：$status"
        $lastStatus = $status
    }
    Start-Sleep -Seconds 2
}

Invoke-Compose -ComposeArguments @('ps')
throw "QA 环境在 $TimeoutSeconds 秒内未就绪，请查看上方容器状态和日志。"
