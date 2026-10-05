// agent/internal/artifacts/worker.go
package artifacts

import (
	"context"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"log/slog"
	"net/http"
	"sync"
	"time"

	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/transport"
)

const (
	passInterval = 15 * time.Second
	backoffBase  = 5 * time.Second
	backoffMax   = 5 * time.Minute
	maxChunk     = 1 << 20
	// Нижняя граница чанка при низкой скорости: меньше — слишком много запросов.
	minChunkBytes = 64 << 10
	// Сколько раз за одну загрузку принять смещение сервера, прежде чем
	// отложить файл: сервер, отвечающий 409 вечно, не должен держать воркер.
	maxRepositions = 5
)

var errIncomplete = errors.New("сервер не завершил загрузку после последнего чанка")

// Transport — то, что воркеру нужно от клиента шлюза.
type Transport interface {
	OpenArtifact(ctx context.Context, sha256 string, size int64) (transport.ArtifactOpenResponse, error)
	UploadChunk(ctx context.Context, uploadID string, offset int64, data []byte) (transport.ArtifactChunkResponse, error)
}

type retryState struct {
	attempts  int
	notBefore time.Time
}

// Worker отправляет копии на сервер: один файл за раз, в фоновом режиме потока,
// с ограничением скорости. Pass вызывается только из одной горутины.
type Worker struct {
	store *Store
	api   Transport
	now   func() time.Time
	sleep func(ctx context.Context, d time.Duration)

	mu  sync.Mutex
	cfg Config

	retry  map[string]*retryState
	resets map[string]int
}

func NewWorker(store *Store, api Transport) *Worker {
	return &Worker{
		store: store, api: api, now: time.Now, sleep: sleepContext, cfg: DefaultConfig(),
		retry: map[string]*retryState{}, resets: map[string]int{},
	}
}

func sleepContext(ctx context.Context, d time.Duration) {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
	case <-timer.C:
	}
}

// SetConfig применяет новый раздел конфигурации и к воркеру, и к каталогу копий.
func (w *Worker) SetConfig(cfg Config) {
	w.mu.Lock()
	w.cfg = cfg
	w.mu.Unlock()
	w.store.SetConfig(cfg)
}

func (w *Worker) config() Config {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.cfg
}

// Run крутит проходы, пока не отменён ctx. Копии, оставшиеся от прошлого
// запуска, подхватываются первым же проходом.
func (w *Worker) Run(ctx context.Context, emit func(events.Envelope)) {
	defer enterBackground()()

	ticker := time.NewTicker(passInterval)
	defer ticker.Stop()
	for {
		w.Pass(ctx)
		w.reportDropped(emit)
		select {
		case <-ctx.Done():
			return
		case <-w.store.Staged():
		case <-ticker.C:
		}
	}
}

// Pass загружает по одной все копии, которым пришёл срок.
func (w *Worker) Pass(ctx context.Context) {
	entries, err := w.store.List()
	if err != nil {
		slog.Warn("каталог копий не прочитан", "error", err)
		return
	}
	w.prune(entries)
	for _, entry := range entries {
		if ctx.Err() != nil {
			return
		}
		if state := w.retry[entry.SHA256]; state != nil && w.now().Before(state.notBefore) {
			continue
		}
		w.settle(ctx, entry, w.upload(ctx, entry))
	}
}

// prune забывает состояние копий, которых в каталоге уже нет (вытеснены или
// удалены): иначе устаревший счётчик повторов ударил бы по новой копии с тем же хешем.
func (w *Worker) prune(entries []Entry) {
	present := make(map[string]bool, len(entries))
	for _, e := range entries {
		present[e.SHA256] = true
	}
	for key := range w.retry {
		if !present[key] {
			delete(w.retry, key)
		}
	}
	for key := range w.resets {
		if !present[key] {
			delete(w.resets, key)
		}
	}
}

func (w *Worker) upload(ctx context.Context, entry Entry) error {
	open, err := w.api.OpenArtifact(ctx, entry.SHA256, entry.Size)
	if err != nil {
		return err
	}
	if open.Status == "exists" {
		w.store.Remove(entry)
		return nil
	}

	chunk := chunkFor(open.ChunkSize, w.config().UploadBytesPerSecond)
	source := &chunkSource{store: w.store, entry: entry}
	defer source.close()

	offset := open.ReceivedBytes
	repositions := 0
	// Жёсткий предел числа обращений: сервер не должен держать воркер вечно.
	limit := (entry.Size+chunk-1)/chunk + 1 + maxRepositions
	for iteration := int64(0); ; iteration++ {
		if iteration >= limit {
			return errIncomplete
		}
		data, err := source.read(offset, chunk)
		if err != nil {
			return err
		}
		// Пустой файл — единственный случай, когда пустой чанк допустим.
		if len(data) == 0 && entry.Size > 0 {
			return errIncomplete
		}

		started := w.now()
		resp, err := w.api.UploadChunk(ctx, open.UploadID, offset, data)
		if received, mismatch := transport.OffsetMismatch(err); mismatch {
			repositions++
			if repositions > maxRepositions {
				return fmt.Errorf("сервер %d раз подряд не принял смещение", repositions)
			}
			offset = received
			continue
		}
		if err != nil {
			return err
		}
		w.pace(ctx, len(data), started)

		if resp.Status == "complete" {
			// На Windows открытый файл не удалить: закрываем копию до Remove.
			source.close()
			w.store.Remove(entry)
			return nil
		}
		// Ответ «partial» без продвижения (и любой не-complete для пустого
		// файла) зациклил бы воркер: откладываем файл с паузой.
		if len(data) == 0 || resp.ReceivedBytes <= offset {
			return errIncomplete
		}
		offset = resp.ReceivedBytes
	}
}

