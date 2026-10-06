//go:build !windows

package volumes

type emptyProvider struct{}

func (emptyProvider) Snapshot() ([]Volume, error) { return nil, nil }

// NewProvider на платформах без поддержки отдаёт пустой список томов:
// сборщики там не запускаются, но агент обязан собираться.
func NewProvider() Provider { return emptyProvider{} }
