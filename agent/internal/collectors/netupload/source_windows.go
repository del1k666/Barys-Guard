//go:build windows

package netupload

import (
	"context"
	"errors"
)

// Временная заглушка: настоящий источник ETW появляется в следующей задаче.
type etwSource struct{}

func NewSource() Source { return etwSource{} }

func (etwSource) Run(context.Context, func(Event)) error {
	return errors.New("источник ETW ещё не реализован")
}
