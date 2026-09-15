package runner_test

import (
	"crypto/tls"
	"net/http"
	"net/http/httptest"
	"testing"
)

// newRunnerTLSServer поднимает сервер, требующий клиентский сертификат.
func newRunnerTLSServer(t *testing.T, ca *testCA, handler http.Handler) *httptest.Server {
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
