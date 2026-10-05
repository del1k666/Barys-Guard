// agent/internal/artifacts/worker_test.go
package artifacts

import (
	"bytes"
	"context"
	"crypto/rand"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/transport"
)

// fakeServer — сервер загрузки в памяти.
type fakeServer struct {
	chunkSize int64
	size      int64
	received  []byte
	known     bool
	stuck     bool // всегда «partial» без продвижения
	staleOpen bool // Open сообщает 0 принятых байт, хотя они есть

	failOpen  error
	failChunk func(offset int64) error

	opens, chunks int
	offsets       []int64
	maxChunk      int
}

func (f *fakeServer) OpenArtifact(_ context.Context, _ string, size int64) (transport.ArtifactOpenResponse, error) {
	f.opens++
	if f.failOpen != nil {
		return transport.ArtifactOpenResponse{}, f.failOpen
	}
	if f.known {
		return transport.ArtifactOpenResponse{Status: "exists"}, nil
	}
	f.size = size
	received := int64(len(f.received))
	if f.staleOpen {
		received = 0
	}
	return transport.ArtifactOpenResponse{
		Status: "upload", UploadID: "u-1", ReceivedBytes: received, ChunkSize: f.chunkSize,
	}, nil
}

func (f *fakeServer) UploadChunk(_ context.Context, _ string, offset int64, data []byte) (transport.ArtifactChunkResponse, error) {
	f.chunks++
	f.offsets = append(f.offsets, offset)
	f.maxChunk = max(f.maxChunk, len(data))
	if f.failChunk != nil {
		if err := f.failChunk(offset); err != nil {
			return transport.ArtifactChunkResponse{}, err
		}
	}
	if offset != int64(len(f.received)) {
		return transport.ArtifactChunkResponse{}, &transport.StatusError{
			Code: 409, Body: fmt.Sprintf(`{"received_bytes":%d}`, len(f.received)),
		}
	}
	if f.stuck {
		return transport.ArtifactChunkResponse{ReceivedBytes: offset, Status: "partial"}, nil
	}
	f.received = append(f.received, data...)
	status := "partial"
	if int64(len(f.received)) >= f.size {
		status = "complete"
	}
	return transport.ArtifactChunkResponse{ReceivedBytes: int64(len(f.received)), Status: status}, nil
}

type workerHarness struct {
	store  *Store
	clock  *fakeClock
	api    *fakeServer
	worker *Worker
	slept  []time.Duration
}

func newWorkerHarness(t *testing.T) *workerHarness {
	t.Helper()
	store, clock := newTestStore(t, nil)
	api := &fakeServer{chunkSize: 1 << 20}
	h := &workerHarness{store: store, clock: clock, api: api, worker: NewWorker(store, api)}
	h.worker.now = clock.Now
	h.worker.sleep = func(_ context.Context, d time.Duration) { h.slept = append(h.slept, d) }
	// Скорость не мешает тестам, кроме теста пейсинга.
	cfg := DefaultConfig()
	cfg.UploadBytesPerSecond = 1 << 40
	h.worker.SetConfig(cfg)
	return h
}

func randomBytes(n int) []byte {
	out := make([]byte, n)
	rand.Read(out)
	return out
}

func (h *workerHarness) pending(t *testing.T) int {
	t.Helper()
	entries, err := h.store.List()
	if err != nil {
		t.Fatal(err)
	}
	return len(entries)
}

func TestFileIsUploadedInOrderedChunksAndTheCopyIsRemoved(t *testing.T) {
	h := newWorkerHarness(t)
	data := randomBytes(2*(1<<20) + 512*1024) // 2,5 МиБ
	stage(t, h.store, data)

	h.worker.Pass(context.Background())

	if !bytes.Equal(h.api.received, data) {
		t.Fatalf("сервер получил %d байт, ожидалось %d", len(h.api.received), len(data))
	}
	if fmt.Sprint(h.api.offsets) != fmt.Sprint([]int64{0, 1 << 20, 2 << 20}) {
		t.Fatalf("смещения = %v", h.api.offsets)
	}
	if h.api.maxChunk > 1<<20 {
		t.Fatalf("чанк в %d байт: больше предела", h.api.maxChunk)
	}
	if h.pending(t) != 0 {
		t.Fatal("копия не удалена после успешной загрузки")
	}
}

