//go:build !windows

package netupload

// NewSource вне Windows источника нет: сборщик не запускается.
func NewSource() Source { return nil }

// Shutdown вне Windows ничего не делает.
func Shutdown() {}
