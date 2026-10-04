# BarysGuard DLP — запуск сервера

## Быстрый старт на стенде (Docker)

Самый короткий путь увидеть систему целиком. Нужны Docker Desktop и PowerShell
(команды запускаются из cmd.exe или PowerShell, не из Git Bash).

```powershell
.\stand.cmd up      # собрать и запустить (первая сборка — около 10 минут)
.\smoke.cmd         # проверить, что всё работает
```

Консоль: http://localhost:8080, логин `admin`, пароль `stand-admin-password`.
Подробности и остальные команды — в `deploy/stand/README.md`. Все тесты
репозитория одной командой: `.	est.cmd`.

Ручной запуск по шагам, описанный ниже, нужен для разработки сервера и для
понимания, из чего стенд собран.

## Требования

- Linux (проверено на Ubuntu 24.04) либо Windows с WSL2
- Python 3.12+
- PostgreSQL 16+ — в контейнере или уже установленный

## Запуск

### База данных

С Docker:

```bash
docker compose -f deploy/docker-compose.dev.yml up -d
```

Без Docker, на уже работающем PostgreSQL, — один раз создать роль и базу:

```sql
CREATE ROLE barysguard LOGIN PASSWORD 'barysguard' CREATEDB;
CREATE DATABASE barysguard OWNER barysguard;
```

### Сервер

```bash
cd server
python -m venv .venv
.venv/bin/pip install -e ".[dev]"

export BG_DATABASE_URL="postgresql+asyncpg://barysguard:barysguard@localhost:5432/barysguard"
export BG_CA_DIR="$PWD/.local/pki"
export BG_CA_PASSPHRASE="смените-это-значение"

.venv/bin/alembic upgrade head
.venv/bin/barysguard-admin create-user --username admin --role admin
```

Команда напечатает ключ API. Он показывается один раз.

```bash
.venv/bin/uvicorn barysguard.main:app --host 0.0.0.0 --port 8000
```

В Windows без WSL исполняемые файлы окружения лежат в `.venv/Scripts/`, а не в `.venv/bin/`.

## Проверка

```bash
export BG_ADMIN_KEY="<ключ из create-user>"

# 1. Сервер жив
curl -s localhost:8000/health

# 2. Удостоверяющий центр создан
curl -s localhost:8000/gateway/v1/ca | head -1

# 3. Оператор выпускает токен регистрации
curl -s -X POST localhost:8000/api/v1/enrollment-tokens \
     -H "X-Api-Key: $BG_ADMIN_KEY" \
     -H "Content-Type: application/json" \
     -d '{"max_uses": 1}'

# 4. Агент регистрируется. Ниже — эмуляция curl'ом, она показывает сам
#    протокол. Настоящий агент — в разделе «Подключение агента».
openssl ecparam -genkey -name prime256v1 -out /tmp/agent.key
openssl req -new -key /tmp/agent.key -subj "/CN=ignored" -out /tmp/agent.csr

python - <<'PY'
import json, urllib.request
csr = open("/tmp/agent.csr").read()
body = json.dumps({
    "token": "<ТОКЕН ИЗ ШАГА 3>",
    "csr_pem": csr,
    "host": {"machine_id": "test-machine-01", "hostname": "TEST",
             "os": "linux", "os_version": "6.8.0", "arch": "amd64",
             "agent_version": "0.1.0"},
}).encode()
req = urllib.request.Request("http://localhost:8000/gateway/v1/enroll",
                             data=body, headers={"Content-Type": "application/json"})
print(json.loads(urllib.request.urlopen(req).read())["agent_id"])
PY

# 5. Агент виден в списке
curl -s localhost:8000/api/v1/agents -H "X-Api-Key: $BG_ADMIN_KEY"
```

## Проверка heartbeat и команд

Серийный номер берётся из выданного сертификата и приводится к нижнему регистру
без ведущих нулей — в такой форме он лежит в `agent_certificates`. Заголовки
`X-Client-*` здесь подставляются вручную вместо nginx.

```bash
export BG_SERIAL="<серийный номер сертификата>"
export BG_AGENT_ID="<agent_id из шага 4>"

# 1. Оператор ставит команду в очередь
curl -s -X POST localhost:8000/api/v1/agents/$BG_AGENT_ID/commands \
     -H "X-Api-Key: $BG_ADMIN_KEY" \
     -H "Content-Type: application/json" \
     -d '{"type": "ping", "payload": {}, "ttl_seconds": 3600}'

# 2. Heartbeat агента: команда должна прийти ровно один раз
heartbeat() {
  curl -s -X POST localhost:8000/gateway/v1/heartbeat \
       -H "X-Client-Verify: SUCCESS" \
       -H "X-Client-Serial: $BG_SERIAL" \
       -H "Content-Type: application/json" \
       -d "{\"agent_version\": \"0.1.0\", \"config_version\": 0,
            \"sent_at\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",
            \"buffered_events\": 0, \"buffer_bytes\": 0}"
}
heartbeat

# 3. Повторный heartbeat: список commands обязан быть пуст
heartbeat

# 4. Агент отчитывается о выполнении (идентификатор команды из шага 2)
curl -s -X POST localhost:8000/gateway/v1/commands/<id команды>/result \
     -H "X-Client-Verify: SUCCESS" \
     -H "X-Client-Serial: $BG_SERIAL" \
     -H "Content-Type: application/json" \
     -d '{"status": "done", "result": {"pong": true}}'
```

