package keystore_test

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"os"
	"runtime"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/config"
	"github.com/barysguard/agent/internal/keystore"
	"github.com/barysguard/agent/internal/platform"
)

// issueCertificate выпускает самоподписанный сертификат с заданным сроком.
func issueCertificate(t *testing.T, key *ecdsa.PrivateKey, notBefore, notAfter time.Time) []byte {
	t.Helper()
	template := &x509.Certificate{
		SerialNumber: big.NewInt(1),
		Subject:      pkix.Name{CommonName: "agent"},
		NotBefore:    notBefore,
		NotAfter:     notAfter,
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		t.Fatalf("CreateCertificate: %v", err)
	}
	return pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
}

func TestGeneratedKeyIsP256(t *testing.T) {
	key, err := keystore.GenerateKey()
	if err != nil {
		t.Fatalf("GenerateKey: %v", err)
	}
	if key.Curve != elliptic.P256() {
		t.Fatalf("кривая = %s, спека требует P-256", key.Curve.Params().Name)
	}
}

func TestCSRCarriesPublicKeyAndValidSignature(t *testing.T) {
	key, err := keystore.GenerateKey()
	if err != nil {
		t.Fatalf("GenerateKey: %v", err)
	}

	csrPEM, err := keystore.CreateCSR(key)
	if err != nil {
		t.Fatalf("CreateCSR: %v", err)
	}

	block, _ := pem.Decode([]byte(csrPEM))
	if block == nil {
		t.Fatal("CSR не является PEM")
	}
	csr, err := x509.ParseCertificateRequest(block.Bytes)
	if err != nil {
		t.Fatalf("ParseCertificateRequest: %v", err)
	}
	if err := csr.CheckSignature(); err != nil {
		t.Fatalf("подпись CSR недействительна: %v", err)
	}
	// Сервер подписывает именно открытый ключ из CSR, а субъект игнорирует.
	if !csr.PublicKey.(*ecdsa.PublicKey).Equal(&key.PublicKey) {
		t.Fatal("CSR несёт не тот открытый ключ")
	}
}

func TestSaveThenLoadReturnsUsablePair(t *testing.T) {
	layout := config.NewLayout(t.TempDir())
	guard := platform.New()

	key, _ := keystore.GenerateKey()
	keyPEM, err := keystore.EncodeKey(key)
	if err != nil {
		t.Fatalf("EncodeKey: %v", err)
	}
	certPEM := issueCertificate(t, key, time.Now().Add(-time.Hour), time.Now().Add(24*time.Hour))

	if err := keystore.Save(layout, guard, keyPEM, certPEM); err != nil {
		t.Fatalf("Save: %v", err)
	}

	pair, leaf, err := keystore.Load(layout, guard)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if len(pair.Certificate) == 0 {
		t.Fatal("в паре нет сертификата")
	}
	if leaf.Subject.CommonName != "agent" {
		t.Fatalf("CN = %q", leaf.Subject.CommonName)
	}
}

func TestLoadRefusesWorldReadableKey(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("режим доступа Unix неприменим к Windows")
	}
	layout := config.NewLayout(t.TempDir())
	guard := platform.New()

	key, _ := keystore.GenerateKey()
	keyPEM, _ := keystore.EncodeKey(key)
	certPEM := issueCertificate(t, key, time.Now().Add(-time.Hour), time.Now().Add(24*time.Hour))
	if err := keystore.Save(layout, guard, keyPEM, certPEM); err != nil {
		t.Fatalf("Save: %v", err)
	}

	// Кто-то скопировал каталог и растерял права по дороге.
	if err := os.Chmod(layout.KeyPath(), 0o644); err != nil {
		t.Skipf("смена прав недоступна: %v", err)
	}

	if _, _, err := keystore.Load(layout, guard); err == nil {
		t.Fatal("загрузка обязана отказать при доступном посторонним ключе")
	}
}

func TestRenewalDueAtTwoThirdsOfLifetime(t *testing.T) {
	notBefore := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	notAfter := notBefore.Add(90 * 24 * time.Hour)
	cert := &x509.Certificate{NotBefore: notBefore, NotAfter: notAfter}

	cases := []struct {
		name string
		days int
		want bool
	}{
		{"свежий", 1, false},
		{"за день до порога", 59, false},
		{"на пороге 60 суток", 60, true},
		{"просрочен", 95, true},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			now := notBefore.Add(time.Duration(testCase.days) * 24 * time.Hour)
			if got := keystore.RenewalDue(cert, now); got != testCase.want {
				t.Fatalf("RenewalDue через %d суток = %v, ожидалось %v", testCase.days, got, testCase.want)
			}
		})
	}
}
