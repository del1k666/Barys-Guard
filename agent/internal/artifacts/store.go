// agent/internal/artifacts/store.go
package artifacts

import (
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"sync"
	"sync/atomic"
	"time"
)

// Причины, по которым копию не делают (второй результат Begin).
const (
	SkipDisabled = "disabled"
	SkipSize     = "size"
	SkipRate     = "rate"
)

var (
	errTooLarge   = errors.New("файл больше предела копирования")
	errSinkClosed = errors.New("копия уже завершена")
	shaPattern    = regexp.MustCompile(`^[0-9a-f]{64}$`)
	entryPattern  = regexp.MustCompile(`^([0-9a-f]{64})-(\d+)\.enc$`)
)

// Sink принимает копию читаемого файла. Ошибка Write означает «копию не
// продолжать»: хеширование от неё не зависит.
type Sink interface {
	io.Writer
	// Commit завершает копию, называя её хешем содержимого.
	Commit(sha256 string) error
	Abort()
}

// Stager решает, нужна ли копия, и открывает приёмник. Второй результат пуст,
// если копия делается, иначе это одна из констант Skip*.
type Stager interface {
	Begin(size int64) (Sink, string)
}

// Entry — копия в каталоге. Size — размер открытого текста.
type Entry struct {
	SHA256  string
	Size    int64
	ModTime time.Time
	name    string
}

// Store — каталог зашифрованных копий со сроком жизни «до загрузки».
type Store struct {
	dir    string
	key    []byte
	now    func() time.Time
	staged chan struct{}
	// remove — удаление файла копии; подменяется в тестах.
	remove func(string) error
	// listCalls — число перечислений каталога; читают тесты.
	listCalls atomic.Int64

	mu          sync.Mutex
	cfg         Config
	windowStart time.Time
	windowBytes int64
	dropped     uint64
	// total — сумма открытого текста завершённых копий. Ведётся на ходу, чтобы
	// Begin не перечитывал каталог на каждую копию; при упоре в предел
	// пересчитывается по каталогу (файлы, заменённые или удалённые в обход Store).
	total int64
}

// NewStore открывает каталог копий. Права на него выставляет вызывающий
// (platform.Guard.SecureDir). Временные файлы прошлого запуска — обрывки
// незавершённых копий — удаляются.
func NewStore(dir string, key []byte, now func() time.Time) (*Store, error) {
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, err
	}
	leftovers, _ := filepath.Glob(filepath.Join(dir, "*.tmp"))
	for _, path := range leftovers {
		os.Remove(path)
	}
	store := &Store{
		dir: dir, key: key, now: now, cfg: DefaultConfig(),
		staged: make(chan struct{}, 1), remove: os.Remove,
	}
	entries, err := store.List()
	if err != nil {
		return nil, err
	}
	store.total = sumSizes(entries)
	return store, nil
}

func sumSizes(entries []Entry) int64 {
	var total int64
	for _, e := range entries {
		total += e.Size
	}
	return total
}

func (s *Store) SetConfig(cfg Config) {
	s.mu.Lock()
	s.cfg = cfg
	s.mu.Unlock()
}

// Staged сообщает воркеру о новой копии. Канал с буфером в один сигнал:
// несколько копий подряд будят воркер один раз.
func (s *Store) Staged() <-chan struct{} { return s.staged }

// TakeDropped отдаёт, сколько копий потеряно с прошлого вызова (бюджет,
// вытеснение, отказ сервера), и обнуляет счётчик.
func (s *Store) TakeDropped() uint64 {
	s.mu.Lock()
	defer s.mu.Unlock()
	n := s.dropped
	s.dropped = 0
	return n
}

func (s *Store) markDropped() {
	s.mu.Lock()
	s.dropped++
	s.mu.Unlock()
}

// Begin открывает приёмник копии. Размер известен из stat до чтения, поэтому
// крупный файл не копируется вовсе. Вызывается из единственного цикла
// наблюдателя за файлами, поэтому незавершённые копии не учитываются в
// StagingMaxBytes (известная граница: до MaxBytes на каждую параллельную копию).
func (s *Store) Begin(size int64) (Sink, string) {
	s.mu.Lock()
	defer s.mu.Unlock()

	cfg := s.cfg
	if !cfg.Enabled {
		return nil, SkipDisabled
	}
	if size > cfg.MaxBytes || size > cfg.StagingMaxBytes {
		return nil, SkipSize
	}

	now := s.now()
	if now.Sub(s.windowStart) >= time.Minute {
		s.windowStart, s.windowBytes = now, 0
	}
	if s.windowBytes+size > cfg.StageBytesPerMinute {
		s.dropped++
		return nil, SkipRate
	}

	s.makeRoomLocked(size, cfg.StagingMaxBytes)

	file, err := os.CreateTemp(s.dir, "*.tmp")
	if err != nil {
		slog.Warn("копия файла не начата", "error", err)
		return nil, SkipDisabled
	}
	enc, err := newEncryptWriter(file, s.key)
	if err != nil {
		file.Close()
		os.Remove(file.Name())
		slog.Warn("копия файла не начата", "error", err)
		return nil, SkipDisabled
	}
	s.windowBytes += size
	return &sink{store: s, file: file, enc: enc, limit: cfg.MaxBytes}, ""
}