Ожидаемое: шаг 2 возвращает одну команду, шаг 3 — пустой список, шаг 4 отвечает
`202`. Повтор шага 4 отвечает `200` и сохранённый результат не меняет.

## Проверка наследования конфигурации

```bash
# 1. Текущая конфигурация
curl -s localhost:8000/api/v1/config -H "X-Api-Key: $BG_ADMIN_KEY"

# 2. Правка глобальной конфигурации. PUT заменяет документ целиком,
#    поэтому передаётся полный набор секций.
curl -s -X PUT localhost:8000/api/v1/config \
     -H "X-Api-Key: $BG_ADMIN_KEY" \
     -H "Content-Type: application/json" \
     -d '{"document": {
            "transport": {"heartbeat_interval_seconds": 15, "event_batch_max": 500,
                          "event_batch_max_bytes": 4194304,
                          "backoff_base_seconds": 1, "backoff_max_seconds": 300},
            "buffer": {"max_bytes": 524288000, "max_age_days": 7},
            "logging": {"level": "debug"},
            "policies": {}}}'

# 3. Агент забирает документ и видит новую версию
curl -s localhost:8000/gateway/v1/config \
     -H "X-Client-Verify: SUCCESS" \
     -H "X-Client-Serial: $BG_SERIAL"

# 4. Эффективная конфигурация агента глазами оператора. applied_version
#    отличается от version, пока агент не прислал heartbeat с новой версией:
#    так видно, кто отстал.
curl -s localhost:8000/api/v1/agents/$BG_AGENT_ID/config -H "X-Api-Key: $BG_ADMIN_KEY"
```

Ожидаемое: после шага 2 `version` в ответе шага 3 отличается от полученной при
регистрации, а `heartbeat_interval_seconds` равен 15.

## Тесты

Интеграционные тесты идут на настоящем PostgreSQL: на каждый прогон создаётся
временная база, которая затем удаляется. Сервер берётся из `BG_TEST_DATABASE_URL`;
без этой переменной PostgreSQL поднимается в контейнере через testcontainers,
и тогда нужен работающий Docker.

```bash
cd server
export BG_TEST_DATABASE_URL="postgresql+asyncpg://barysguard:barysguard@localhost:5432/barysguard"
.venv/bin/python -m pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy barysguard
```

Роль в `BG_TEST_DATABASE_URL` должна иметь право `CREATEDB`.

## Переменные окружения

| Переменная | Обязательна | Назначение |
|---|---|---|
| `BG_DATABASE_URL` | да | Подключение к PostgreSQL |
| `BG_CA_PASSPHRASE` | да | Парольная фраза ключа удостоверяющего центра |
| `BG_CA_DIR` | нет | Каталог удостоверяющего центра, по умолчанию `/var/lib/barysguard/pki` |
| `BG_LOG_LEVEL` | нет | Уровень журналирования, по умолчанию `INFO` |
| `BG_TEST_DATABASE_URL` | нет | Сервер PostgreSQL для интеграционных тестов |

`BG_CA_PASSPHRASE` при утрате делает невозможным выпуск и продление сертификатов —
весь флот придётся регистрировать заново. Хранить вне сервера.

## Развёртывание за nginx

`deploy/nginx/barysguard.conf` завершает TLS и проверяет клиентские сертификаты
агентов, передавая результат приложению заголовками `X-Client-*`. Директивы
`proxy_set_header` в этом файле — единственное, что мешает клиенту объявить себя
чужим агентом: они безусловно затирают одноимённые заголовки из запроса.
Приложение слушает только `127.0.0.1` и наружу напрямую не выставляется.

## Подключение агента

Агент — отдельный модуль Go в каталоге `agent/`. Общего кода с сервером нет:
их связывает только контракт `api/gateway-v1.yaml`.

### Сборка

```bash
cd agent
go build -o barysguard-agent ./cmd/barysguard-agent

# Кросс-сборка под второй целевой хост
GOOS=linux   go build -o barysguard-agent       ./cmd/barysguard-agent
GOOS=windows go build -o barysguard-agent.exe   ./cmd/barysguard-agent
```

Версия зашивается при сборке: `-ldflags "-X main.agentVersion=0.1.0"`.

### Регистрация

Агент обязан знать удостоверяющий центр **до** первого запроса. Сертификат CA
берётся из дистрибутива — его отдаёт `GET /gateway/v1/ca`, и он же лежит
в `$BG_CA_DIR/ca.crt` на сервере.

```bash
./barysguard-agent enroll \
  -server https://dlp.example:8443 \
  -token "BG-ENROLL-..." \
  -ca-file /path/to/ca.crt \
  -data-dir ./agent-data
```

Если файла CA под рукой нет, его можно забрать по сети, но **только**
с проверкой отпечатка:

