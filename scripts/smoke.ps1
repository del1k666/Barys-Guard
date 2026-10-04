<#
.SYNOPSIS
Сквозная проверка запущенного стенда через HTTP.

.EXAMPLE
.\smoke.cmd
#>
[CmdletBinding()]
param()

. (Join-Path $PSScriptRoot 'lib\common.ps1')

$stand = Get-StandEnv
$base = "http://127.0.0.1:$($stand['STAND_CONSOLE_PORT'])"
$session = New-ApiSession
$script:StepNo = 0

function Fail-Step {
    param([string]$Title, [string]$Message)
    Write-Host ('[{0}] FAIL  {1}' -f $script:StepNo, $Title) -ForegroundColor Red
    Write-Host ('      {0}' -f $Message) -ForegroundColor Red
    exit $script:StepNo
}

function Assert-Step {
    param([string]$Title, [scriptblock]$Check)
    $script:StepNo++
    try {
        & $Check
    }
    catch {
        Fail-Step $Title $_.Exception.Message
    }
    Write-Host ('[{0}] ok    {1}' -f $script:StepNo, $Title) -ForegroundColor Green
}

function Expect-Equal {
    param($Actual, $Expected, [string]$What)
    if ($Actual -ne $Expected) {
        throw ("{0}: ожидалось '{1}', получено '{2}'" -f $What, $Expected, $Actual)
    }
}

# Опрашивает пробу, пока она не вернёт Done = $true. Проба возвращает объект
# с полями Done и Note (описание текущего состояния для сообщения об ошибке).
function Wait-Until {
    param([scriptblock]$Probe, [int]$TimeoutSec, [string]$What)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $last = $null
    while ((Get-Date) -lt $deadline) {
        $last = & $Probe
        if ($last.Done) { return $last }
        Start-Sleep -Seconds 2
    }
    throw ('{0}: за {1} с не дождались; последнее состояние: {2}' -f $What, $TimeoutSec, $last.Note)
}

Assert-Step 'сервер жив и готов (/health, /ready)' {
    $health = Invoke-Api -Method GET -Url "$base/health"
    if ($health.Status -eq 0) { throw "GET /health: нет ответа ($($health.Text)); стенд не запущен? Выполните .\stand.cmd up" }
    Expect-Equal $health.Status 200 'GET /health'
    Expect-Equal (Invoke-Api -Method GET -Url "$base/ready").Status 200 'GET /ready'
}

Assert-Step 'неверные учётные данные дают 401' {
    # Несуществующий пользователь, а не неверный пароль администратора: серия
    # неудачных входов не должна блокировать учётную запись стенда.
    $r = Invoke-Api -Method POST -Url "$base/api/v1/auth/login" `
        -Body @{ username = 'smoke-no-such-user'; password = 'definitely-wrong-password' }
    Expect-Equal $r.Status 401 'POST /api/v1/auth/login с неверными данными'
}

Assert-Step 'администратор входит' {
    $r = Invoke-Api -Method POST -Url "$base/api/v1/auth/login" -Session $session `
        -Body @{ username = $stand['BG_STAND_ADMIN_USERNAME']; password = $stand['BG_STAND_ADMIN_PASSWORD'] }
    Expect-Equal $r.Status 200 'POST /api/v1/auth/login'
    Expect-Equal $r.Json.username $stand['BG_STAND_ADMIN_USERNAME'] 'имя в ответе'
    Expect-Equal $r.Json.must_change_password $false 'must_change_password'
}

Assert-Step 'в парке не меньше пяти агентов' {
    [void](Wait-Until -TimeoutSec 90 -What 'число агентов' -Probe {
            $r = Invoke-Api -Method GET -Url "$base/api/v1/overview" -Session $session
            $total = 0
            if ($r.Status -eq 200) { $total = $r.Json.agents.total }
            [pscustomobject]@{ Done = ($total -ge 5); Note = "всего $total (HTTP $($r.Status))" }
        })
}

Assert-Step 'не меньше пяти агентов активны' {
    [void](Wait-Until -TimeoutSec 90 -What 'число активных агентов' -Probe {
            $r = Invoke-Api -Method GET -Url "$base/api/v1/overview" -Session $session
            $active = 0
            if ($r.Status -eq 200) { $active = $r.Json.agents.active }
            [pscustomobject]@{ Done = ($active -ge 5); Note = "активных $active (HTTP $($r.Status))" }
        })
}

Assert-Step 'агенты распределены по группам Бухгалтерия (3) и ИТ (2)' {
    $r = Invoke-Api -Method GET -Url "$base/api/v1/groups" -Session $session
    Expect-Equal $r.Status 200 'GET /api/v1/groups'
    $counts = @{}
    foreach ($group in @($r.Json)) { $counts[$group.name] = $group.agent_count }
    if ($counts['Бухгалтерия'] -lt 3) { throw "в группе Бухгалтерия агентов: $($counts['Бухгалтерия']), ожидалось не меньше 3" }
    if ($counts['ИТ'] -lt 2) { throw "в группе ИТ агентов: $($counts['ИТ']), ожидалось не меньше 2" }
}

Assert-Step 'команда ping доходит до агента и выполняется' {
    $agents = Invoke-Api -Method GET -Url "$base/api/v1/agents?status=active&limit=1" -Session $session
    Expect-Equal $agents.Status 200 'GET /api/v1/agents'
    $items = @($agents.Json.items)
    if ($items.Count -eq 0) { throw 'нет ни одного активного агента' }
    $agentId = $items[0].id

    $sent = Invoke-Api -Method POST -Url "$base/api/v1/agents/$agentId/commands" -Session $session `
        -Body @{ type = 'ping' }
    Expect-Equal $sent.Status 201 'POST /api/v1/agents/{id}/commands'
    $commandId = $sent.Json.id

    # Агент забирает команду на ближайшем heartbeat (по умолчанию раз в 30 секунд).
    [void](Wait-Until -TimeoutSec 120 -What 'выполнение ping' -Probe {
            $list = Invoke-Api -Method GET -Url "$base/api/v1/commands?agent_id=$agentId&limit=20" -Session $session
            $state = 'не найдена'
            foreach ($command in @($list.Json.items)) {
                if ($command.id -eq $commandId) { $state = $command.status }
            }
            if ($state -eq 'failed' -or $state -eq 'expired') { throw "команда завершилась со статусом $state" }
            [pscustomobject]@{ Done = ($state -eq 'done'); Note = "статус $state" }
        })
}

Assert-Step 'консоль отдаёт приложение на / и на глубоких адресах' {
    foreach ($path in '/', '/agents/00000000-0000-0000-0000-000000000000') {
        $r = Invoke-Api -Method GET -Url "$base$path"
        Expect-Equal $r.Status 200 ('GET ' + $path)
        if ($r.Text -notmatch '<div id="root">') {
            throw ('GET {0}: в ответе нет корня приложения' -f $path)
        }
    }
}

Assert-Step 'порт консоли не пропускает поддельную личность агента' {
    $forged = @{ 'X-Client-Verify' = 'SUCCESS'; 'X-Client-Serial' = '1'; 'X-Client-DN' = 'CN=attacker' }
    $r = Invoke-Api -Method GET -Url "$base/gateway/v1/config" -Headers $forged
    Expect-Equal $r.Status 404 'GET /gateway/v1/config на порту консоли с подделанными заголовками'
}

Write-Host ''
Write-Host 'Все проверки пройдены.' -ForegroundColor Green
exit 0
