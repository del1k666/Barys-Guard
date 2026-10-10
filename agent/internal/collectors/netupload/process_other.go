//go:build !windows

package netupload

// ProcessInfo вне Windows не определяет процесс.
func ProcessInfo(uint32) map[string]any { return nil }

// ProcessFamily вне Windows возвращает сам pid.
func ProcessFamily(pid uint32) uint32 { return pid }
