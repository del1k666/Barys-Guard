package filewatch

import (
	"sort"
	"strings"
	"time"
)

type Op int

const (
	OpCreate Op = iota
	OpModify
	OpDelete
)

type Action string

const (
	ActionCreate Action = "create"
	ActionModify Action = "modify"
	ActionDelete Action = "delete"
	ActionRename Action = "rename"
	ActionCopy   Action = "copy"
)

// Settled — итог склейки: один файл, одно действие.
type Settled struct {
	Path, OldPath string
	Action        Action
}

type pending struct {
	path        string // последнее написание: регистр в путях значим только для показа
	first, last time.Time
	created     bool
	deleted     bool
	lastDelete  bool
	renamedFrom string
}

// Debouncer склеивает поток уведомлений в события по одному на файл.
// Одна копия файла порождает десятки уведомлений; событие выходит, когда
// файл стабилен. Не потокобезопасен: им владеет цикл сборщика.
type Debouncer struct{ items map[string]*pending }

func NewDebouncer() *Debouncer { return &Debouncer{items: map[string]*pending{}} }

func key(path string) string { return strings.ToLower(path) }

func (d *Debouncer) touch(path string, now time.Time) *pending {
	item, ok := d.items[key(path)]
	if !ok {
		item = &pending{first: now}
		d.items[key(path)] = item
	}
	item.path = path
	item.last = now
	return item
}

func (d *Debouncer) Notify(path string, op Op, now time.Time) {
	item := d.touch(path, now)
	switch op {
	case OpCreate:
		item.created, item.lastDelete = true, false
	case OpModify:
		item.lastDelete = false
	case OpDelete:
		item.deleted, item.lastDelete = true, true
	}
}

// NotifyRename учитывает переименование. Если старое имя появилось в том же
// окне склейки (редактор писал временный файл и переименовал его), новое имя
// считается созданием: настоящего «старого файла» никто не видел.
func (d *Debouncer) NotifyRename(oldPath, newPath string, now time.Time) {
	createdHere := false
	if old, ok := d.items[key(oldPath)]; ok {
		createdHere = old.created
		delete(d.items, key(oldPath))
	}
	item := d.touch(newPath, now)
	item.lastDelete = false
	if createdHere {
		item.created = true
		return
	}
	item.renamedFrom = oldPath
}

// Settle отдаёт события, по которым нет новых уведомлений дольше stable
// либо которые ждут дольше maxWait, и забывает их.
func (d *Debouncer) Settle(now time.Time, stable, maxWait time.Duration) []Settled {
	type ready struct {
		item *pending
		out  Settled
	}
	var done []ready
	for k, item := range d.items {
		if now.Sub(item.last) < stable && now.Sub(item.first) < maxWait {
			continue
		}
		delete(d.items, k)

		out := Settled{Path: item.path}
		switch {
		case item.lastDelete && item.created:
			continue // создан и удалён в одном окне: временный файл
		case item.lastDelete:
			out.Action = ActionDelete
		case item.deleted && item.created:
			out.Action = ActionModify // файл заменён: редактор удалил и создал заново
		case item.renamedFrom != "":
			out.Action, out.OldPath = ActionRename, item.renamedFrom
		case item.created:
			out.Action = ActionCreate
		default:
			out.Action = ActionModify
		}
		done = append(done, ready{item: item, out: out})
	}

	sort.Slice(done, func(i, j int) bool {
		if !done[i].item.first.Equal(done[j].item.first) {
			return done[i].item.first.Before(done[j].item.first)
		}
		return done[i].out.Path < done[j].out.Path
	})
	result := make([]Settled, 0, len(done))
	for _, entry := range done {
		result = append(result, entry.out)
	}
	return result
}
