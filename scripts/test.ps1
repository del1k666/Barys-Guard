<#
.SYNOPSIS
Запускает все проверки репозитория: консоль, агент, сервер.

.EXAMPLE
.\test.cmd                 # всё
.\test.cmd -Only web       # только консоль
#>
[CmdletBinding()]
param(
    [ValidateSet('all', 'web', 'agent', 'server')]
    [string]$Only = 'all'
)

. (Join-Path $PSScriptRoot 'lib\common.ps1')

$results = New-Object System.Collections.ArrayList
$overall = Get-Date

# Выполняет один шаг в каталоге Dir. Падение не прерывает скрипт: нужна полная
# картина, а не первая красная строка.
function Invoke-TestStep {
    param([string]$Block, [string]$Title, [string]$Dir, [scriptblock]$Command)

    Write-Host ''
    Write-Host ('=== [{0}] {1}' -f $Block, $Title) -ForegroundColor Cyan

    $started = Get-Date
    $ok = $false
    $previous = $ErrorActionPreference
    # Утилиты пишут в stderr даже при успехе; для Windows PowerShell 5.1 это
    # не должно быть ошибкой. Успех определяется кодом возврата.
    $ErrorActionPreference = 'Continue'
    $pushed = $false
    try {
        Push-Location (Join-Path $script:RepoRoot $Dir) -ErrorAction Stop
        $pushed = $true
        $global:LASTEXITCODE = 0
        & $Command
        $ok = ($LASTEXITCODE -eq 0)
    }
    catch {
        Write-Host $_.Exception.Message -ForegroundColor Red
    }
    finally {
        if ($pushed) { Pop-Location }
        $ErrorActionPreference = $previous
    }

    $seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 1)
    [void]$results.Add([pscustomobject]@{ Block = $Block; Title = $Title; Ok = $ok; Seconds = $seconds })
}

function Add-Failure {
    param([string]$Block, [string]$Title)
    Write-Host ''
    Write-Host ('=== [{0}] {1}' -f $Block, $Title) -ForegroundColor Red
    [void]$results.Add([pscustomobject]@{ Block = $Block; Title = $Title; Ok = $false; Seconds = 0 })
}

# Ищет POSIX sh: сначала Git Bash рядом с git, затем sh из PATH. Системный
# bash/sh из System32 (оболочка WSL) не подходит и пропускается.
function Find-Sh {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) {
        $dir = Split-Path -Parent $git.Source
        foreach ($root in @((Split-Path -Parent $dir), (Split-Path -Parent (Split-Path -Parent $dir)))) {
            foreach ($rel in 'bin\sh.exe', 'usr\bin\sh.exe') {
                $candidate = Join-Path $root $rel
                if (Test-Path $candidate) { return $candidate }
            }
        }
    }
    $onPath = Get-Command sh -ErrorAction SilentlyContinue
    if ($onPath -and $onPath.Source -notlike "$env:SystemRoot\*") { return $onPath.Source }
    return $null
}
if ($Only -eq 'all' -or $Only -eq 'web') {
    if (-not (Test-Path (Join-Path $script:RepoRoot 'web\node_modules'))) {
        Invoke-TestStep 'web' 'npm ci' 'web' { npm ci }
    }
    Invoke-TestStep 'web' 'typecheck' 'web' { npm run typecheck }
    Invoke-TestStep 'web' 'tests' 'web' { npm test }
    Invoke-TestStep 'web' 'build' 'web' { npm run build }
    Invoke-TestStep 'web' 'licenses' 'web' { npm run licenses }
}

if ($Only -eq 'all' -or $Only -eq 'agent') {
    Invoke-TestStep 'agent' 'go vet' 'agent' { go vet ./... }
    Invoke-TestStep 'agent' 'go test' 'agent' { go test ./... }
    $sh = Find-Sh
    if ($sh) {
        Invoke-TestStep 'agent' 'entrypoint.sh' 'agent' { & $sh entrypoint_test.sh }
    }
    else {
        Write-Host 'sh не найден (нужен Git Bash): проверка entrypoint.sh пропущена.' -ForegroundColor Yellow
        Add-Failure 'agent' 'entrypoint.sh: sh не найден (нужен Git Bash)'
    }
}

if ($Only -eq 'all' -or $Only -eq 'server') {
    $python = Join-Path $script:RepoRoot 'server\.venv\Scripts\python.exe'
    $ruff = Join-Path $script:RepoRoot 'server\.venv\Scripts\ruff.exe'
    $mypy = Join-Path $script:RepoRoot 'server\.venv\Scripts\mypy.exe'

    if (-not (Test-Path $python)) {
        Invoke-TestStep 'server' 'создание окружения' 'server' {
            python -m venv .venv
            if ($LASTEXITCODE -eq 0) { & .\.venv\Scripts\python.exe -m pip install -e '.[dev]' }
        }
    }

    if (Test-DockerRunning) {
        Invoke-TestStep 'server' 'pytest' 'server' { & $python -m pytest -q }
    }
    else {
        # Интеграционные тесты поднимают PostgreSQL в контейнере.
        Add-Failure 'server' 'pytest: Docker не запущен (нужен для PostgreSQL в тестах)'
    }
    Invoke-TestStep 'server' 'ruff check' 'server' { & $ruff check . }
    Invoke-TestStep 'server' 'ruff format --check' 'server' { & $ruff format --check . }
    Invoke-TestStep 'server' 'mypy' 'server' { & $mypy barysguard }
}

Write-Host ''
Write-Host '=== Итог ===' -ForegroundColor Cyan
foreach ($r in $results) {
    $mark = 'ok  '
    $color = 'Green'
    if (-not $r.Ok) { $mark = 'FAIL'; $color = 'Red' }
    Write-Host ('{0}  [{1}] {2} ({3} с)' -f $mark, $r.Block, $r.Title, $r.Seconds) -ForegroundColor $color
}

$failed = @($results | Where-Object { -not $_.Ok })
$total = [math]::Round(((Get-Date) - $overall).TotalSeconds, 0)
Write-Host ''
if ($failed.Count -gt 0) {
    Write-Host ('Не прошло: {0} из {1}. Всего {2} с.' -f $failed.Count, $results.Count, $total) -ForegroundColor Red
    exit 1
}
Write-Host ('Всё прошло: {0} шагов за {1} с.' -f $results.Count, $total) -ForegroundColor Green
exit 0
