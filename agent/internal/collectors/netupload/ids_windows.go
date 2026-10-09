//go:build windows

package netupload

// ВНИМАНИЕ: свойства событий НЕ ПОДТВЕРЖДЕНЫ на живой системе.
//
// Подтверждено манифестами провайдеров (wevtutil gp <провайдер> /ge /gm):
//   - Kernel-File: ключевые слова 0x10 FILENAME, 0x20 FILEIO, 0x80 CREATE,
//     0x100 READ; события 12 Create (0xA0), 14 Close (0x20), 15 Read (0x120);
//   - Kernel-Network: 10/26/42/58 — отправка TCPv4/TCPv6/UDPv4/UDPv6;
//   - DNS-Client: 3008 — завершённый запрос.
//
// Имена FileName/FileObject для Create встречаются в тестах библиотеки etw.
//
// НЕ проверено до запуска agent/cmd/etwprobe от администратора на настоящей
// машине (сверить по выводу пробника):
//  1. имена свойств: FileObject (Create/Read/Close), FileName, PID, size, daddr,
//     QueryName, QueryResults;
//  2. FileObject — одно и то же значение в Create, Read и Close одного файла;
//  3. путь в Create приходит в NT-форме (\Device\HarddiskVolumeN\...);
//  4. формат daddr (строка адреса, "ip:порт" или число) и size (десятичное число);
//  5. формат QueryResults ("type:  5 host;1.2.3.4;");
//  6. что события чтения действительно приходят с выбранной маской ключевых слов.
//
// При расхождении правится только этот файл (и asAddr/asUint в props.go,
// если формат значений другой).
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
