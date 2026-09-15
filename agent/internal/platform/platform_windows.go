//go:build windows

package platform

import (
	"fmt"
	"os"
	"unsafe"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/registry"
)

// Полный доступ SYSTEM (SY), Administrators (BA) и владельцу процесса.
// Буква P означает protected: наследование от родительского каталога
// отключено, иначе права, заданные на %ProgramData%, вернули бы доступ
// группе Users.
//
// Владелец процесса в списке обязателен. В бою агент работает службой
// под SYSTEM, и тогда эта запись совпадает с первой. Но список, отбирающий
// доступ у того, кто эти файлы создаёт, не защищает ни от чего: запустивший
// процесс пользователь всегда может сменить владельца файла и прочитать его.
// Зато без этой записи агент, запущенный не из-под SYSTEM, запирает сам себя.
const restrictedSDDLTemplate = "D:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;%s)"

type guard struct{}

// currentUserSID отдаёт SID владельца текущего процесса.
func currentUserSID() (string, error) {
	user, err := windows.GetCurrentProcessToken().GetTokenUser()
	if err != nil {
		return "", err
	}
	return user.User.Sid.String(), nil
}

func applyDACL(path string) error {
	sid, err := currentUserSID()
	if err != nil {
		return err
	}
	descriptor, err := windows.SecurityDescriptorFromString(
		fmt.Sprintf(restrictedSDDLTemplate, sid),
	)
	if err != nil {
		return err
	}
	dacl, _, err := descriptor.DACL()
	if err != nil {
		return err
	}
	return windows.SetNamedSecurityInfo(
		path,
		windows.SE_FILE_OBJECT,
		windows.DACL_SECURITY_INFORMATION|windows.PROTECTED_DACL_SECURITY_INFORMATION,
		nil, nil, dacl, nil,
	)
}

func (guard) SecureDir(path string) error {
	if err := os.MkdirAll(path, 0o700); err != nil {
		return err
	}
	return applyDACL(path)
}

func (guard) SecureFile(path string) error { return applyDACL(path) }

// Группы, присутствие которых в списке доступа означает, что ключ
// читает кто угодно из вошедших в систему.
func forbiddenSIDs() ([]*windows.SID, error) {
	var result []*windows.SID
	for _, known := range []windows.WELL_KNOWN_SID_TYPE{
		windows.WinWorldSid,
		windows.WinBuiltinUsersSid,
		windows.WinAuthenticatedUserSid,
	} {
		sid, err := windows.CreateWellKnownSid(known)
		if err != nil {
			return nil, err
		}
		result = append(result, sid)
	}
	return result, nil
}

func (guard) VerifySecure(path string) error {
	descriptor, err := windows.GetNamedSecurityInfo(
		path, windows.SE_FILE_OBJECT, windows.DACL_SECURITY_INFORMATION,
	)
	if err != nil {
		return err
	}
	dacl, _, err := descriptor.DACL()
	if err != nil {
		return err
	}
	forbidden, err := forbiddenSIDs()
	if err != nil {
		return err
	}

	for index := uint32(0); index < uint32(dacl.AceCount); index++ {
		var ace *windows.ACCESS_ALLOWED_ACE
		if err := windows.GetAce(dacl, index, &ace); err != nil {
			return err
		}
		sid := (*windows.SID)(unsafe.Pointer(&ace.SidStart))
		for _, bad := range forbidden {
			if sid.Equals(bad) {
				return fmt.Errorf("%w: %s доступен группе %s", ErrInsecurePermissions, path, bad)
			}
		}
	}
	return nil
}

func (guard) MachineID() (string, error) {
	// WOW64_64KEY обязателен: 32-битный процесс без него попадёт
	// в перенаправленный раздел реестра и прочтёт чужое значение.
	key, err := registry.OpenKey(
		registry.LOCAL_MACHINE,
		`SOFTWARE\Microsoft\Cryptography`,
		registry.QUERY_VALUE|registry.WOW64_64KEY,
	)
	if err != nil {
		return "", err
	}
	defer key.Close()

	guid, _, err := key.GetStringValue("MachineGuid")
	if err != nil {
		return "", err
	}
	if guid == "" {
		return "", fmt.Errorf("MachineGuid пуст")
	}
	return guid, nil
}

func (guard) OSVersion() (string, error) {
	// RtlGetVersion, а не GetVersionEx: последний с Windows 8.1 врёт
	// приложениям без манифеста совместимости.
	version := windows.RtlGetVersion()
	return fmt.Sprintf("%d.%d.%d", version.MajorVersion, version.MinorVersion, version.BuildNumber), nil
}
