package transport_test

import (
	"context"
	"errors"
	"io"
	"net/http"
	"testing"

	"github.com/barysguard/agent/internal/transport"
)

func newTestClient(t *testing.T, handler http.Handler) *transport.Client {
	t.Helper()
	ca := newTestCA(t)
	server := newTLSServer(t, ca, handler)
	client, err := transport.NewMutual(server.URL, ca.pool(), ca.issue(t, "agent", ""))
	if err != nil {
		t.Fatalf("NewMutual: %v", err)
	}
	return client
}

func TestSendEventsPostsNDJSONAndParsesResult(t *testing.T) {
	var gotType, gotPath string
	var gotBody []byte
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotType = r.Header.Get("Content-Type")
		gotPath = r.URL.Path
		gotBody, _ = io.ReadAll(r.Body)
		w.WriteHeader(http.StatusAccepted)
		w.Write([]byte(`{"accepted":2,"duplicates":1,"rejected":[{"line":3,"reason":"invalid_event"}]}`))
	}))

	result, err := client.SendEvents(context.Background(), []byte("{\"a\":1}\n{\"b\":2}\n"))
	if err != nil {
		t.Fatalf("SendEvents: %v", err)
	}

	if gotPath != "/gateway/v1/events" {
		t.Errorf("путь = %q", gotPath)
	}
	if gotType != "application/x-ndjson" {
		t.Errorf("Content-Type = %q", gotType)
	}
	if string(gotBody) != "{\"a\":1}\n{\"b\":2}\n" {
		t.Errorf("тело = %q", gotBody)
	}
	if result.Accepted != 2 || result.Duplicates != 1 ||
		len(result.Rejected) != 1 || result.Rejected[0].Line != 3 || result.Rejected[0].Reason != "invalid_event" {
		t.Errorf("результат: %+v", result)
	}
}

func TestSendEventsSurfacesStatusCode(t *testing.T) {
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "too big", http.StatusRequestEntityTooLarge)
	}))

	_, err := client.SendEvents(context.Background(), []byte("{}\n"))

	var statusErr *transport.StatusError
	if !errors.As(err, &statusErr) || statusErr.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("ошибка = %v, ожидался StatusError 413", err)
	}
}
