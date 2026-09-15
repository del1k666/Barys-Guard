// Package keystore хранит ключ и сертификат агента на диске.
package keystore

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"fmt"
	"os"
	"time"

	"github.com/barysguard/agent/internal/config"
	"github.com/barysguard/agent/internal/platform"
)

// Доля срока, после которой сертификат пора продлевать: 60 суток из 90.
const renewalNumerator, renewalDenominator = 2, 3

// GenerateKey создаёт ключ агента. Ключ генерируется на хосте и по сети
// не передаётся никогда — наружу уходит только CSR с открытой частью.
func GenerateKey() (*ecdsa.PrivateKey, error) {
	return ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
}

func EncodeKey(key *ecdsa.PrivateKey) ([]byte, error) {
	der, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		return nil, err
	}
	return pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der}), nil
}

// CreateCSR формирует запрос на сертификат.
//
// Субъект произвольный: агент на этот момент ещё не знает своего agent_id,
// а сервер субъект запроса игнорирует и формирует собственный.
func CreateCSR(key *ecdsa.PrivateKey) (string, error) {
	template := &x509.CertificateRequest{
		Subject:            pkix.Name{CommonName: "barysguard-agent"},
		SignatureAlgorithm: x509.ECDSAWithSHA256,
	}
	der, err := x509.CreateCertificateRequest(rand.Reader, template, key)
	if err != nil {
		return "", err
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE REQUEST", Bytes: der})), nil
}

// Save кладёт ключ и сертификат атомарно.
func Save(layout config.Layout, guard platform.Guard, keyPEM, certPEM []byte) error {
	if err := guard.SecureDir(layout.PKIDir()); err != nil {
		return err
	}
	if err := config.WriteAtomic(layout.KeyPath(), keyPEM, guard); err != nil {
		return fmt.Errorf("запись ключа: %w", err)
	}
	if err := config.WriteAtomic(layout.CertPath(), certPEM, guard); err != nil {
		return fmt.Errorf("запись сертификата: %w", err)
	}
	return nil
}

func SaveCA(layout config.Layout, guard platform.Guard, caPEM []byte) error {
	if err := guard.SecureDir(layout.PKIDir()); err != nil {
		return err
	}
	return config.WriteAtomic(layout.CAPath(), caPEM, guard)
}

// Load читает пару и разобранный сертификат.
//
// Проверка прав обязательна и выполняется до чтения: ключ, доступный
// посторонним, уже не доказывает личность агента.
func Load(layout config.Layout, guard platform.Guard) (tls.Certificate, *x509.Certificate, error) {
	if err := guard.VerifySecure(layout.KeyPath()); err != nil {
		return tls.Certificate{}, nil, err
	}

	pair, err := tls.LoadX509KeyPair(layout.CertPath(), layout.KeyPath())
	if err != nil {
		return tls.Certificate{}, nil, fmt.Errorf("загрузка пары ключ-сертификат: %w", err)
	}

	leaf, err := x509.ParseCertificate(pair.Certificate[0])
	if err != nil {
		return tls.Certificate{}, nil, fmt.Errorf("разбор сертификата: %w", err)
	}
	pair.Leaf = leaf
	return pair, leaf, nil
}

func LoadCAPool(layout config.Layout) (*x509.CertPool, error) {
	raw, err := os.ReadFile(layout.CAPath())
	if err != nil {
		return nil, err
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(raw) {
		return nil, fmt.Errorf("%s не содержит сертификатов PEM", layout.CAPath())
	}
	return pool, nil
}

// RenewalDue сообщает, истекло ли 2/3 срока сертификата.
//
// Отсчёт идёт по полям самого сертификата, а не по записанной дате выпуска:
// файл могли восстановить из резервной копии, и сохранённая отметка соврала бы.
func RenewalDue(cert *x509.Certificate, now time.Time) bool {
	lifetime := cert.NotAfter.Sub(cert.NotBefore)
	if lifetime <= 0 {
		return true
	}
	elapsed := now.Sub(cert.NotBefore)
	return elapsed*time.Duration(renewalDenominator) >= lifetime*time.Duration(renewalNumerator)
}