func TestKnownArtifactIsNotTransferred(t *testing.T) {
	h := newWorkerHarness(t)
	h.api.known = true
	stage(t, h.store, randomBytes(2000))

	h.worker.Pass(context.Background())

	if h.api.chunks != 0 {
		t.Fatalf("отправлено %d чанков для известного артефакта", h.api.chunks)
	}
	if h.pending(t) != 0 {
		t.Fatal("копия известного артефакта осталась")
	}
}

func TestUploadResumesFromTheServersOffset(t *testing.T) {
	h := newWorkerHarness(t)
	data := randomBytes(2*(1<<20) + 100)
	h.api.received = append([]byte(nil), data[:1<<20]...) // первая часть уже на сервере
	stage(t, h.store, data)

	h.worker.Pass(context.Background())

	if len(h.api.offsets) == 0 || h.api.offsets[0] != 1<<20 {
		t.Fatalf("первый чанк со смещением %v, ожидалось 1048576", h.api.offsets)
	}
	if !bytes.Equal(h.api.received, data) {
		t.Fatal("итоговое содержимое не совпало")
	}
}

func TestOffsetMismatchRepositionsInsteadOfFailing(t *testing.T) {
	h := newWorkerHarness(t)
	data := randomBytes(2*(1<<20) + 100)
	h.api.received = append([]byte(nil), data[:1<<20]...)
	h.api.staleOpen = true // сессия говорит «0», хотя сервер ждёт 1 МиБ
	stage(t, h.store, data)

	h.worker.Pass(context.Background())

	if !bytes.Equal(h.api.received, data) {
		t.Fatal("после 409 загрузка не дошла до конца")
	}
}

// Сервер без конца отвечает 409: воркер обязан выйти из прохода, а не крутиться.
func TestEndlessOffsetMismatchStopsThePass(t *testing.T) {
	h := newWorkerHarness(t)
	h.api.failChunk = func(int64) error {
		return &transport.StatusError{Code: 409, Body: `{"received_bytes":0}`}
	}
	stage(t, h.store, randomBytes(3000))

	h.worker.Pass(context.Background())

	if h.pending(t) != 1 {
		t.Fatal("копия должна остаться для следующей попытки")
	}
}

func TestServerErrorKeepsTheCopyAndBacksOff(t *testing.T) {
	h := newWorkerHarness(t)
	h.api.failOpen = &transport.StatusError{Code: 503}
	stage(t, h.store, randomBytes(2000))

	h.worker.Pass(context.Background())
	h.worker.Pass(context.Background()) // пауза ещё не прошла
	if h.api.opens != 1 {
		t.Fatalf("за время паузы сделано %d обращений, ожидалось 1", h.api.opens)
	}
	if h.pending(t) != 1 {
		t.Fatal("копия потеряна при временной ошибке")
	}

	h.clock.now = h.clock.now.Add(6 * time.Second)
	h.worker.Pass(context.Background())
	if h.api.opens != 2 {
		t.Fatalf("после паузы обращений %d, ожидалось 2", h.api.opens)
	}
}

func TestRetryAfterIsHonoured(t *testing.T) {
	h := newWorkerHarness(t)
	h.api.failOpen = &transport.StatusError{Code: 429, RetryAfter: 2 * time.Minute}
	stage(t, h.store, randomBytes(2000))

	h.worker.Pass(context.Background())
	h.clock.now = h.clock.now.Add(time.Minute)
	h.worker.Pass(context.Background())

	if h.api.opens != 1 {
		t.Fatalf("обращений %d: Retry-After проигнорирован", h.api.opens)
	}
}

func TestClientErrorDropsTheCopyAndIsReported(t *testing.T) {
	h := newWorkerHarness(t)
	h.api.failChunk = func(int64) error { return &transport.StatusError{Code: 413} }
	stage(t, h.store, randomBytes(2000))

	h.worker.Pass(context.Background())

	if h.pending(t) != 0 {
		t.Fatal("копия, которую сервер отверг навсегда, осталась")
	}
	if got := h.store.TakeDropped(); got != 1 {
		t.Fatalf("TakeDropped = %d, ожидалось 1", got)
	}
}

