//go:build !windows

package filewatch

type noAttributor struct{}

func (noAttributor) Attribute(string) (map[string]any, bool) { return nil, false }

func NewAttributor() Attributor { return noAttributor{} }