// makeRoomLocked вытесняет самые старые копии, пока новая не поместится.
// Сумма считается по размеру открытого текста: шифрование добавляет доли процента.
// Каталог перечитывается только при упоре в предел.
func (s *Store) makeRoomLocked(size, limit int64) {
	if s.total+size <= limit {
		return
	}
	entries, err := s.List()
	if err != nil {
		return
	}
	s.total = sumSizes(entries)
	for len(entries) > 0 && s.total+size > limit {
		oldest := entries[0]
		entries = entries[1:]
		// Не удалось удалить (на Windows файл может быть открыт воркером):
		// места это не освободило и потерей не считается, идём к следующей.
		if s.remove(filepath.Join(s.dir, oldest.name)) == nil {
			s.dropped++
			s.total -= oldest.Size
		}
	}
}

// List отдаёт копии, старые первыми.
func (s *Store) List() ([]Entry, error) {
	s.listCalls.Add(1)
	files, err := os.ReadDir(s.dir)
	if err != nil {
		return nil, err
	}
	var out []Entry
	for _, file := range files {
		match := entryPattern.FindStringSubmatch(file.Name())
		if match == nil {
			continue
		}
		info, err := file.Info()
		if err != nil {
			continue
		}
		size, _ := strconv.ParseInt(match[2], 10, 64)
		out = append(out, Entry{SHA256: match[1], Size: size, ModTime: info.ModTime(), name: file.Name()})
	}
	sort.Slice(out, func(i, j int) bool {
		if !out[i].ModTime.Equal(out[j].ModTime) {
			return out[i].ModTime.Before(out[j].ModTime)
		}
		return out[i].name < out[j].name
	})
	return out, nil
}

type readCloser struct {
	io.Reader
	io.Closer
}

// Open отдаёт расшифрованное содержимое копии.
func (s *Store) Open(e Entry) (io.ReadCloser, error) {
	file, err := os.Open(filepath.Join(s.dir, e.name))
	if err != nil {
		return nil, err
	}
	reader, err := newDecryptReader(file, s.key)
	if err != nil {
		file.Close()
		return nil, err
	}
	return readCloser{reader, file}, nil
}

func (s *Store) Remove(e Entry) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if os.Remove(filepath.Join(s.dir, e.name)) == nil {
		s.total = max(s.total-e.Size, 0)
	}
}

type sink struct {
	store *Store
	file  *os.File
	enc   *encryptWriter
	limit int64
	done  bool
	// failed — первая ошибка записи: после неё копия испорчена и не публикуется.
	failed error
}

func (k *sink) Write(p []byte) (int, error) {
	if k.done {
		return 0, errSinkClosed
	}
	if k.failed != nil {
		return 0, k.failed
	}
	// Файл мог вырасти между stat и чтением: предел действует на фактические байты.
	if k.enc.plain+int64(len(p)) > k.limit {
		k.failed = errTooLarge
		return 0, k.failed
	}
	n, err := k.enc.Write(p)
	if err != nil {
		k.failed = err
	}
	return n, err
}

func (k *sink) Commit(sha256 string) error {
	if k.done {
		return nil
	}
	k.done = true
	if k.failed != nil {
		k.discard()
		return k.failed
	}
	// Хеш становится именем файла: он обязан быть именно хешем.
	if !shaPattern.MatchString(sha256) {
		k.discard()
		return fmt.Errorf("недопустимый хеш копии %q", sha256)
	}
	if err := k.enc.Close(); err != nil {
		k.discard()
		return err
	}
	if err := k.file.Sync(); err != nil {
		k.discard()
		return err
	}
	temporary := k.file.Name()
	if err := k.file.Close(); err != nil {
		os.Remove(temporary)
		return err
	}
	target := filepath.Join(k.store.dir, fmt.Sprintf("%s-%d.enc", sha256, k.enc.plain))
	if err := os.Rename(temporary, target); err != nil {
		os.Remove(temporary)
		return err
	}
	// Замена копии с тем же именем учтётся дважды; это лишь раньше
	// приведёт к пересчёту по каталогу в Begin.
	k.store.mu.Lock()
	k.store.total += k.enc.plain
	k.store.mu.Unlock()
	select {
	case k.store.staged <- struct{}{}:
	default:
	}
	return nil
}

func (k *sink) Abort() {
	if k.done {
		return
	}
	k.done = true
	k.discard()
}

func (k *sink) discard() {
	k.file.Close()
	os.Remove(k.file.Name())
}
