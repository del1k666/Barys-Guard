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
// Запуск:
//
//	go test -tags e2e ./e2e/ -v \
//	  -server http://127.0.0.1:8000 -token BG-ENROLL-... -ca /path/ca.crt
package e2e

import (
	"flag"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

var (
	serverURL = flag.String("server", "", "адрес шлюза")
	token     = flag.String("token", "", "токен регистрации")
	caPath    = flag.String("ca", "", "сертификат CA")
)

func buildAgent(t *testing.T) string {
	t.Helper()
	binary := filepath.Join(t.TempDir(), "barysguard-agent")
	build := exec.Command("go", "build", "-o", binary, "../cmd/barysguard-agent")
	if output, err := build.CombinedOutput(); err != nil {
		t.Fatalf("сборка агента: %v\n%s", err, output)
	}
	return binary
}

func TestAgentEnrollsAndReportsStatus(t *testing.T) {
	if *serverURL == "" || *token == "" || *caPath == "" {
		t.Skip("нужны -server, -token и -ca")
	}

	binary := buildAgent(t)
	dataDir := t.TempDir()

	enroll := exec.Command(binary, "enroll",
		"-data-dir", dataDir, "-server", *serverURL,
		"-token", *token, "-ca-file", *caPath,
	)
	output, err := enroll.CombinedOutput()
	if err != nil {
		t.Fatalf("enroll: %v\n%s", err, output)
	}
	if !strings.Contains(string(output), "зарегистрирован") {
		t.Fatalf("неожиданный вывод: %s", output)
	}

	// Ключ обязан лежать на диске и быть закрытым от посторонних.
	if _, err := os.Stat(filepath.Join(dataDir, "pki", "agent.key")); err != nil {
		t.Fatalf("ключ не сохранён: %v", err)
	}

	status := exec.Command(binary, "status", "-data-dir", dataDir)
	output, err = status.CombinedOutput()
	if err != nil {
		t.Fatalf("status: %v\n%s", err, output)
	}
	for _, expected := range []string{"agent_id:", "config_version:", "cert_not_after:"} {
		if !strings.Contains(string(output), expected) {
			t.Errorf("в выводе status нет %q:\n%s", expected, output)
		}
	}
}

// TestEnrollRejectsUsedToken проверяет, что одноразовый токен действительно
// одноразовый: повторная регистрация тем же токеном обязана провалиться.
func TestEnrollRejectsUsedToken(t *testing.T) {
	if *serverURL == "" || *token == "" || *caPath == "" {
		t.Skip("нужны -server, -token и -ca")
	}

	binary := buildAgent(t)

	// Первая регистрация расходует единственное использование токена.
	first := exec.Command(binary, "enroll",
		"-data-dir", t.TempDir(), "-server", *serverURL,
		"-token", *token, "-ca-file", *caPath,
	)
	if output, err := first.CombinedOutput(); err != nil {
		t.Fatalf("первая регистрация: %v\n%s", err, output)
	}

	second := exec.Command(binary, "enroll",
		"-data-dir", t.TempDir(), "-server", *serverURL,
		"-token", *token, "-ca-file", *caPath,
	)
	if output, err := second.CombinedOutput(); err == nil {
		t.Fatalf("израсходованный токен обязан быть отвергнут, получено:\n%s", output)
	}
}
