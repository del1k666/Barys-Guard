#!/bin/sh
# Тест entrypoint.sh на подменном агенте: настоящий бинарник не нужен.
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/entrypoint.sh"
FAILED=0
SANDBOXES=""

cleanup() {
  for d in $SANDBOXES; do rm -rf "$d"; done
}
trap cleanup EXIT

fail() {
  echo "FAIL: $1" >&2
  FAILED=1
}

new_sandbox() {
  SANDBOX="$(mktemp -d)"
  SANDBOXES="$SANDBOXES $SANDBOX"
  mkdir -p "$SANDBOX/bin" "$SANDBOX/enroll" "$SANDBOX/tls" "$SANDBOX/data"
  echo "BG-ENROLL-TESTTOKEN" > "$SANDBOX/enroll/accounting.token"
  : > "$SANDBOX/tls/ca.crt"
  LOG="$SANDBOX/calls.log"
  : > "$LOG"
  cat > "$SANDBOX/bin/barysguard-agent" <<'FAKE'
#!/bin/sh
echo "$*" >> "$FAKE_LOG"
case "$1" in
  status)
    [ -f "$FAKE_DIR/enrolled" ]
    ;;
  enroll)
    n=$(cat "$FAKE_DIR/attempts" 2>/dev/null || echo 0)
    n=$((n + 1))
    echo "$n" > "$FAKE_DIR/attempts"
    [ "$n" -gt "${FAKE_ENROLL_FAILURES:-0}" ] || exit 1
    : > "$FAKE_DIR/enrolled"
    ;;
  run)
    if [ "${FAKE_RUN_WAIT:-0}" = 1 ]; then
      trap 'echo TERM >> "$FAKE_DIR/got-term"; exit 0' TERM
      sleep 30 &
      wait $!
      wait $!
    fi
    exit "${FAKE_RUN_EXIT:-0}"
    ;;
esac
FAKE
  chmod +x "$SANDBOX/bin/barysguard-agent"
}

# Запускает entrypoint в песочнице; код возврата кладёт в RC. Переменные
# окружения передаются аргументами (NAME=значение): присваивания перед вызовом
# функции в разных оболочках доходят до дочерних команд по-разному.
run_entrypoint() {
  RC=0
  env     PATH="$SANDBOX/bin:$PATH" FAKE_LOG="$LOG" FAKE_DIR="$SANDBOX"     BG_AGENT_DATA_DIR="$SANDBOX/data"     BG_ENROLL_DIR="$SANDBOX/enroll"     BG_TLS_DIR="$SANDBOX/tls"     BG_MACHINE_ID_FILE="$SANDBOX/machine-id"     BG_ENROLL_RETRY_DELAY=0     "$@"     sh "$SCRIPT" > "$SANDBOX/out.txt" 2>&1 || RC=$?
}

count_calls() {
  grep -c -- "^$1 " "$LOG" || true
}

# 1. Свежий агент: сначала регистрация с токеном группы и CA, потом работа.
new_sandbox
run_entrypoint BG_AGENT_GROUP=accounting
[ "$RC" -eq 0 ] || fail "свежий агент: код возврата $RC"
grep -qF -- "enroll -server https://nginx:8443 -token BG-ENROLL-TESTTOKEN -ca-file $SANDBOX/tls/ca.crt -data-dir $SANDBOX/data" "$LOG" \
  || fail "свежий агент: enroll вызван не с теми аргументами: $(cat "$LOG")"
grep -qF -- "run -data-dir $SANDBOX/data" "$LOG" || fail "свежий агент: run не вызван"

# 2. Уже зарегистрированный агент не регистрируется повторно.
new_sandbox
: > "$SANDBOX/enrolled"
run_entrypoint BG_AGENT_GROUP=accounting
[ "$(count_calls enroll)" -eq 0 ] || fail "зарегистрированный агент: enroll вызван повторно"
[ "$(count_calls run)" -eq 1 ] || fail "зарегистрированный агент: run не вызван"

# 3. Без группы — код 64 и понятное сообщение.
new_sandbox
env -u BG_AGENT_GROUP sh -c '
  PATH="$1/bin:$PATH" FAKE_LOG="$2" FAKE_DIR="$1" BG_MACHINE_ID_FILE="$1/machine-id" sh "$3"
' _ "$SANDBOX" "$LOG" "$SCRIPT" > "$SANDBOX/out.txt" 2>&1 && RC=0 || RC=$?
[ "$RC" -eq 64 ] || fail "без группы: ожидался код 64, получен $RC"
grep -q "BG_AGENT_GROUP" "$SANDBOX/out.txt" || fail "без группы: нет сообщения про BG_AGENT_GROUP"

# 4. machine_id: 32 символа, зависит от имени хоста, стабилен между запусками.
new_sandbox
run_entrypoint BG_AGENT_GROUP=accounting BG_HOSTNAME=agent-1
first="$(cat "$SANDBOX/machine-id")"
[ "${#first}" -eq 32 ] || fail "machine_id: длина ${#first}, ожидалось 32"
new_sandbox
run_entrypoint BG_AGENT_GROUP=accounting BG_HOSTNAME=agent-2
second="$(cat "$SANDBOX/machine-id")"
[ "$first" != "$second" ] || fail "machine_id: у разных хостов совпал"
new_sandbox
run_entrypoint BG_AGENT_GROUP=accounting BG_HOSTNAME=agent-1
[ "$first" = "$(cat "$SANDBOX/machine-id")" ] || fail "machine_id: у того же хоста изменился"

