# Сборщики «Файлы и USB»: ручная проверка на Windows

Что умеет агент после цикла 2b-1: присылает на сервер события подключения и
отключения внешних носителей (`usb/mount`, `usb/unmount`) и операций с файлами
(`file/create`, `modify`, `rename`, `delete`, `copy`) в наблюдаемых папках и на
внешних дисках. Это **аудит**: агент видит действие после того, как оно
произошло, и заблокировать его не может.

## Подготовка

Стенд запущен (`.\stand.cmd up`), агент собран и зарегистрирован. Если нет:

```powershell
$work = 'C:\BarysGuardTest'
New-Item -ItemType Directory -Force $work | Out-Null
cd C:\Users\User\Desktop\BarysGuard\BarysGuard\agent
go build -o "$work\barysguard-agent.exe" .\cmd\barysguard-agent
docker cp barysguard-stand-nginx-1:/etc/barysguard/tls/ca.crt "$work\ca.crt"

$h = @{ Origin = 'http://localhost:8080' }
$null = Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/v1/auth/login -Headers $h `
  -ContentType 'application/json' -SessionVariable s `
  -Body '{"username":"admin","password":"stand-admin-password"}'
$tok = (Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/v1/enrollment-tokens -Headers $h `
  -WebSession $s -ContentType 'application/json' -Body '{"max_uses":1}').token
& "$work\barysguard-agent.exe" enroll -server https://localhost:8443 -token $tok `
  -ca-file "$work\ca.crt" -data-dir "$work\data"
```

Запуск. **Для наблюдения за папками всех пользователей запускайте PowerShell от
администратора**; без прав агент наблюдает только то, к чему у него есть доступ,
а для остальных папок присылает `agent/watch_denied`.

```powershell
& "$work\barysguard-agent.exe" run -data-dir "$work\data"
```

События уходят на сервер вместе с очередным heartbeat (раз в 30 секунд), поэтому
после действия подождите до полминуты. Смотреть удобно в браузере, где вы вошли
в консоль (`http://localhost:8080`): откройте адреса ниже. Либо из PowerShell
через `$s` из блока выше.

| Что смотреть | Адрес |
|---|---|
| Все события | `http://localhost:8080/api/v1/events` |
| Только файлы | `http://localhost:8080/api/v1/events?channel=file` |
| Только USB | `http://localhost:8080/api/v1/events?channel=usb` |
| Только копирования | `http://localhost:8080/api/v1/events?channel=file&action=copy` |

## Сценарии

1. **Файлы в «Документах».** Создайте файл в `Документах`, допишите в него строку,
   переименуйте, удалите. Ожидается: `file/create`, `modify` (хеш `artifact.sha256`
   изменился), `rename` (в `subject` есть `old_path`), `delete`; в `actor` ваш
   пользователь. Каждое действие отделяйте паузой в 3–4 секунды: события склеиваются.
2. **Подключение флешки.** Вставьте флешку. Ожидается `usb/mount`: буква, метка,
   серийный номер тома, файловая система, размер, производитель и модель
   устройства. Агент, запущенный с уже вставленной флешкой, тоже её увидит.
3. **Копирование на флешку.** Скопируйте из `Документов` файл на флешку.
   Ожидается `file/copy` с `severity=high`, `subject.src_path` (откуда),
   `subject.dst_path` (куда), `artifact.sha256` и, если процесс ещё держал файл
   открытым, `process` (чаще всего `explorer.exe`). Источник находится по
   совпадению хеша с файлом, который агент видел в наблюдаемых папках; если
   файл лежал вне наблюдаемых папок, будет `file/create` на флешке без `src_path`
   (`severity=medium`).
4. **Файл создан прямо на флешке.** `file/create`, `severity=medium`.
5. **Извлечение флешки.** `usb/unmount`. Смена флешки в той же букве даёт
   `unmount` и `mount`.
6. **Большой файл.** Файл больше `max_hash_bytes` (по умолчанию 256 МиБ)
   отправляется без `artifact`, в `labels` стоит `hash: skipped_size`, размер в
   `subject.size_bytes`.
7. **Массовая операция.** Распакуйте архив с сотнями файлов в `Загрузки`. Если
   файлов больше `max_events_per_second` (200) за секунду, часть отбрасывается и
   приходит `agent/events_dropped` с количеством.
8. **Нет прав.** Запустите агента не от администратора: по папкам других
   профилей придёт `agent/watch_denied`, а ваши папки наблюдаются.

## Настройка

Раздел `collectors` в документе конфигурации агента (`PUT /api/v1/config` для
всей организации, `PUT /api/v1/groups/{id}/config` для группы). Значения по
умолчанию:

```json
"collectors": {
  "usb": { "enabled": true, "poll_seconds": 2 },
  "file_watch": {
    "enabled": true,
    "paths": ["%USERS%\\Documents", "%USERS%\\Desktop", "%USERS%\\Downloads"],
    "exclude": ["*\\~$*", "*.tmp", "*.crdownload", "*\\AppData\\*"],
    "stable_ms": 1500, "max_wait_ms": 30000,
    "max_hash_bytes": 268435456, "max_events_per_second": 200
  }
}
```

`%USERS%` раскрывается в профиль каждого пользователя. В `exclude` работают
маски `*` и `?` без учёта регистра. Рабочий каталог агента не наблюдается никогда.
Агент подхватывает смену раздела `collectors` сам, без перезапуска.

## Что агент не умеет в этом цикле

- Блокировать запись на USB: нужен драйвер (minifilter), это отдельная фаза.
- Определять процесс всегда: Restart Manager видит его, только пока файл открыт.
  Если не удалось, событие уходит с `labels.process = "unknown"`.
- Различать каталог и файл при удалении: удаление каталога приходит как `file/delete`.
- Сетевые папки и диски, буфер обмена, печать, содержимое файлов: следующие циклы.
- Работать службой Windows: пока агент запускается из окна терминала.

## Если что-то не так

| Симптом | Что делать |
|---|---|
| Нет событий `file` | подождите до 30 секунд (heartbeat); проверьте, что файл в наблюдаемой папке и имя не подходит под `exclude` |
| Нет событий USB | подождите 2–4 секунды после вставки; внутренние диски событий `usb` не дают |
| `agent/watch_denied` | нет прав на папку; запустите от администратора |
| Кракозябры в логе | `chcp 65001` перед запуском |
| `enroll` отказал | токен использован или просрочен: выпустите новый; повторная регистрация — флаг `-force` |