func TestHashMismatchIsRetriedOnceThenDropped(t *testing.T) {
	h := newWorkerHarness(t)
	h.api.failChunk = func(int64) error { return &transport.StatusError{Code: 422} }
	stage(t, h.store, randomBytes(2000))

	h.worker.Pass(context.Background())
	if h.pending(t) != 1 {
		t.Fatal("первая 422 должна оставить копию для одного повтора")
	}

	h.clock.now = h.clock.now.Add(6 * time.Second)
	h.worker.Pass(context.Background())
	if h.pending(t) != 0 || h.store.TakeDropped() != 1 {
		t.Fatal("после второй 422 копия должна быть снята и учтена как потеря")
	}
}

func TestEmptyFileIsUploadedWithOneEmptyChunk(t *testing.T) {
	h := newWorkerHarness(t)
	stage(t, h.store, nil)

	h.worker.Pass(context.Background())

	if h.api.chunks != 1 || len(h.api.received) != 0 {
		t.Fatalf("чанков %d, байт %d: ожидался один пустой чанк", h.api.chunks, len(h.api.received))
	}
	if h.pending(t) != 0 {
		t.Fatal("копия пустого файла не удалена")
	}
}

func TestUploadSpeedIsPaced(t *testing.T) {
	h := newWorkerHarness(t)
	cfg := DefaultConfig()
	cfg.UploadBytesPerSecond = 1 << 20 // 1 МиБ/с
	h.worker.SetConfig(cfg)
	stage(t, h.store, randomBytes(1<<20))

	h.worker.Pass(context.Background())

	var total time.Duration
	for _, d := range h.slept {
		total += d
	}
	if total < 900*time.Millisecond || total > 1100*time.Millisecond {
		t.Fatalf("пауза %v: 1 МиБ при 1 МиБ/с должен занимать около секунды", total)
	}
}

func TestMissingStagedCopyIsDroppedNotRetriedForever(t *testing.T) {
	h := newWorkerHarness(t)
	stage(t, h.store, randomBytes(3000))
	entries, _ := h.store.List()
	// Файл исчез между перечислением и чтением (антивирус, чистка диска).
	os.Remove(filepath.Join(h.store.dir, entries[0].name))

	err := h.worker.upload(context.Background(), entries[0])
	h.worker.settle(context.Background(), entries[0], err)

	if got := h.store.TakeDropped(); got != 1 {
		t.Fatalf("TakeDropped = %d, потеря не учтена", got)
	}
	h.clock.now = h.clock.now.Add(time.Hour)
	h.worker.Pass(context.Background())
	if h.api.opens != 1 {
		t.Fatalf("обращений %d: исчезнувшая копия не должна повторяться", h.api.opens)
	}
}

func TestCorruptStagedCopyIsDropped(t *testing.T) {
	h := newWorkerHarness(t)
	stage(t, h.store, randomBytes(3000))
	entries, _ := h.store.List()
	path := filepath.Join(h.store.dir, entries[0].name)
	raw, _ := os.ReadFile(path)
	raw[len(raw)/2] ^= 1
	os.WriteFile(path, raw, 0o600)

	h.worker.Pass(context.Background())

	if h.pending(t) != 0 || h.store.TakeDropped() != 1 {
		t.Fatal("повреждённая копия должна быть снята и учтена как потеря")
	}
}

func TestDroppedCopiesAreReportedOnceAsAnAgentEvent(t *testing.T) {
	h := newWorkerHarness(t)
	h.store.markDropped()
	h.store.markDropped()

	var got []events.Envelope
	h.worker.reportDropped(func(e events.Envelope) { got = append(got, e) })
	h.worker.reportDropped(func(e events.Envelope) { got = append(got, e) })

	if len(got) != 1 {
		t.Fatalf("событий %d, ожидалось 1", len(got))
	}
	if got[0].Channel != events.ChannelAgent || got[0].Action != "artifact_dropped" {
		t.Fatalf("событие = %+v", got[0])
	}
}

// Сервер отвечает «partial» без продвижения: проход обязан завершиться.
func TestPartialWithoutProgressStopsThePass(t *testing.T) {
	for name, size := range map[string]int{"data": 3000, "empty": 0} {
		t.Run(name, func(t *testing.T) {
			h := newWorkerHarness(t)
			h.api.stuck = true
			var data []byte
			if size > 0 {
				data = randomBytes(size)
			}
			stage(t, h.store, data)

			h.worker.Pass(context.Background())

			if h.api.chunks > 3 {
				t.Fatalf("чанков %d: воркер крутится на месте", h.api.chunks)
			}
			if h.pending(t) != 1 {
				t.Fatal("копия должна остаться")
			}
			if len(h.worker.retry) != 1 {
				t.Fatal("состояние паузы не выставлено")
			}
		})
	}
}

