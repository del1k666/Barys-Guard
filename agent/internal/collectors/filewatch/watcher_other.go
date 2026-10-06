//go:build !windows

package filewatch

import (
	"context"
	"errors"
)

// ErrUnsupported — наблюдение за файлами не реализовано на этой платформе.
var ErrUnsupported = errors.New("наблюдение за файлами не поддерживается на этой платформе")

// StartWatcher наблюдает за корнем root, пока не отменён ctx, и передаёт
// разобранные уведомления в out; overflow сообщает о переполнении буфера ОС.
type StartWatcher func(ctx context.Context, root string, out chan<- Raw, overflow func(root string)) error

var DefaultStartWatcher StartWatcher = func(context.Context, string, chan<- Raw, func(string)) error {
	return ErrUnsupported
}
