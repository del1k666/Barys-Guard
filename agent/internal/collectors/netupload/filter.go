package netupload

import (
	"strings"

	"github.com/barysguard/agent/internal/collectors/filewatch"
)

// Filter отбрасывает чтения, не похожие на работу с документом. Он дёшево
// отвечает по пути и размеру и не обращается к диску.
type Filter struct {
	extensions map[string]struct{}
	exclude    *filewatch.Excluder
	min, max   int64
}

func NewFilter(cfg Config) *Filter {
	extensions := make(map[string]struct{}, len(cfg.Extensions))
	for _, ext := range cfg.Extensions {
		extensions[strings.ToLower(strings.TrimPrefix(ext, "."))] = struct{}{}
	}
	return &Filter{
		extensions: extensions,
		exclude:    filewatch.NewExcluder(cfg.ExcludePaths),
		min:        cfg.MinFileBytes,
		max:        cfg.MaxFileBytes,
	}
}

// extension возвращает расширение без точки; точка после последнего разделителя
// не считается (каталог вида dir.v2).
func extension(path string) string {
	index := strings.LastIndexAny(path, `./\`)
	if index < 0 || path[index] != '.' {
		return ""
	}
	return strings.ToLower(path[index+1:])
}

func (f *Filter) PathOK(path string) bool {
	if _, ok := f.extensions[extension(path)]; !ok {
		return false
	}
	return !f.exclude.Match(path)
}

func (f *Filter) SizeOK(size int64) bool {
	return size >= f.min && size <= f.max
}
