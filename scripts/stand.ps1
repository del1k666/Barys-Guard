<#
.SYNOPSIS
Управление dev-стендом BarysGuard (Docker).

.EXAMPLE
.\stand.cmd up        # собрать и запустить
.\stand.cmd status    # состояние и число агентов
.\stand.cmd add-agent agent-extra-1 it
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet('up', 'down', 'reset', 'status', 'logs', 'add-agent')]
    [string]$Command,

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$Rest = @()
)

. (Join-Path $PSScriptRoot 'lib\common.ps1')

function Assert-DockerReady {
    if (-not (Test-DockerRunning)) {
        Write-Host 'Docker не запущен. Запустите Docker Desktop и повторите команду.' -ForegroundColor Red
        exit 1
    }
}

function Test-StandRunning {
    $found = Invoke-Docker ps -q --filter "label=com.docker.compose.project=$($script:ProjectName)"
    return ($found.Output.Count -gt 0)
}

function Remove-ExtraAgents {
    $found = Invoke-Docker ps -aq --filter 'label=barysguard.stand=1'
    if ($found.Output.Count -gt 0) {
        [void](Invoke-Docker (@('rm', '-f') + $found.Output))
    }
}

function Start-Stand {
    Assert-DockerReady

    if (-not (Test-Path $script:EnvFile)) {
        Copy-Item (Join-Path $script:StandDir '.env.example') $script:EnvFile
        Write-Host "Создан $script:EnvFile из .env.example (его можно править)."
    }
    $stand = Get-StandEnv

    if (-not (Test-StandRunning)) {
        foreach ($name in 'STAND_CONSOLE_PORT', 'STAND_AGENT_PORT') {
            $port = [int]$stand[$name]
            if (-not (Test-PortFree $port)) {
                Write-Host "Порт $port занят другой программой. Освободите его или задайте другой в $script:EnvFile ($name)." -ForegroundColor Red
                exit 1
            }
        }
    }

    Write-Host 'Сборка и запуск (первая сборка занимает несколько минут)...'
    try {
        Invoke-Compose up -d --build
    }
    catch {
        Write-Host 'Запуск не удался. Последние строки журнала bootstrap:' -ForegroundColor Red
        try { Invoke-Compose logs --tail 30 bootstrap } catch { }
        throw
    }

    $readyUrl = "http://127.0.0.1:$($stand['STAND_CONSOLE_PORT'])/ready"
    $deadline = (Get-Date).AddSeconds(180)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        if ((Invoke-Api -Method GET -Url $readyUrl -TimeoutSec 5).Status -eq 200) {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 2
    }
    if (-not $ready) {
        Write-Host 'Сервер не стал готов за 3 минуты. Журнал: .\stand.cmd logs server' -ForegroundColor Red
        exit 1
    }

    Write-Host ''
    Write-Host 'Стенд запущен.' -ForegroundColor Green
    Write-Host "  Консоль:  http://localhost:$($stand['STAND_CONSOLE_PORT'])"
    Write-Host "  Логин:    $($stand['BG_STAND_ADMIN_USERNAME'])"
    Write-Host "  Пароль:   $($stand['BG_STAND_ADMIN_PASSWORD'])   (только для стенда!)"
    Write-Host '  Агенты регистрируются в течение минуты: .\stand.cmd status'
    Write-Host '  Проверка:                               .\smoke.cmd'
}

function Stop-Stand {
    Assert-DockerReady
    # Агенты из add-agent — обычные контейнеры в сети проекта: пока они живы,
    # compose не может удалить сеть. Данные их томов остаются.
    Remove-ExtraAgents
    Invoke-Compose down
}

function Reset-Stand {
    Assert-DockerReady
    Remove-ExtraAgents
    Invoke-Compose down --volumes --remove-orphans
    $volumes = Invoke-Docker volume ls -q --filter 'label=barysguard.stand=1'
    if ($volumes.Output.Count -gt 0) {
        [void](Invoke-Docker (@('volume', 'rm') + $volumes.Output))
    }
    Write-Host 'Стенд сброшен: база, CA, токены и данные агентов удалены.'
}

