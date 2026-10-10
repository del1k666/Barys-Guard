//go:build !windows

package netupload

// ProcessInfo вне Windows не определяет процесс.
func ProcessInfo(uint32) map[string]any { return nil }
