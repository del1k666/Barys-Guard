package filewatch

import (
	"encoding/binary"
	"strings"
	"unicode/utf16"
)

type Kind int

const (
	Created Kind = iota
	Modified
	Deleted
	Renamed
)

// Raw — одно разобранное уведомление файловой системы.
type Raw struct {
	Kind          Kind
	Path, OldPath string
}

const (
	actionAdded      = 1
	actionRemoved    = 2
	actionModified   = 3
	actionRenamedOld = 4
	actionRenamedNew = 5
	// Заголовок FILE_NOTIFY_INFORMATION: NextEntryOffset, Action, FileNameLength.
	notifyHeader = 12
)

func join(root, name string) string {
	return strings.TrimRight(root, `\`) + `\` + name
}

// ParseNotifications разбирает буфер ReadDirectoryChangesW. Вынесено из
// Windows-кода: разбор проверяется на любой платформе и не должен паниковать
// на повреждённом буфере. Пара RENAMED_OLD_NAME/RENAMED_NEW_NAME собирается
// в одно переименование; половина пары превращается в удаление или создание
// (файл переименован из наблюдаемой папки наружу или обратно).
func ParseNotifications(root string, buf []byte) []Raw {
	var out []Raw
	pendingOld := ""
	flushOld := func() {
		if pendingOld != "" {
			out = append(out, Raw{Kind: Deleted, Path: pendingOld})
			pendingOld = ""
		}
	}

	offset := 0
	for offset+notifyHeader <= len(buf) {
		next := int(binary.LittleEndian.Uint32(buf[offset:]))
		action := binary.LittleEndian.Uint32(buf[offset+4:])
		nameLen := int(binary.LittleEndian.Uint32(buf[offset+8:]))
		nameStart := offset + notifyHeader
		if nameLen%2 != 0 || nameLen < 0 || nameStart+nameLen > len(buf) {
			break
		}

		units := make([]uint16, nameLen/2)
		for i := range units {
			units[i] = binary.LittleEndian.Uint16(buf[nameStart+i*2:])
		}
		path := join(root, string(utf16.Decode(units)))

		if action != actionRenamedNew {
			flushOld()
		}
		switch action {
		case actionAdded:
			out = append(out, Raw{Kind: Created, Path: path})
		case actionRemoved:
			out = append(out, Raw{Kind: Deleted, Path: path})
		case actionModified:
			out = append(out, Raw{Kind: Modified, Path: path})
		case actionRenamedOld:
			pendingOld = path
		case actionRenamedNew:
			if pendingOld != "" {
				out = append(out, Raw{Kind: Renamed, Path: path, OldPath: pendingOld})
				pendingOld = ""
			} else {
				out = append(out, Raw{Kind: Created, Path: path})
			}
		}

		if next <= 0 || offset+next <= offset {
			break
		}
		offset += next
	}
	flushOld()
	return out
}
