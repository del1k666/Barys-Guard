// Package platform закрывает всё, что различается между Windows и Linux.
// Остальные пакеты агента об операционной системе не знают.
package platform

import "errors"

// ErrInsecurePermissions возвращается, когда файл ключа доступен посторонним.
// Агент в этом случае отказывается стартовать — так же поступает ssh.
// Продолжать работу, делая вид, что скомпрометированный ключ доказывает
// личность, хуже, чем остановиться.
var ErrInsecurePermissions = errors.New("файл ключа доступен посторонним")

// Guard выставляет и подтверждает права на файлы ключей, а также добывает
// сведения о хосте, которые нельзя получить переносимым способом.
//
// VerifySecure существует отдельно от SecureFile намеренно: права,
// выставленные при записи, мог изменить кто угодно — ручное копирование
// каталога, восстановление из архива, неаккуратный установщик.
type Guard interface {
	SecureDir(path string) error
	SecureFile(path string) error
	VerifySecure(path string) error
	MachineID() (string, error)
	OSVersion() (string, error)
}

// New отдаёт реализацию для текущей операционной системы.
func New() Guard { return guard{} }
