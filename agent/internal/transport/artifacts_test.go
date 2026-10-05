// agent/internal/transport/artifacts_test.go
package transport_test

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/barysguard/agent/internal/transport"
)

func TestOpenArtifactPostsHashAndSize(t *testing.T) {
	sha := strings.Repeat("ab", 32)
	var gotMethod, gotPath string
	var gotBody map[string]any
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotMethod, gotPath = r.Method, r.URL.Path
		json.NewDecoder(r.Body).Decode(&gotBody)
		w.WriteHeader(http.StatusCreated)
		w.Write([]byte(`{"status":"upload","upload_id":"u-1","received_bytes":5,"chunk_size":1048576}`))
	}))

	got, err := client.OpenArtifact(context.Background(), sha, 7)
	if err != nil {
		t.Fatalf("OpenArtifact: %v", err)
	}

	if gotMethod != http.MethodPost || gotPath != "/gateway/v1/artifacts" {
		t.Fatalf("запрос %s %s", gotMethod, gotPath)
	}
	if gotBody["sha256"] != sha || gotBody["size"] != float64(7) {
		t.Fatalf("тело = %v", gotBody)
	}
	want := transport.ArtifactOpenResponse{Status: "upload", UploadID: "u-1", ReceivedBytes: 5, ChunkSize: 1048576}
	if got != want {
		t.Fatalf("ответ = %+v, ожидалось %+v", got, want)
	}
}

func TestUploadChunkSendsOffsetAndRawBytes(t *testing.T) {
	var gotMethod, gotPath, gotOffset, gotType string
	var gotBody []byte
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotMethod, gotPath = r.Method, r.URL.Path
		gotOffset, gotType = r.Header.Get("X-Offset"), r.Header.Get("Content-Type")
		gotBody, _ = io.ReadAll(r.Body)
		w.WriteHeader(http.StatusAccepted)
		w.Write([]byte(`{"received_bytes":13,"status":"partial"}`))
	}))

	got, err := client.UploadChunk(context.Background(), "u-1", 10, []byte("abc"))
	if err != nil {
		t.Fatalf("UploadChunk: %v", err)
	}

	if gotMethod != http.MethodPut || gotPath != "/gateway/v1/artifacts/u-1" {
		t.Fatalf("запрос %s %s", gotMethod, gotPath)
	}
	if gotOffset != "10" || gotType != "application/octet-stream" || string(gotBody) != "abc" {
		t.Fatalf("offset=%q type=%q body=%q", gotOffset, gotType, gotBody)
	}
	if got.ReceivedBytes != 13 || got.Status != "partial" {
		t.Fatalf("ответ = %+v", got)
	}
}

func TestOffsetMismatchIsReadFromA409(t *testing.T) {
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusConflict)
		w.Write([]byte(`{"received_bytes":1024}`))
	}))

	_, err := client.UploadChunk(context.Background(), "u-1", 0, []byte("x"))
	received, ok := transport.OffsetMismatch(err)
	if !ok || received != 1024 {
		t.Fatalf("OffsetMismatch = %d, %v", received, ok)
	}
}

func TestOffsetMismatchIgnoresOtherErrors(t *testing.T) {
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "сбой", http.StatusServiceUnavailable)
	}))

	_, err := client.UploadChunk(context.Background(), "u-1", 0, []byte("x"))
	if _, ok := transport.OffsetMismatch(err); ok {
		t.Fatal("503 принят за расхождение смещения")
	}
	if _, ok := transport.OffsetMismatch(nil); ok {
		t.Fatal("nil принят за расхождение смещения")
	}
}
