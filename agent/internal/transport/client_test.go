package transport_test

import (
	"context"
	"crypto/sha256"
	"crypto/tls"
	"encoding/hex"
	"encoding/pem"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/transport"
)

// newTLSServer поднимает сервер, требующий клиентский сертификат.
func newTLSServer(t *testing.T, ca *testCA, handler http.Handler) *httptest.Server {
	t.Helper()
	server := httptest.NewUnstartedServer(handler)
	server.TLS = &tls.Config{
		Certificates: []tls.Certificate{ca.issue(t, "server", "localhost")},
		ClientCAs:    ca.pool(),
		ClientAuth:   tls.RequireAndVerifyClientCert,
		MinVersion:   tls.VersionTLS12,
	}
	server.StartTLS()
	t.Cleanup(server.Close)
	return server
}

func TestMutualClientPresentsItsCertificate(t *testing.T) {
	ca := newTestCA(t)
	var seenCN string
	server := newTLSServer(t, ca, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Если бы клиент не предъявил сертификат, сюда бы не дошло.
		seenCN = r.TLS.PeerCertificates[0].Subject.CommonName
		w.Write([]byte(`{"status":"ok"}`))
	}))

	client, err := transport.NewMutual(server.URL, ca.pool(), ca.issue(t, "agent-42", ""))
	if err != nil {
		t.Fatalf("NewMutual: %v", err)
	}
	if _, err := client.Whoami(context.Background()); err != nil {
		t.Fatalf("Whoami: %v", err)
	}

	if seenCN != "agent-42" {
		t.Fatalf("сервер увидел CN %q, ожидался agent-42", seenCN)
	}
}

func TestBootstrapClientIsRejectedWhereCertificateIsRequired(t *testing.T) {
	ca := newTestCA(t)
	server := newTLSServer(t, ca, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))

	client, err := transport.NewBootstrap(server.URL, ca.pool())
	if err != nil {
		t.Fatalf("NewBootstrap: %v", err)
	}
	// Бутстрапный клиент сертификата не имеет — рукопожатие обязано провалиться.
	if _, err := client.Whoami(context.Background()); err == nil {
		t.Fatal("ожидался отказ рукопожатия без клиентского сертификата")
	}
}

func TestUnknownCAIsRejected(t *testing.T) {
	ca := newTestCA(t)
	stranger := newTestCA(t)
	server := newTLSServer(t, ca, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))

	client, err := transport.NewMutual(server.URL, stranger.pool(), ca.issue(t, "agent", ""))
	if err != nil {
		t.Fatalf("NewMutual: %v", err)
	}
	if _, err := client.Whoami(context.Background()); err == nil {
		t.Fatal("сертификат чужого CA обязан быть отвергнут")
	}
}

func TestStatusErrorCarriesCodeAndRetryAfter(t *testing.T) {
	ca := newTestCA(t)
	server := newTLSServer(t, ca, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Retry-After", "17")
		w.WriteHeader(http.StatusTooManyRequests)
	}))

	client, _ := transport.NewMutual(server.URL, ca.pool(), ca.issue(t, "agent", ""))
	_, err := client.Whoami(context.Background())

	var statusErr *transport.StatusError
	if !errors.As(err, &statusErr) {
		t.Fatalf("ожидался *StatusError, получено %T: %v", err, err)
	}
	if statusErr.Code != http.StatusTooManyRequests {
		t.Fatalf("код = %d", statusErr.Code)
	}
	// Своё представление о паузе агент обязан уступить серверному.
	if statusErr.RetryAfter != 17*time.Second {
		t.Fatalf("RetryAfter = %v, ожидалось 17s", statusErr.RetryAfter)
	}
}

func TestForbiddenIsRecognised(t *testing.T) {
	ca := newTestCA(t)
	server := newTLSServer(t, ca, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusForbidden)
	}))

	client, _ := transport.NewMutual(server.URL, ca.pool(), ca.issue(t, "agent", ""))
	_, err := client.Whoami(context.Background())

	if !transport.IsForbidden(err) {
		t.Fatalf("403 обязан опознаваться: %v", err)
	}
}

func TestFetchCAAcceptsMatchingPin(t *testing.T) {
	ca := newTestCA(t)
	// GET /ca идёт без клиентского сертификата, поэтому сервер его не требует.
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write(ca.pem)
	}))
	t.Cleanup(server.Close)

	block, _ := pem.Decode(ca.pem)
	sum := sha256.Sum256(block.Bytes)

	got, err := transport.FetchCA(context.Background(), server.URL, hex.EncodeToString(sum[:]))
	if err != nil {
		t.Fatalf("FetchCA: %v", err)
	}
	if string(got) != string(ca.pem) {
		t.Fatal("вернулся не тот CA")
	}
}

func TestFetchCARejectsWrongPin(t *testing.T) {
	// Смысл отпечатка в том, что подменённый CA не проходит.
	ca := newTestCA(t)
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write(ca.pem)
	}))
	t.Cleanup(server.Close)

	_, err := transport.FetchCA(context.Background(), server.URL, "00"+hex.EncodeToString(make([]byte, 31)))
	if err == nil {
		t.Fatal("несовпавший отпечаток обязан приводить к отказу")
	}
}

func TestFetchCADemandsAPin(t *testing.T) {
	// Скачать CA и тут же начать ему доверять — это тот самый перехват,
	// против которого вводится mTLS.
	if _, err := transport.FetchCA(context.Background(), "https://example.invalid", ""); err == nil {
		t.Fatal("без отпечатка загрузка CA обязана отказывать")
	}
}
