//go:build e2e

// Сквозной тест против настоящего сервера. Под тегом, потому что требует
// поднятого Docker и запущенного FastAPI: делать его обязательным для
// каждого прогона значило бы останавливать разработку всякий раз,
// когда Docker не поднят.
//
// Регистрация идёт по пути, не требующему клиентского сертификата, поэтому
// её достаточно проверить против локального uvicorn. Heartbeat и команды
// идут по mTLS, а заголовки X-Client-* проставляет nginx — для их проверки
// нужен обратный прокси из deploy/nginx/barysguard.conf.
//
// Токены тест выпускает сам через операторский API: общий токен из флага
// связал бы сценарии общим бюджетом использований, и они начали бы падать
// друг от друга, а не от дефектов агента.
//
// Запуск:
//
//	go test -tags e2e ./e2e/ -v \
//	  -server http://127.0.0.1:8000 -api-key <ключ оператора> -ca /path/ca.crt
package e2e

import (
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

var (
	serverURL = flag.String("server", "", "адрес шлюза")
	apiKey    = flag.String("api-key", "", "ключ оператора для выпуска токенов")
	caPath    = flag.String("ca", "", "сертификат CA")
)

func requireFlags(t *testing.T) {
	t.Helper()
	if *serverURL == "" || *apiKey == "" || *caPath == "" {
		t.Skip("нужны -server, -api-key и -ca")
	}
}

// mintToken выпускает токен регистрации через операторский API.
func mintToken(t *testing.T, maxUses int) string {
	t.Helper()
	body, _ := json.Marshal(map[string]int{"max_uses": maxUses})

	req, err := http.NewRequest(
		http.MethodPost, *serverURL+"/api/v1/enrollment-tokens", bytes.NewReader(body),
	)
	if err != nil {
		t.Fatalf("запрос токена: %v", err)
	}
	req.Header.Set("X-Api-Key", *apiKey)
	req.Header.Set("Content-Type", "application/json")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("выпуск токена: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusCreated && resp.StatusCode != http.StatusOK {
		t.Fatalf("выпуск токена: сервер ответил %d", resp.StatusCode)
	}

	var out struct {
		Token string `json:"token"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		t.Fatalf("разбор ответа: %v", err)
	}
	if out.Token == "" {
		t.Fatal("сервер вернул пустой токен")
	}
	return out.Token
}

func buildAgent(t *testing.T) string {
	t.Helper()
	binary := filepath.Join(t.TempDir(), "barysguard-agent.exe")
	build := exec.Command("go", "build", "-o", binary, "../cmd/barysguard-agent")
	if output, err := build.CombinedOutput(); err != nil {
		t.Fatalf("сборка агента: %v\n%s", err, output)
	}
	return binary
}

func enroll(t *testing.T, binary, dataDir, token string) (string, error) {
	t.Helper()
	command := exec.Command(binary, "enroll",
		"-data-dir", dataDir, "-server", *serverURL,
		"-token", token, "-ca-file", *caPath,
	)
	output, err := command.CombinedOutput()
	return string(output), err
}

func TestAgentEnrollsAndReportsStatus(t *testing.T) {
	requireFlags(t)

	binary := buildAgent(t)
	dataDir := t.TempDir()

	output, err := enroll(t, binary, dataDir, mintToken(t, 1))
	if err != nil {
		t.Fatalf("enroll: %v\n%s", err, output)
	}
	if !strings.Contains(output, "зарегистрирован") {
		t.Fatalf("неожиданный вывод: %s", output)
	}

	// Ключ обязан лежать на диске: он генерируется на хосте и никуда не уходит.
	if _, err := os.Stat(filepath.Join(dataDir, "pki", "agent.key")); err != nil {
		t.Fatalf("ключ не сохранён: %v", err)
	}

	status := exec.Command(binary, "status", "-data-dir", dataDir)
	raw, err := status.CombinedOutput()
	if err != nil {
		t.Fatalf("status: %v\n%s", err, raw)
	}
	for _, expected := range []string{"agent_id:", "config_version:", "cert_not_after:"} {
		if !strings.Contains(string(raw), expected) {
			t.Errorf("в выводе status нет %q:\n%s", expected, raw)
		}
	}

	// Версия конфигурации приходит настоящая, а не ноль: иначе агент
	// сходил бы за конфигом лишний раз на первом же heartbeat.
	if strings.Contains(string(raw), "config_version: 0") {
		t.Errorf("сервер отдал нулевую версию конфигурации:\n%s", raw)
	}
}

func TestSecondEnrollNeedsForce(t *testing.T) {
	requireFlags(t)

	binary := buildAgent(t)
	dataDir := t.TempDir()

	if output, err := enroll(t, binary, dataDir, mintToken(t, 1)); err != nil {
		t.Fatalf("первая регистрация: %v\n%s", err, output)
	}

	// Затереть действующую личность одной неосторожной командой нельзя.
	output, err := enroll(t, binary, dataDir, mintToken(t, 1))
	if err == nil {
		t.Fatalf("повторная регистрация без -force обязана отказывать:\n%s", output)
	}
}

func TestUsedTokenIsRejected(t *testing.T) {
	requireFlags(t)

	binary := buildAgent(t)
	token := mintToken(t, 1)

	if output, err := enroll(t, binary, t.TempDir(), token); err != nil {
		t.Fatalf("первая регистрация: %v\n%s", err, output)
	}

	// Одноразовый токен обязан быть одноразовым. Каталог другой,
	// поэтому отказ придёт от сервера, а не от проверки на диске.
	output, err := enroll(t, binary, t.TempDir(), token)
	if err == nil {
		t.Fatalf("израсходованный токен обязан быть отвергнут:\n%s", output)
	}
	if !strings.Contains(output, "регистрация") {
		t.Errorf("ожидалась ошибка регистрации, получено:\n%s", output)
	}
}

func TestUnknownTokenIsRejected(t *testing.T) {
	requireFlags(t)

	binary := buildAgent(t)
	output, err := enroll(t, binary, t.TempDir(), "BG-ENROLL-"+strings.Repeat("Z", 32))
	if err == nil {
		t.Fatalf("выдуманный токен обязан быть отвергнут:\n%s", output)
	}
	fmt.Fprintf(os.Stderr, "отказ сервера: %s\n", strings.TrimSpace(output))
}
