# Общие помощники скриптов стенда. Windows PowerShell 5.1 совместим.
Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'

$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$script:StandDir = Join-Path $script:RepoRoot 'deploy\stand'
$script:ComposeFile = Join-Path $script:StandDir 'docker-compose.yml'
$script:EnvFile = Join-Path $script:StandDir '.env'
$script:ProjectName = 'barysguard-stand'

function Get-StandEnv {
    # Значения по умолчанию совпадают с .env.example; .env их переопределяет.
    $values = @{
        BG_STAND_ADMIN_USERNAME = 'admin'
        BG_STAND_ADMIN_PASSWORD = 'stand-admin-password'
        STAND_CONSOLE_PORT      = '8080'
        STAND_AGENT_PORT        = '8443'
    }
    if (Test-Path $script:EnvFile) {
        foreach ($line in Get-Content -Path $script:EnvFile -Encoding UTF8) {
            if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
            $key, $value = $line.Split('=', 2)
            $values[$key.Trim()] = $value.Trim()
        }
    }
    return $values
}

# Вызовы docker пишут в stderr даже при успехе (прогресс, предупреждения);
# при $ErrorActionPreference = 'Stop' Windows PowerShell 5.1 принимает это за
# ошибку. Поэтому каждый вызов идёт при 'Continue', а результат читается по
# $LASTEXITCODE. Параметры функций объявлены через $args, а не param(...):
# у функции с [Parameter()] появляются общие параметры (-Debug, -Verbose), и
# PowerShell съел бы флаги docker -d и -v как их сокращения.
function Invoke-Docker {
    $words = @($args | ForEach-Object { $_ })  # разворачивает переданные массивы
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & docker @words 2>$null
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    return @{ Code = $code; Output = @($output | Where-Object { $_ }) }
}

function Test-DockerRunning {
    try {
        return ((Invoke-Docker info).Code -eq 0)
    }
    catch {
        return $false
    }
}

function Invoke-Compose {
    $words = @($args | ForEach-Object { $_ })  # разворачивает переданные массивы
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & docker compose --project-directory $script:StandDir -f $script:ComposeFile @words
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    if ($code -ne 0) {
        throw "docker compose завершился с кодом $code"
    }
}

function Test-PortFree {
    param([int]$Port)
    $listener = $null
    try {
        $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
        $listener.Start()
        return $true
    }
    catch {
        return $false
    }
    finally {
        if ($null -ne $listener) { $listener.Stop() }
    }
}

function New-ApiSession {
    return New-Object Microsoft.PowerShell.Commands.WebRequestSession
}

# Один HTTP-вызов. Не бросает исключение на 4xx/5xx: возвращает Status, Json
# (разобранное тело или $null) и Text. Status = 0 — соединения нет.
function Invoke-Api {
    param(
        [string]$Method,
        [string]$Url,
        $Body = $null,
        $Session = $null,
        [hashtable]$Headers = @{},
        [int]$TimeoutSec = 15
    )
    $params = @{
        Uri             = $Url
        Method          = $Method
        UseBasicParsing = $true
        TimeoutSec      = $TimeoutSec
        Headers         = $Headers
    }
    if ($null -ne $Session) { $params.WebSession = $Session }
    if ($null -ne $Body) {
        $json = $Body | ConvertTo-Json -Depth 6 -Compress
        # Байтами, чтобы кириллица не превратилась в ISO-8859-1.
        $params.Body = [System.Text.Encoding]::UTF8.GetBytes($json)
        $params.ContentType = 'application/json; charset=utf-8'
    }

    $status = 0
    $text = ''
    try {
        $response = Invoke-WebRequest @params
        $status = [int]$response.StatusCode
        $text = $response.Content
    }
    catch [System.Net.WebException] {
        if ($null -eq $_.Exception.Response) {
            return [pscustomobject]@{ Status = 0; Json = $null; Text = $_.Exception.Message }
        }
        $status = [int]$_.Exception.Response.StatusCode
        $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
        $text = $reader.ReadToEnd()
        $reader.Dispose()
    }

    $parsed = $null
    if ($text) {
        try { $parsed = $text | ConvertFrom-Json } catch { $parsed = $null }
    }
    return [pscustomobject]@{ Status = $status; Json = $parsed; Text = $text }
}