function Show-Status {
    Assert-DockerReady
    Invoke-Compose ps -a
    $stand = Get-StandEnv
    $base = "http://127.0.0.1:$($stand['STAND_CONSOLE_PORT'])"
    $session = New-ApiSession
    $login = Invoke-Api -Method POST -Url "$base/api/v1/auth/login" -Session $session `
        -Body @{ username = $stand['BG_STAND_ADMIN_USERNAME']; password = $stand['BG_STAND_ADMIN_PASSWORD'] }
    if ($login.Status -ne 200) {
        Write-Host "Вход в консоль не удался (HTTP $($login.Status)): стенд не запущен либо пароль в $script:EnvFile не совпадает с тем, с которым он создавался." -ForegroundColor Yellow
        return
    }
    $overview = Invoke-Api -Method GET -Url "$base/api/v1/overview" -Session $session
    if ($overview.Status -ne 200) {
        Write-Host "Обзор недоступен (HTTP $($overview.Status))." -ForegroundColor Yellow
        return
    }
    $agents = $overview.Json.agents
    Write-Host ''
    Write-Host ('Агенты: всего {0}, активны {1}, не в сети {2}, ожидают {3}, отозваны {4}' -f `
            $agents.total, $agents.active, $agents.offline, $agents.pending, $agents.revoked)
}

function Add-StandAgent {
    Assert-DockerReady

    $name = 'agent-extra-' + (Get-Date -Format 'HHmmss')
    if ($Rest.Count -gt 0) { $name = $Rest[0] }
    $group = 'accounting'
    if ($Rest.Count -gt 1) { $group = $Rest[1] }

    if ($name -notmatch '^[a-z0-9][a-z0-9-]{0,40}$') {
        Write-Host 'Имя агента: строчные латинские буквы, цифры и дефис.' -ForegroundColor Red
        exit 1
    }
    if ($group -notin 'accounting', 'it') {
        Write-Host 'Группа: accounting или it.' -ForegroundColor Red
        exit 1
    }

    $network = "$($script:ProjectName)_default"
    if ((Invoke-Docker network inspect $network).Code -ne 0) {
        Write-Host 'Стенд не запущен: сначала .\stand.cmd up' -ForegroundColor Red
        exit 1
    }
    if ((Invoke-Docker ps -aq --filter "name=^/$name$").Output.Count -gt 0) {
        Write-Host "Контейнер $name уже существует." -ForegroundColor Red
        exit 1
    }

    $volume = "$($script:ProjectName)_$name-data"
    [void](Invoke-Docker volume create --label barysguard.stand=1 $volume)
    $run = Invoke-Docker run -d --name $name --hostname $name --network $network `
        --restart on-failure --label barysguard.stand=1 `
        -v "$($script:ProjectName)_tls:/tls:ro" -v "$($script:ProjectName)_enroll:/enroll:ro" `
        -v "${volume}:/var/lib/agent" `
        -e "BG_AGENT_GROUP=$group" -e 'BG_SERVER_URL=https://nginx:8443' `
        barysguard-agent:stand
    if ($run.Code -ne 0) {
        Write-Host "Не удалось запустить $name." -ForegroundColor Red
        exit 1
    }
    Write-Host "Агент $name запущен (группа $group). Появится в консоли через несколько секунд."
}

try {
    switch ($Command) {
        'up' { Start-Stand }
        'down' { Stop-Stand }
        'reset' { Reset-Stand }
        'status' { Show-Status }
        'logs' { Assert-DockerReady; Invoke-Compose (@('logs', '--tail', '200') + $Rest) }
        'add-agent' { Add-StandAgent }
    }
}
catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
