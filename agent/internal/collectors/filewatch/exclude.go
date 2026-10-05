package filewatch

import "strings"

// Excluder сопоставляет путь со списком шаблонов. Шаблон — простая маска:
// * совпадает с любой последовательностью символов (включая разделители),
// ? с одним символом; регистр не учитывается, как в файловой системе Windows.
type Excluder struct{ patterns []string }

func NewExcluder(patterns []string) *Excluder {
	lowered := make([]string, 0, len(patterns))
	for _, pattern := range patterns {
		lowered = append(lowered, strings.ToLower(pattern))
	}
	return &Excluder{patterns: lowered}
}

func (e *Excluder) Match(path string) bool {
	lowered := strings.ToLower(path)
	for _, pattern := range e.patterns {
		if globMatch([]rune(pattern), []rune(lowered)) {
			return true
		}
	}
	return false
}

// globMatch — итеративное сопоставление с откатом к последней звёздочке,
// без рекурсии: путь длиной в тысячи символов не должен переполнить стек.
func globMatch(pattern, text []rune) bool {
	p, t := 0, 0
	star, mark := -1, 0
	for t < len(text) {
		switch {
		case p < len(pattern) && (pattern[p] == '?' || pattern[p] == text[t]):
			p++
			t++
		case p < len(pattern) && pattern[p] == '*':
			star, mark = p, t
			p++
		case star >= 0:
			p = star + 1
			mark++
			t = mark
		default:
			return false
		}
	}
	for p < len(pattern) && pattern[p] == '*' {
		p++
	}
	return p == len(pattern)
}
