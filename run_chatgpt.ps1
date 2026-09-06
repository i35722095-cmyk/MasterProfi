$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$storedKey = [Environment]::GetEnvironmentVariable("OPENAI_API_KEY", "User")
if ([string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY) -and -not [string]::IsNullOrWhiteSpace($storedKey)) {
    $env:OPENAI_API_KEY = $storedKey
}

if ([string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY)) {
    $secureKey = Read-Host "Введите OpenAI API key" -AsSecureString
    $env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new("", $secureKey).Password
}

if (Test-Path -LiteralPath ".venv\Scripts\python.exe") {
    & ".venv\Scripts\python.exe" -u -m source.main
} else {
    & python -u -m source.main
}

if ($LASTEXITCODE -ne 0) {
    Read-Host "Проект завершился с ошибкой. Нажмите Enter, чтобы закрыть окно"
}
