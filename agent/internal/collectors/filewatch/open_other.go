//go:build !windows

package filewatch

import (
	"io"
	"os"
)

func openFile(path string) (io.ReadCloser, int64, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, 0, err
	}
	info, err := file.Stat()
	if err != nil {
		file.Close()
		return nil, 0, err
	}
	if info.IsDir() {
		file.Close()
		return nil, 0, errIsDirectory
	}
	return file, info.Size(), nil
}