# 5. Уже выданный machine_id не перезаписывается.
new_sandbox
echo "keepmekeepmekeepmekeepmekeepme12" > "$SANDBOX/machine-id"
run_entrypoint BG_AGENT_GROUP=accounting BG_HOSTNAME=agent-9
[ "$(cat "$SANDBOX/machine-id")" = "keepmekeepmekeepmekeepmekeepme12" ] \
  || fail "machine_id: существующий перезаписан"

# 6. Регистрация повторяется, пока nginx не поднимется.
new_sandbox
run_entrypoint FAKE_ENROLL_FAILURES=2 BG_AGENT_GROUP=accounting
[ "$RC" -eq 0 ] || fail "повтор регистрации: код возврата $RC"
[ "$(count_calls enroll)" -eq 3 ] || fail "повтор регистрации: попыток $(count_calls enroll), ожидалось 3"
[ "$(count_calls run)" -eq 1 ] || fail "повтор регистрации: run не вызван"

# 7. Попытки конечны: после исчерпания код 1 и работа не начинается.
new_sandbox
run_entrypoint FAKE_ENROLL_FAILURES=99 BG_ENROLL_RETRIES=3 BG_AGENT_GROUP=accounting
[ "$RC" -eq 1 ] || fail "исчерпание попыток: ожидался код 1, получен $RC"
[ "$(count_calls enroll)" -eq 3 ] || fail "исчерпание попыток: попыток $(count_calls enroll)"
[ "$(count_calls run)" -eq 0 ] || fail "исчерпание попыток: run вызван"

# 8. Нет файла токена: регистрация даже не пытается идти с пустым токеном.
new_sandbox
rm "$SANDBOX/enroll/accounting.token"
run_entrypoint BG_ENROLL_RETRIES=2 BG_AGENT_GROUP=accounting
[ "$RC" -eq 1 ] || fail "нет токена: ожидался код 1, получен $RC"
[ "$(count_calls enroll)" -eq 0 ] || fail "нет токена: enroll вызван с пустым токеном"

# 9. Код 2 от агента (сертификат отозван) — штатная остановка, код 0.
new_sandbox
: > "$SANDBOX/enrolled"
run_entrypoint BG_AGENT_GROUP=accounting FAKE_RUN_EXIT=2
[ "$RC" -eq 0 ] || fail "отозванный агент: ожидался код 0, получен $RC"
grep -q "отозван" "$SANDBOX/out.txt" || fail "отозванный агент: нет объяснения в журнале"

# 10. Любая другая ошибка агента пробрасывается как есть: compose её перезапустит.
new_sandbox
: > "$SANDBOX/enrolled"
run_entrypoint BG_AGENT_GROUP=accounting FAKE_RUN_EXIT=1
[ "$RC" -eq 1 ] || fail "сбой агента: ожидался код 1, получен $RC"

# 11. Код 137 (например, OOM) пробрасывается как есть, а не превращается в 127.
new_sandbox
: > "$SANDBOX/enrolled"
run_entrypoint BG_AGENT_GROUP=accounting FAKE_RUN_EXIT=137
[ "$RC" -eq 137 ] || fail "убитый агент: ожидался код 137, получен $RC"

# 12. Сигнал остановки пересылается агенту, вход завершается его кодом.
new_sandbox
: > "$SANDBOX/enrolled"
env PATH="$SANDBOX/bin:$PATH" FAKE_LOG="$LOG" FAKE_DIR="$SANDBOX" FAKE_RUN_WAIT=1   BG_AGENT_GROUP=accounting BG_AGENT_DATA_DIR="$SANDBOX/data"   BG_MACHINE_ID_FILE="$SANDBOX/machine-id"   sh "$SCRIPT" > "$SANDBOX/out.txt" 2>&1 &
EP=$!
i=0
while [ "$i" -lt 100 ] && [ "$(count_calls run)" -eq 0 ]; do
  sleep 0.1
  i=$((i + 1))
done
sleep 0.3
kill -TERM "$EP" 2>/dev/null || true
i=0
while [ "$i" -lt 100 ] && kill -0 "$EP" 2>/dev/null; do
  sleep 0.1
  i=$((i + 1))
done
if kill -0 "$EP" 2>/dev/null; then
  kill -KILL "$EP" 2>/dev/null || true
  pkill -f "sleep 30" 2>/dev/null || true
  fail "сигнал: вход не завершился за 10 с"
fi
RC=0
wait "$EP" || RC=$?
[ "$RC" -eq 0 ] || fail "сигнал: ожидался код 0, получен $RC"
[ -f "$SANDBOX/got-term" ] || fail "сигнал: агент не получил TERM"

if [ "$FAILED" -eq 0 ]; then
  echo "entrypoint_test: все проверки прошли"
fi
exit "$FAILED"