func TestBackoffStateIsPrunedWhenTheCopyVanishes(t *testing.T) {
	h := newWorkerHarness(t)
	h.api.failOpen = &transport.StatusError{Code: 503}
	stage(t, h.store, randomBytes(2000))
	h.worker.Pass(context.Background())
	if len(h.worker.retry) != 1 {
		t.Fatal("нет состояния паузы")
	}
	entries, _ := h.store.List()
	os.Remove(filepath.Join(h.store.dir, entries[0].name))

	h.worker.Pass(context.Background())

	if len(h.worker.retry) != 0 || len(h.worker.resets) != 0 {
		t.Fatalf("retry=%d resets=%d", len(h.worker.retry), len(h.worker.resets))
	}
}

func TestStaleHashRetryCounterDoesNotHitARestagedCopy(t *testing.T) {
	h := newWorkerHarness(t)
	h.api.failChunk = func(int64) error { return &transport.StatusError{Code: 422} }
	data := randomBytes(2000)
	stage(t, h.store, data)
	h.worker.Pass(context.Background())
	entries, _ := h.store.List()
	os.Remove(filepath.Join(h.store.dir, entries[0].name))
	h.worker.Pass(context.Background()) // прореживание

	stage(t, h.store, data)
	h.clock.now = h.clock.now.Add(time.Hour)
	h.worker.Pass(context.Background())

	if h.pending(t) != 1 || h.store.TakeDropped() != 0 {
		t.Fatal("первая 422 новой копии не должна её снимать")
	}
}

func TestAuthAndTimeoutErrorsAreTransient(t *testing.T) {
	for _, code := range []int{401, 403, 408} {
		h := newWorkerHarness(t)
		h.api.failOpen = &transport.StatusError{Code: code}
		stage(t, h.store, randomBytes(2000))

		h.worker.Pass(context.Background())

		if h.pending(t) != 1 || h.store.TakeDropped() != 0 || len(h.worker.retry) != 1 {
			t.Fatalf("код %d: копия потеряна или пауза не выставлена", code)
		}
	}
}

func TestOtherClientErrorsStillDrop(t *testing.T) {
	for _, code := range []int{400, 413} {
		h := newWorkerHarness(t)
		h.api.failOpen = &transport.StatusError{Code: code}
		stage(t, h.store, randomBytes(2000))

		h.worker.Pass(context.Background())

		if h.pending(t) != 0 || h.store.TakeDropped() != 1 {
			t.Fatalf("код %d: копия должна быть снята", code)
		}
	}
}

// Чанк обязан укладываться в ~четверть секунды при заданной скорости: на
// медленном канале мегабайтный чанк не успевает за таймаут запроса клиента.
func TestChunkIsCappedByTheConfiguredSpeed(t *testing.T) {
	cases := []struct {
		name    string
		bps     int64
		size    int
		wantMax int
	}{
		{"128KiB/s", 128 << 10, 300 << 10, 64 << 10},
		{"floor at 1KiB/s", 1 << 10, 200 << 10, 64 << 10},
		{"default", DefaultConfig().UploadBytesPerSecond, 3 << 20, 1 << 20},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			h := newWorkerHarness(t)
			cfg := DefaultConfig()
			cfg.UploadBytesPerSecond = tc.bps
			h.worker.SetConfig(cfg)
			data := randomBytes(tc.size)
			stage(t, h.store, data)

			h.worker.Pass(context.Background())

			if !bytes.Equal(h.api.received, data) {
				t.Fatalf("сервер получил %d байт из %d", len(h.api.received), len(data))
			}
			if want := max(int64(minChunkBytes), tc.bps/4); int64(h.api.maxChunk) > want {
				t.Fatalf("чанк %d байт больше max(64 КиБ, bps/4) = %d", h.api.maxChunk, want)
			}
			if h.api.maxChunk > tc.wantMax {
				t.Fatalf("чанк %d байт, предел %d", h.api.maxChunk, tc.wantMax)
			}
			if tc.bps < 4*minChunkBytes && h.api.maxChunk != minChunkBytes {
				t.Fatalf("чанк %d байт, при низкой скорости ожидалось %d", h.api.maxChunk, minChunkBytes)
			}
		})
	}
}
