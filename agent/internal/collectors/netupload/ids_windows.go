//go:build windows

package netupload

import "golang.org/x/sys/windows"

// ВНИМАНИЕ: НЕ ПОДТВЕРЖДЕНО. Номера событий и имена свойств ниже взяты по памяти
// из манифестов провайдеров и НЕ сверены с реальной системой. До запуска
// agent/cmd/etwprobe от администратора на настоящей машине они считаются
// непроверенными. Что необходимо сверить по выводу пробника:
//
//  1. Kernel-File: номер события создания (idFileCreate), чтения (idFileRead),
//     закрытия (idFileClose); имена свойств FileObject и FileName; что путь
//     приходит в NT-форме (\Device\HarddiskVolumeN\...); что FileObject — число
//     (строка "0x..." или десятичная), одинаковое в создании/чтении/закрытии.
//  2. Kernel-Network: номера событий отправки TCP IPv4/IPv6 и UDP IPv4/IPv6
//     (idTCPv4Send, idTCPv6Send, idUDPv4Send, idUDPv6Send); имена свойств PID,
//     size, daddr; формат daddr (строка с адресом, "ip:порт" или число — для
//     числа нужна ветка uint32/порядок байт в asAddr).
//  3. DNS-Client: номер завершённого запроса (idDNSQueryDone), имена свойств
//     QueryName и QueryResults, формат QueryResults ("type:  5 host;1.2.3.4;").
//  4. Что события приходят с заданными ключевыми словами Kernel-File
//     (keywordsKernelFile) и что чтения файлов попадают в выборку.
//
// При расхождении правится только этот файл (и, если изменилось имя свойства,
// константы prop* ниже).
var (
	guidKernelFile    = mustGUID("{EDD08927-9CC4-4E65-B970-C2560FB5C289}") // Microsoft-Windows-Kernel-File
	guidKernelNetwork = mustGUID("{7DD42A49-5329-4832-8DFD-43D979153A88}") // Microsoft-Windows-Kernel-Network
	guidDNSClient     = mustGUID("{1C95126E-7EEA-49A9-A3FE-A378B03DDB4D}") // Microsoft-Windows-DNS-Client
)

const (
	// Kernel-File: ключевые слова FILENAME | READ | CREATE.
	keywordsKernelFile uint64 = 0x10 | 0x20 | 0x80

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

func mustGUID(text string) windows.GUID {
	guid, err := windows.GUIDFromString(text)
	if err != nil {
		panic(err)
	}
	return guid
}
