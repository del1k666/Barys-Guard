#!/bin/sh
# Вход контейнера с агентом: при первом запуске регистрирует его токеном
# своей группы, затем запускает рабочий цикл.
set -eu

DATA_DIR="${BG_AGENT_DATA_DIR:-/var/lib/agent}"
ENROLL_DIR="${BG_ENROLL_DIR:-/enroll}"
TLS_DIR="${BG_TLS_DIR:-/tls}"
MACHINE_ID_FILE="${BG_MACHINE_ID_FILE:-/etc/machine-id}"
SERVER_URL="${BG_SERVER_URL:-https://nginx:8443}"
RETRIES="${BG_ENROLL_RETRIES:-30}"
DELAY="${BG_ENROLL_RETRY_DELAY:-2}"

if [ -z "${BG_AGENT_GROUP:-}" ]; then
  echo "BG_AGENT_GROUP не задана: укажите группу (имя файла токена без .token)" >&2
  exit 64
fi

# Агент на Linux читает /etc/machine-id и без него не запускается, а в
# контейнере файла нет. Идентификатор выводится из имени хоста: у разных
# контейнеров он разный, а у одного и того же стабилен между перезапусками.
# Без этого все агенты стенда оказались бы одной машиной.
if [ ! -s "$MACHINE_ID_FILE" ]; then
  HOST_NAME="${BG_HOSTNAME:-$(hostname)}"
  printf '%s' "$HOST_NAME" | sha256sum | cut -c1-32 > "$MACHINE_ID_FILE"
fi

# Зарегистрированный агент (его данные лежат в томе) повторно не регистрируется:
# сервер отклонил бы это, а токен остался бы потраченным зря.
if ! barysguard-agent status -data-dir "$DATA_DIR" > /dev/null 2>&1; then
  TOKEN_FILE="$ENROLL_DIR/$BG_AGENT_GROUP.token"
  attempt=1
  while :; do
    # nginx и сервер могут быть ещё не готовы; токен — не пустой файл.
    if [ -s "$TOKEN_FILE" ] \
      && barysguard-agent enroll \
        -server "$SERVER_URL" \
        -token "$(cat "$TOKEN_FILE")" \
        -ca-file "$TLS_DIR/ca.crt" \
        -data-dir "$DATA_DIR"; then
      break
    fi
    if [ "$attempt" -ge "$RETRIES" ]; then
      echo "регистрация не удалась за $RETRIES попыток" >&2
      exit 1
    fi
    attempt=$((attempt + 1))
    sleep "$DELAY"
  done
fi

# Агент запускается дочерним процессом, а не через exec: нужно различить
# код 2 («сервер отказал в обслуживании: сертификат отозван»). Для compose с
# restart: on-failure это штатное завершение, и перезапускать отозванного
# агента по кругу незачем. Сигналы остановки пересылаются агенту.
barysguard-agent run -data-dir "$DATA_DIR" &
child=$!
trap 'kill -TERM "$child" 2>/dev/null || true' TERM INT

code=0
wait "$child" || code=$?
# Сигнал прерывает первый wait кодом больше 128: дожидаемся самого агента.
if [ "$code" -gt 128 ]; then
  code=0
  wait "$child" || code=$?
fi

if [ "$code" -eq 2 ]; then
  echo "сервер отказал агенту в обслуживании (сертификат отозван): остановка без перезапуска" >&2
  exit 0
fi
exit "$code"
