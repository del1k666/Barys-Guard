//go:build !windows

package identity

type none struct{}

func (none) FileOwner(string) map[string]any { return nil }
func (none) ConsoleUser() map[string]any     { return nil }

// New на платформах без поддержки не определяет никого.
func New() Resolver { return none{} }
