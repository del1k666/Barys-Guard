# BarysGuard DLP — запуск сервера

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

# 4. Агент регистрируется (эмуляция; настоящий агент — план 1B)
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
