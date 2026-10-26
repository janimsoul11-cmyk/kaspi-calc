# Перевыпуск ключей: записывает новые токен Telegram, ключ Anthropic и ключ Groq в /opt/assistant/.env
# на сервере и перезапускает бота. Ключи вводятся скрыто (видны только звёздочки) и уходят на сервер
# через stdin ssh, а не в командной строке. Проверку, запись и перезапуск делает deploy/set_keys.py.
param([string]$HostAlias = "sellmir")
$ErrorActionPreference = "Stop"

$fields = [ordered]@{
    TELEGRAM_BOT_TOKEN = @{ Prompt = "Токен бота от @BotFather (123456789:AA...)"; Pattern = '^\d{5,}:[A-Za-z0-9_-]{30,}$' }
    ANTHROPIC_API_KEY  = @{ Prompt = "Ключ Anthropic (sk-ant-...)"; Pattern = '^sk-ant-[A-Za-z0-9_-]{20,}$' }
    STT_API_KEY        = @{ Prompt = "Ключ Groq (gsk_...)"; Pattern = '^[A-Za-z0-9_-]{20,}$' }
}

function Read-Secret([string]$prompt) {
    $secure = Read-Host -AsSecureString $prompt
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return ([Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)).Trim() }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

Write-Host "Вставь каждый новый ключ (правая кнопка мыши или Ctrl+V) и нажми Enter."
Write-Host "Пустой Enter - оставить этот ключ как есть.`n"
$new = [ordered]@{}
foreach ($name in $fields.Keys) {
    while ($true) {
        $value = Read-Secret $fields[$name].Prompt
        if (-not $value) { Write-Host "  пропущен"; break }
        if ($value -match $fields[$name].Pattern) { $new[$name] = $value; Write-Host "  принят ($($value.Length) символов)"; break }
        Write-Host "  Не похоже на $name. Скопируй ключ целиком ещё раз."
    }
}
if ($new.Count -eq 0) { Write-Host "`nНи одного ключа не введено, ничего не меняю."; exit 1 }

# Код и ключи идут через stdin: в командной строке их не видно, и нет предела ~8 КБ на длину команды ssh.
$code = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $PSScriptRoot "set_keys.py")))
$OutputEncoding = [Text.UTF8Encoding]::new($false)
Write-Host "`nОтправляю на сервер $HostAlias..."
"$code`n$($new | ConvertTo-Json -Compress)" | ssh $HostAlias "python3 -c 'import base64,sys;exec(base64.b64decode(sys.stdin.readline()))'"
$status = $LASTEXITCODE
$new.Clear()

if ($status -eq 0) {
    Write-Host "`nГотово. Отправь боту /today. Если ответил - удали СТАРЫЕ ключи в консолях Anthropic и Groq."
} elseif ($status -eq 2) {
    Write-Host "`nКлючи записаны, но бот после перезапуска выглядит нездоровым - смотри журнал выше."
} else {
    Write-Host "`nКлючи не записаны (код $status). Бот работает как раньше."
}
exit $status