```bash
# Отпечаток считается на сервере и передаётся установщику отдельно от сети
openssl x509 -in /var/lib/barysguard/pki/ca.crt -outform der | sha256sum

./barysguard-agent enroll -server https://dlp.example:8443 \
  -token "BG-ENROLL-..." -ca-pin "<полученный sha256>"
```

Без `-ca-file` или `-ca-pin` регистрация отказывает. Это не придирка:
скачать CA по непроверенному каналу и тут же начать ему доверять — ровно тот
перехват, против которого и вводится mTLS.

Повторная регистрация поверх действующей отклоняется; перезаписать личность
агента можно только явным `-force`.

### Работа

```bash
./barysguard-agent run    -data-dir ./agent-data   # цикл heartbeat
./barysguard-agent status -data-dir ./agent-data   # локальная диагностика
```

`run` шлёт heartbeat с интервалом, который задаёт сервер, забирает
конфигурацию при расхождении версий, исполняет команды `ping`,
`refresh_config` и `collect_diagnostics` и продлевает сертификат
по достижении 2/3 срока.

Коды возврата: `0` — штатное завершение по сигналу, `2` — сервер отказал
в обслуживании (сертификат отозван), `1` — прочие ошибки. Код `2` выделен
ради `RestartPreventExitStatus=2` в юните systemd: отозванного агента
не нужно поднимать заново.

### Полный стенд с nginx

Личность агента сервер определяет по заголовкам `X-Client-*`, которые
проставляет nginx после проверки клиентского сертификата. На голом
`docker-compose.dev.yml` проходит только регистрация: она идёт по пути,
не требующему сертификата. Для heartbeat, команд и конфигурации нужен
обратный прокси.

Сначала выпустить серверный сертификат внутренним CA стенда. Он подписывает
и клиентские сертификаты, поэтому агент проверит цепочку тем же файлом:

```bash
cd server
export BG_TLS_DIR="$PWD/.local/tls"
mkdir -p "$BG_TLS_DIR"

.venv/bin/python - <<'PY'
import datetime as dt, ipaddress, os
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

ca_dir, out = Path(os.environ["BG_CA_DIR"]), Path(os.environ["BG_TLS_DIR"])
ca_cert = x509.load_pem_x509_certificate((ca_dir / "ca.crt").read_bytes())
ca_key = serialization.load_pem_private_key(
    (ca_dir / "ca.key").read_bytes(), password=os.environ["BG_CA_PASSPHRASE"].encode()
)

key = ec.generate_private_key(ec.SECP256R1())
now = dt.datetime.now(dt.UTC)
cert = (
    x509.CertificateBuilder()
    .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
    .issuer_name(ca_cert.subject).public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now - dt.timedelta(minutes=5))
    .not_valid_after(now + dt.timedelta(days=365))
    .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    .add_extension(x509.SubjectAlternativeName([
        x509.DNSName("localhost"), x509.DNSName("host.docker.internal"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    ]), critical=False)
    .sign(ca_key, hashes.SHA256())
)
(out / "server.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
(out / "server.key").write_bytes(key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption()))
(out / "ca.crt").write_bytes((ca_dir / "ca.crt").read_bytes())
PY
```

Затем поднять прокси и перезапустить приложение так, чтобы контейнер его видел:

```bash
# Приложение слушает 0.0.0.0, иначе из контейнера до него не достучаться.
# На общей сети так делать не следует.
.venv/bin/uvicorn barysguard.main:app --host 0.0.0.0 --port 8000 &

docker compose -f deploy/docker-compose.dev.yml \
               -f deploy/docker-compose.nginx.yml up -d
```

Проверка контура целиком:

```bash
./barysguard-agent enroll -server https://localhost:8443 \
  -token "BG-ENROLL-..." -ca-file "$BG_TLS_DIR/ca.crt" -data-dir ./agent-data
./barysguard-agent run -data-dir ./agent-data
```

После первого heartbeat `GET /api/v1/agents` показывает агента со статусом
`active` и непустым `last_heartbeat_at`, а поставленные команды приходят
с результатами.

> **Если порт 5432 уже занят.** Локально установленный PostgreSQL перехватит
> порт раньше контейнера, и приложение молча уйдёт работать в него: ошибки
> не будет, но таблицы окажутся не там, где вы их ищете. Проверить, кто
> слушает порт, стоит до запуска миграций.

### Тесты агента

```bash
cd agent
go vet ./... && go test ./...
```

Тесты транспорта поднимают `httptest` с настоящим TLS и требованием
клиентского сертификата, поэтому проверяют, что агент его действительно
предъявляет. Сквозной тест против живого сервера вынесен под build tag
и в обычный прогон не входит:

```bash
go test -tags e2e ./e2e/ -v \
  -server http://127.0.0.1:8000 \
  -api-key "<ключ оператора>" \
  -ca "<файл, полученный из GET /gateway/v1/ca>"
```

Токены тест выпускает сам через операторский API. Общий токен из флага
связал бы сценарии единым бюджетом использований, и они начали бы падать
друг от друга, а не от дефектов агента.