// chunkFor выбирает размер чанка: не больше серверного и 1 МиБ, и не больше
// четверти секунды при заданной скорости (но не меньше minChunkBytes). Иначе на
// медленном канале чанк не успевает за таймаут запроса, смещение не растёт и
// каждый повтор впустую занимает канал. Докачка по смещению допускает любой размер.
func chunkFor(server, bps int64) int64 {
	chunk := int64(maxChunk)
	if server > 0 {
		chunk = min(chunk, server)
	}
	if bps > 0 {
		chunk = min(chunk, max(minChunkBytes, bps/4))
	}
	return chunk
}

// pace растягивает отправку до upload_bytes_per_second: фоновая загрузка не
// должна занимать канал рабочей станции.
func (w *Worker) pace(ctx context.Context, sent int, started time.Time) {
	bps := w.config().UploadBytesPerSecond
	if bps <= 0 {
		return
	}
	want := time.Duration(float64(sent) / float64(bps) * float64(time.Second))
	if remaining := want - w.now().Sub(started); remaining > 0 {
		w.sleep(ctx, remaining)
	}
}

// settle разбирает итог попытки: успех, повтор позже либо отказ навсегда.
func (w *Worker) settle(ctx context.Context, entry Entry, err error) {
	if err == nil {
		delete(w.retry, entry.SHA256)
		delete(w.resets, entry.SHA256)
		return
	}
	if ctx.Err() != nil {
		return
	}
	if errors.Is(err, fs.ErrNotExist) || errors.Is(err, errCorrupt) {
		slog.Warn("копия файла недоступна, загрузка отменена", "sha256", entry.SHA256, "error", err)
		w.drop(entry)
		return
	}

	var status *transport.StatusError
	if errors.As(err, &status) {
		switch {
		case status.Code == http.StatusUnprocessableEntity:
			// Хеш не сошёлся: один повтор с начала, затем отказ.
			w.resets[entry.SHA256]++
			if w.resets[entry.SHA256] > 1 {
				slog.Warn("сервер не принял содержимое дважды", "sha256", entry.SHA256)
				w.drop(entry)
				return
			}
			w.backoff(entry, 0)
		case status.Code == http.StatusNotFound:
			// Сессия истекла: следующий проход откроет новую.
			w.backoff(entry, 0)
		case isTransient(status.Code):
			w.backoff(entry, status.RetryAfter)
		default:
			slog.Warn("сервер отверг файл", "sha256", entry.SHA256, "status", status.Code)
			w.drop(entry)
		}
		return
	}

	slog.Warn("загрузка файла не удалась, повтор позже", "sha256", entry.SHA256, "error", err)
	w.backoff(entry, 0)
}

func (w *Worker) backoff(entry Entry, retryAfter time.Duration) {
	state := w.retry[entry.SHA256]
	if state == nil {
		state = &retryState{}
		w.retry[entry.SHA256] = state
	}
	state.attempts++
	delay := min(backoffBase<<min(state.attempts-1, 6), backoffMax)
	if retryAfter > delay {
		delay = retryAfter
	}
	state.notBefore = w.now().Add(delay)
}

func (w *Worker) drop(entry Entry) {
	w.store.Remove(entry)
	w.store.markDropped()
	delete(w.retry, entry.SHA256)
	delete(w.resets, entry.SHA256)
}

// reportDropped сообщает серверу о потерянных копиях одним событием.
func (w *Worker) reportDropped(emit func(events.Envelope)) {
	n := w.store.TakeDropped()
	if n == 0 || emit == nil {
		return
	}
	env, err := events.NewEnvelope(events.ChannelAgent, "artifact_dropped", events.SeverityLow, map[string]any{
		"component": "artifacts",
		"detail":    fmt.Sprintf("содержимое не сохранено или не загружено: %d файл(ов); превышен бюджет, место на диске или сервер отказал", n),
	})
	if err != nil {
		slog.Error("событие о потере копий не создано", "error", err)
		return
	}
	emit(env)
}

// chunkSource читает копию последовательно и переоткрывает её только при
// смене смещения: расшифровывать файл с начала на каждый чанк значило бы
// квадратичную нагрузку на процессор.
type chunkSource struct {
	store  *Store
	entry  Entry
	reader io.ReadCloser
	pos    int64
	buf    []byte
}

func (c *chunkSource) read(offset, size int64) ([]byte, error) {
	if c.reader == nil || offset != c.pos {
		c.close()
		reader, err := c.store.Open(c.entry)
		if err != nil {
			return nil, err
		}
		if _, err := io.CopyN(io.Discard, reader, offset); err != nil {
			reader.Close()
			return nil, err
		}
		c.reader, c.pos = reader, offset
	}
	if int64(cap(c.buf)) < size {
		c.buf = make([]byte, size)
	}
	buf := c.buf[:size]
	n, err := io.ReadFull(c.reader, buf)
	if err != nil && !errors.Is(err, io.EOF) && !errors.Is(err, io.ErrUnexpectedEOF) {
		return nil, err
	}
	c.pos += int64(n)
	return buf[:n], nil
}

func (c *chunkSource) close() {
	if c.reader != nil {
		c.reader.Close()
		c.reader = nil
	}
}

// isTransient — коды, после которых файл нужно сохранить и повторить позже.
// 401/403 — сбой на всём шлюзе (ротация сертификата, прокси, отозванный агент):
// сбросив копии по ним, мы бы безвозвратно потеряли всё накопленное.
func isTransient(code int) bool {
	switch code {
	case http.StatusUnauthorized, http.StatusForbidden, http.StatusRequestTimeout, http.StatusTooManyRequests:
		return true
	}
	return code >= 500
}
