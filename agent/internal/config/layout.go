// Package config задаёт раскладку файлов агента на диске и читает их.
package config

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"

	"github.com/barysguard/agent/internal/platform"
)

// Layout — раскладка рабочего каталога агента.
type Layout struct{ Dir string }

func NewLayout(dir string) Layout { return Layout{Dir: dir} }

// DefaultDir отдаёт рабочий каталог по умолчанию для текущей платформы.
func DefaultDir() string {
	if runtime.GOOS == "windows" {
		if programData := os.Getenv("ProgramData"); programData != "" {
			return filepath.Join(programData, "BarysGuard")
		}
		return filepath.Join(`C:\ProgramData`, "BarysGuard")
	}
	return "/var/lib/barysguard"
}

// Настройки заполняет оператор, состояние пишет агент. Общий файл означал бы,
// что переустановка с готовыми настройками затирает личность агента.
func (l Layout) SettingsPath() string { return filepath.Join(l.Dir, "agent.json") }
func (l Layout) StatePath() string    { return filepath.Join(l.Dir, "state.json") }
func (l Layout) PKIDir() string       { return filepath.Join(l.Dir, "pki") }
func (l Layout) KeyPath() string      { return filepath.Join(l.PKIDir(), "agent.key") }
func (l Layout) CertPath() string     { return filepath.Join(l.PKIDir(), "agent.crt") }
func (l Layout) CAPath() string       { return filepath.Join(l.PKIDir(), "ca.crt") }

// WriteAtomic пишет во временный файл рядом и переименовывает поверх целевого.
//
// Прямая запись оставляет агента после обрыва питания с ключом от одного
// сертификата и телом другого — состоянием, из которого он не выйдет
// без переустановки.
func WriteAtomic(path string, data []byte, guard platform.Guard) error {
	if err := guard.SecureDir(filepath.Dir(path)); err != nil {
		return err
	}

	temporary, err := os.CreateTemp(filepath.Dir(path), filepath.Base(path)+".*")
	if err != nil {
		return err
	}
	name := temporary.Name()
	// Убирает временный файл на любом пути выхода, кроме успешного
	// переименования: после Rename файла с этим именем уже нет.
	defer os.Remove(name)

	if _, err := temporary.Write(data); err != nil {
		temporary.Close()
		return err
	}
	// Данные обязаны лечь на диск до переименования, иначе атомарность
	// имени не спасает от пустого файла после отключения питания.
	if err := temporary.Sync(); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	if err := guard.SecureFile(name); err != nil {
		return err
	}
	if err := os.Rename(name, path); err != nil {
		return fmt.Errorf("переименование %s в %s: %w", name, path, err)
	}
	return nil
}

// Буфер событий и его ключ лежат вне каталога pki: ключ буфера не
// удостоверяет личность агента, и подмена сертификата его не затрагивает.
func (l Layout) BufferPath() string    { return filepath.Join(l.Dir, "events.db") }
func (l Layout) BufferKeyPath() string { return filepath.Join(l.Dir, "buffer.key") }
