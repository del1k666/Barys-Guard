package main

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"math/big"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/config"
	"github.com/barysguard/agent/internal/transport"
)

func TestEnrollStoresIdentityAndRefusesToRepeat(t *testing.T) {
	certificatePEM := selfSignedPEM(t)

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var request transport.EnrollRequest
		json.NewDecoder(r.Body).Decode(&request)
		if request.CSRPEM == "" {
			t.Error("CSR не прислан")
		}
		w.WriteHeader(http.StatusCreated)
		json.NewEncoder(w).Encode(transport.EnrollResponse{
			AgentID:                  "6f1a9c2e-0e4b-4f9c-9a3e-1d2b3c4d5e6f",
			CertificatePEM:           string(certificatePEM),
			CAPEM:                    string(certificatePEM),
			ConfigVersion:            12,
			HeartbeatIntervalSeconds: 30,
		})
	}))
	t.Cleanup(server.Close)

	dir := t.TempDir()
	options := enrollOptions{
		dataDir:   dir,
		serverURL: server.URL,
		token:     "BG-ENROLL-AAAA",
		caFile:    writeTempCA(t, dir, certificatePEM),
	}

	if err := runEnroll(options); err != nil {
		t.Fatalf("runEnroll: %v", err)
	}

	layout := config.NewLayout(dir)
	state, err := config.LoadState(layout)
	if err != nil {
		t.Fatalf("LoadState: %v", err)
	}
	if state.AgentID != "6f1a9c2e-0e4b-4f9c-9a3e-1d2b3c4d5e6f" {
		t.Fatalf("agent_id = %q", state.AgentID)
	}
	if _, err := os.Stat(layout.KeyPath()); err != nil {
		t.Fatalf("ключ не сохранён: %v", err)
	}

	// Затереть действующую личность одной неосторожной командой нельзя.
	if err := runEnroll(options); err == nil {
		t.Fatal("повторная регистрация без --force обязана отказывать")
	}
}

func TestEnrollDemandsTrustAnchor(t *testing.T) {
	// Ни файла CA, ни отпечатка — доверять нечему, и выбирать
	// что-нибудь на своё усмотрение агент не вправе.
	err := runEnroll(enrollOptions{
		dataDir:   t.TempDir(),
		serverURL: "https://example.invalid",
		token:     "BG-ENROLL-AAAA",
	})
	if err == nil {
		t.Fatal("регистрация без якоря доверия обязана отказывать")
	}
}

func writeTempCA(t *testing.T, dir string, caPEM []byte) string {
	t.Helper()
	path := filepath.Join(dir, "bundled-ca.crt")
	if err := os.WriteFile(path, caPEM, 0o600); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}
	return path
}

// selfSignedPEM выпускает сертификат прямо в тесте.
//
// Готовая константа в файле устарела бы молча: срок действия истекает,
// и тест начинает падать через год по причине, не связанной с кодом.
func selfSignedPEM(t *testing.T) []byte {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatalf("ключ: %v", err)
	}
	template := &x509.Certificate{
		SerialNumber:          big.NewInt(1),
		Subject:               pkix.Name{CommonName: "BarysGuard Test CA"},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().Add(24 * time.Hour),
		IsCA:                  true,
		KeyUsage:              x509.KeyUsageCertSign,
		BasicConstraintsValid: true,
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		t.Fatalf("сертификат: %v", err)
	}
	return pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
}
