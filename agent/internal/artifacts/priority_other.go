// agent/internal/artifacts/priority_other.go
//go:build !windows

package artifacts

// enterBackground вне Windows ничего не меняет: агент на Linux файлы
// съёмных носителей не собирает.
func enterBackground() func() { return func() {} }
