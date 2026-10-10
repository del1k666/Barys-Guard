//go:build windows

package netupload

// Идентификаторы и свойства подтверждены пробником agent/cmd/etwprobe на живой
// Windows 11 (2026-10-10):
//   - Kernel-File: 12 Create (FileName, FileObject — путь в NT-форме
//     \Device\HarddiskVolumeN\...), 14 Close, 15 Read (FileObject без FileName,
//     поэтому путь берётся из таблицы по FileObject из Create);
//   - Kernel-Network: 10/26/42/58 — отправка (PID, size, daddr, dport); daddr —
//     чистый IP, порт отдельным свойством; 11/43 — приём;
//   - DNS-Client: 3008 — QueryName и QueryResults вида "142.251.20.94;"
//     (CNAME-формат "type:  5 host;" код тоже разбирает).
//
// Не проверено вживую: полный сценарий выгрузки файла в браузере и Telegram
// (раздел 6 docs/NETWORK_UPLOAD.md).
const (
	guidKernelFile    = "{EDD08927-9CC4-4E65-B970-C2560FB5C289}" // Microsoft-Windows-Kernel-File
	guidKernelNetwork = "{7DD42A49-5329-4832-8DFD-43D979153A88}" // Microsoft-Windows-Kernel-Network
	guidDNSClient     = "{1C95126E-7EEA-49A9-A3FE-A378B03DDB4D}" // Microsoft-Windows-DNS-Client
)

const (
	// Kernel-File: FILEIO | CREATE | READ.
	keywordsKernelFile uint64 = 0x20 | 0x80 | 0x100

	idFileCreate uint16 = 12
	idFileRead   uint16 = 15
	idFileClose  uint16 = 14

	// Kernel-Network: отправка данных.
	idTCPv4Send uint16 = 10
	idTCPv6Send uint16 = 26
	idUDPv4Send uint16 = 42
	idUDPv6Send uint16 = 58

	// DNS-Client: завершённый запрос.
	idDNSQueryDone uint16 = 3008
)

// Имена свойств событий (регистр важен: так их отдаёт TDH).
const (
	propFileObject = "FileObject"
	propFileName   = "FileName"
	propNetPID     = "PID"
	propNetSize    = "size"
	propNetDest    = "daddr"
	propDNSName    = "QueryName"
	propDNSResults = "QueryResults"
)
