BUILTIN_YARA_RULES = {
    "Mimikatz": """rule Mimikatz_Generic {
    meta:
        description = "Detects Mimikatz credential dumper"
        author      = "BarysGuard"
        severity    = "critical"
    strings:
        $s1 = "mimikatz" ascii nocase
        $s2 = "sekurlsa" ascii nocase
        $s3 = "lsadump" ascii nocase
        $s4 = "kerberos::list" ascii nocase
        $s5 = "privilege::debug" ascii nocase
        $b1 = { 6D 69 6D 69 6B 61 74 7A }
    condition:
        2 of ($s*) or $b1
}""",

    "Meterpreter": """rule Meterpreter_Shellcode {
    meta:
        description = "Detects Metasploit Meterpreter shellcode patterns"
        author      = "BarysGuard"
        severity    = "critical"
    strings:
        $h1 = { FC E8 8? 00 00 00 }
        $h2 = { 60 89 E5 31 D2 64 }
        $s1 = "metsrv" ascii nocase
        $s2 = "ReflectiveLoader" ascii
        $s3 = "meterpreter" ascii nocase
        $s4 = { 4D 65 74 65 72 70 72 65 74 65 72 }
    condition:
        any of ($h*) or 2 of ($s*)
}""",

    "CobaltStrike": """rule CobaltStrike_Beacon {
    meta:
        description = "Detects Cobalt Strike Beacon artifacts"
        author      = "BarysGuard"
        severity    = "critical"
    strings:
        $s1 = "%s as %s\\\\%s: %d" ascii
        $s2 = "beacon.dll" ascii nocase
        $s3 = "cobaltstrike" ascii nocase
        $h1 = { 00 68 74 74 70 73 3F 00 }
        $h2 = { 69 68 69 68 69 6B }
        $cfg1 = { 00 01 00 01 00 02 }
    condition:
        1 of ($s*) or any of ($h*) or $cfg1
}""",

    "PowerShell_Encoded": """rule PowerShell_Encoded_Command {
    meta:
        description = "Detects PowerShell encoded/obfuscated execution"
        author      = "BarysGuard"
        severity    = "high"
    strings:
        $s1 = "-EncodedCommand" ascii nocase
        $s2 = "-enc " ascii nocase
        $s3 = "powershell -e " ascii nocase
        $s4 = "IEX(" ascii nocase
        $s5 = "Invoke-Expression" ascii nocase
        $s6 = "FromBase64String" ascii nocase
        $s7 = "-WindowStyle Hidden" ascii nocase
        $s8 = "bypass" ascii nocase
    condition:
        3 of them
}""",

    "Ransomware_Generic": """rule Ransomware_Generic_Indicators {
    meta:
        description = "Generic ransomware behavioral indicators"
        author      = "BarysGuard"
        severity    = "critical"
    strings:
        $ext1 = ".encrypted" ascii nocase
        $ext2 = ".locked" ascii nocase
        $ext3 = ".crypto" ascii nocase
        $ext4 = "YOUR_FILES_ENCRYPTED" ascii nocase
        $note1 = "DECRYPT" ascii nocase
        $note2 = "RANSOM" ascii nocase
        $note3 = "bitcoin" ascii nocase
        $note4 = "recover your files" ascii nocase
        $api1 = "CryptEncrypt" ascii
        $api2 = "CryptGenRandom" ascii
        $vss1 = "vssadmin delete shadows" ascii nocase
        $vss2 = "wbadmin delete" ascii nocase
    condition:
        2 of ($ext*) or 3 of ($note*) or (any of ($vss*) and 1 of ($api*))
}""",

    "WannaCry": """rule WannaCry_Ransomware {
    meta:
        description = "Detects WannaCry / WannaCrypt ransomware"
        author      = "BarysGuard"
        severity    = "critical"
    strings:
        $s1 = "WannaCry" ascii nocase
        $s2 = "WannaDecryptor" ascii nocase
        $s3 = "tasksche.exe" ascii nocase
        $s4 = "@WanaDecryptor@.exe" ascii
        $s5 = "msg/m_chinese(simplified).wnry" ascii
        $h1 = { ED 1D 3C 15 }
        $kill = "MsWinZonesCacheCounterMutexA" ascii
    condition:
        2 of ($s*) or $h1 or $kill
}""",

    "Emotet": """rule Emotet_Trojan {
    meta:
        description = "Detects Emotet banking trojan indicators"
        author      = "BarysGuard"
        severity    = "critical"
    strings:
        $s1 = "emotet" ascii nocase
        $s2 = "geodo" ascii nocase
        $reg1 = "SOFTWARE\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Run" ascii nocase
        $net1 = { 47 45 54 20 2F }
        $enc1 = { C7 45 ?? 45 6D 6F 74 }
        $str1 = "UpdateTask" ascii
        $str2 = "svchost.exe" ascii
    condition:
        any of ($s*) or 2 of ($str*) or ($reg1 and ($net1 or $enc1))
}""",

    "AgentTesla": """rule AgentTesla_Stealer {
    meta:
        description = "Detects Agent Tesla keylogger/stealer"
        author      = "BarysGuard"
        severity    = "high"
    strings:
        $s1 = "AgentTesla" ascii nocase
        $s2 = "agent tesla" ascii nocase
        $s3 = "SmtpClient" ascii
        $s4 = "GetAsyncKeyState" ascii
        $s5 = "Keylogger" ascii nocase
        $s6 = "clipboard" ascii nocase
        $smtp = { 53 4D 54 50 }
    condition:
        2 of ($s*) or ($smtp and 2 of ($s*))
}""",

    "Njrat": """rule NjRat_RAT {
    meta:
        description = "Detects NjRat Remote Access Trojan"
        author      = "BarysGuard"
        severity    = "high"
    strings:
        $s1 = "njRAT" ascii nocase
        $s2 = "Bladabindi" ascii nocase
        $s3 = "|'|'|" ascii
        $s4 = "njq8" ascii nocase
        $net = { 7C 27 7C 27 7C }
        $mutex = "fuckav" ascii nocase
    condition:
        any of ($s*) or $net or $mutex
}""",

    "Keylogger_Generic": """rule Keylogger_Generic {
    meta:
        description = "Generic keylogger API usage patterns"
        author      = "BarysGuard"
        severity    = "high"
    strings:
        $api1 = "SetWindowsHookEx" ascii
        $api2 = "GetAsyncKeyState" ascii
        $api3 = "GetKeyState" ascii
        $api4 = "KeyboardProc" ascii
        $api5 = "WH_KEYBOARD_LL" ascii
        $log1 = "keylog" ascii nocase
        $log2 = "keystroke" ascii nocase
    condition:
        3 of ($api*) or (2 of ($api*) and 1 of ($log*))
}""",

    "ProcessInjection": """rule Process_Injection_Techniques {
    meta:
        description = "Detects common process injection API patterns"
        author      = "BarysGuard"
        severity    = "high"
    strings:
        $api1 = "VirtualAllocEx" ascii
        $api2 = "WriteProcessMemory" ascii
        $api3 = "CreateRemoteThread" ascii
        $api4 = "NtCreateThreadEx" ascii
        $api5 = "RtlCreateUserThread" ascii
        $api6 = "SetThreadContext" ascii
        $api7 = "QueueUserAPC" ascii
    condition:
        3 of them
}""",

    "DLL_Sideloading": """rule DLL_Sideloading_Indicators {
    meta:
        description = "Detects potential DLL sideloading artifacts"
        author      = "BarysGuard"
        severity    = "medium"
    strings:
        $s1 = "LoadLibrary" ascii
        $s2 = "GetProcAddress" ascii
        $s3 = "DllMain" ascii
        $s4 = "DllRegisterServer" ascii
        $path1 = "\\\\AppData\\\\Roaming\\\\" ascii nocase
        $path2 = "\\\\AppData\\\\Local\\\\Temp\\\\" ascii nocase
        $path3 = "%TEMP%" ascii nocase
    condition:
        ($s1 and $s2 and ($s3 or $s4)) and any of ($path*)
}""",

    "WebShell_PHP": """rule WebShell_PHP {
    meta:
        description = "Detects PHP web shells"
        author      = "BarysGuard"
        severity    = "high"
    strings:
        $s1 = "eval(base64_decode" ascii nocase
        $s2 = "eval(gzinflate" ascii nocase
        $s3 = "eval(str_rot13" ascii nocase
        $s4 = "system($_" ascii nocase
        $s5 = "exec($_" ascii nocase
        $s6 = "passthru($_" ascii nocase
        $s7 = "shell_exec($_" ascii nocase
        $s8 = "<?php eval" ascii nocase
    condition:
        any of them
}""",

    "Suspicious_Office_Macro": """rule Suspicious_Office_Macro {
    meta:
        description = "Detects potentially malicious Office macros"
        author      = "BarysGuard"
        severity    = "medium"
    strings:
        $s1 = "AutoOpen" ascii
        $s2 = "AutoExec" ascii
        $s3 = "Document_Open" ascii
        $s4 = "Shell(" ascii nocase
        $s5 = "WScript.Shell" ascii nocase
        $s6 = "powershell" ascii nocase
        $s7 = "cmd.exe" ascii nocase
        $s8 = "CreateObject" ascii
        $s9 = "Chr(" ascii
    condition:
        (any of ($s1,$s2,$s3)) and 3 of ($s4,$s5,$s6,$s7,$s8,$s9)
}""",

    "Network_Recon": """rule Network_Recon_Tools {
    meta:
        description = "Detects network reconnaissance tool signatures"
        author      = "BarysGuard"
        severity    = "medium"
    strings:
        $nmap = "nmap" ascii nocase
        $masscan = "masscan" ascii nocase
        $netcat = "netcat" ascii nocase
        $s1 = "port scan" ascii nocase
        $s2 = "SYN scan" ascii nocase
        $s3 = "OS detection" ascii nocase
        $s4 = "-sS " ascii
        $s5 = "-p 1-65535" ascii nocase
    condition:
        any of ($nmap,$masscan,$netcat) or 2 of ($s*)
}""",

    "Credential_Harvesting": """rule Credential_Harvesting {
    meta:
        description = "Detects credential harvesting patterns"
        author      = "BarysGuard"
        severity    = "high"
    strings:
        $s1 = "password" ascii nocase
        $s2 = "credential" ascii nocase
        $s3 = "LSASS" ascii nocase
        $s4 = "SAM database" ascii nocase
        $s5 = "hashdump" ascii nocase
        $api1 = "LsaRetrievePrivateData" ascii
        $api2 = "SamConnect" ascii
        $api3 = "CryptUnprotectData" ascii
    condition:
        2 of ($api*) or (3 of ($s*) and 1 of ($api*))
}""",

    "Persistence_Registry": """rule Persistence_Registry {
    meta:
        description = "Detects registry-based persistence mechanisms"
        author      = "BarysGuard"
        severity    = "medium"
    strings:
        $r1 = "CurrentVersion" ascii nocase
        $r2 = "RunOnce" ascii nocase
        $r3 = "Winlogon" ascii nocase
        $api1 = "RegSetValueEx" ascii
        $api2 = "RegCreateKey" ascii
    condition:
        any of ($r*) and any of ($api*)
}""",

    "UAC_Bypass": """rule UAC_Bypass_Techniques {
    meta:
        description = "Detects common UAC bypass techniques"
        author      = "BarysGuard"
        severity    = "high"
    strings:
        $s1 = "eventvwr.exe" ascii nocase
        $s2 = "fodhelper.exe" ascii nocase
        $s3 = "sdclt.exe" ascii nocase
        $s4 = "cmstp.exe" ascii nocase
        $s5 = "bypassuac" ascii nocase
        $reg = "ms-settings" ascii nocase
        $api = "ShellExecute" ascii
    condition:
        (2 of ($s*)) or ($reg and $api)
}""",

    "Lateral_Movement": """rule Lateral_Movement_SMB {
    meta:
        description = "Detects lateral movement via SMB/WMI"
        author      = "BarysGuard"
        severity    = "high"
    strings:
        $s1 = "psexec" ascii nocase
        $s2 = "wmiexec" ascii nocase
        $s3 = "smbexec" ascii nocase
        $s4 = "pass-the-hash" ascii nocase
        $s5 = "IPC$" ascii
        $smb = { 5C 00 5C 00 }
        $wmi1 = "Win32_Process" ascii
        $wmi2 = "WMI" ascii
    condition:
        any of ($s*) or (2 of ($wmi*) and $smb)
}""",

    "Anti_Analysis": """rule Anti_Analysis_Evasion {
    meta:
        description = "Detects anti-debugging and anti-analysis techniques"
        author      = "BarysGuard"
        severity    = "medium"
    strings:
        $d1 = "IsDebuggerPresent" ascii
        $d2 = "CheckRemoteDebuggerPresent" ascii
        $d3 = "NtQueryInformationProcess" ascii
        $vm1 = "VMware" ascii nocase
        $vm2 = "VirtualBox" ascii nocase
        $vm3 = "VBOX" ascii nocase
        $sand1 = "SbieDll.dll" ascii
        $sand2 = "sbiedll" ascii nocase
    condition:
        2 of ($d*) or 2 of ($vm*) or any of ($sand*)
}""",

    "EICAR_Test": """rule EICAR_Test_File {
    meta:
        description = "Detects EICAR antivirus test file"
        author      = "BarysGuard"
        severity    = "low"
    strings:
        $eicar = "EICAR-STANDARD-ANTIVIRUS-TEST-FILE" ascii
        $eicar2 = "X5O!P%@AP" ascii
    condition:
        any of them
}""",

    "LockBit": """rule LockBit_Ransomware {
    meta:
        description = "Detects LockBit ransomware artifacts"
        author      = "BarysGuard"
        severity    = "critical"
    strings:
        $s1 = "LockBit" ascii nocase
        $s2 = "lockbit" ascii nocase
        $s3 = "Restore-My-Files.txt" ascii nocase
        $s4 = "LockBit_Ransomware.hta" ascii nocase
        $s5 = ".lockbit" ascii nocase
        $vss1 = "vssadmin Delete Shadows" ascii nocase
        $vss2 = "wmic shadowcopy delete" ascii nocase
        $api1 = "CryptGenRandom" ascii
        $api2 = "CryptEncrypt" ascii
    condition:
        any of ($s*) or (any of ($vss*) and any of ($api*))
}""",

    "BlackCat_ALPHV": """rule BlackCat_ALPHV_Ransomware {
    meta:
        description = "Detects BlackCat/ALPHV ransomware"
        author      = "BarysGuard"
        severity    = "critical"
    strings:
        $s1 = "ALPHV" ascii nocase
        $s2 = "BlackCat" ascii nocase
        $s3 = "RECOVER-FILES.txt" ascii nocase
        $s4 = "alphv_keys" ascii nocase
        $s5 = "access_token" ascii
        $rust1 = "tokio_runtime" ascii
        $rust2 = "chacha20poly1305" ascii
        $rust3 = "aes_256_gcm" ascii nocase
    condition:
        any of ($s1,$s2,$s3,$s4) or (2 of ($rust*) and $s5)
}""",

    "RedLine_Stealer": """rule RedLine_Stealer {
    meta:
        description = "Detects RedLine credential stealer"
        author      = "BarysGuard"
        severity    = "critical"
    strings:
        $s1 = "RedLine" ascii nocase
        $s2 = "red_line" ascii nocase
        $net1 = "WalletPath" ascii
        $net2 = "BrowserData" ascii
        $net3 = "TelegramData" ascii
        $net4 = "FtpData" ascii
        $steal1 = "LoginData" ascii
        $steal2 = "Web Data" ascii
        $steal3 = "Cookies" ascii
        $steal4 = "Local State" ascii
    condition:
        any of ($s*) or 3 of ($net*) or 3 of ($steal*)
}""",

    "AsyncRAT": """rule AsyncRAT_Remote_Access {
    meta:
        description = "Detects AsyncRAT remote access trojan"
        author      = "BarysGuard"
        severity    = "high"
    strings:
        $s1 = "AsyncRAT" ascii nocase
        $s2 = "async-rat" ascii nocase
        $func1 = "SendHeartbeat" ascii
        $func2 = "ReceiveCommand" ascii
        $func3 = "ExecuteCommand" ascii
        $func4 = "KeyLoggerHandler" ascii
        $cfg1 = "Pastebin" ascii nocase
        $cfg2 = "aes256" ascii nocase
        $mutex = "AsyncMutex" ascii nocase
    condition:
        any of ($s*) or 2 of ($func*) or ($mutex and any of ($cfg*))
}""",

    "QuasarRAT": """rule QuasarRAT_Remote_Access {
    meta:
        description = "Detects Quasar RAT artifacts"
        author      = "BarysGuard"
        severity    = "high"
    strings:
        $s1 = "QuasarRAT" ascii nocase
        $s2 = "Quasar.Client" ascii
        $s3 = "Quasar.Common" ascii
        $func1 = "DoShellExecute" ascii
        $func2 = "DoDownloadAndExecute" ascii
        $func3 = "DoKeylogger" ascii
        $func4 = "GetSystemInfo" ascii
        $mutex = "QSR_MUTEX_" ascii
    condition:
        any of ($s*) or 2 of ($func*) or $mutex
}""",

    "Remcos_RAT": """rule Remcos_RAT {
    meta:
        description = "Detects Remcos Remote Control software used maliciously"
        author      = "BarysGuard"
        severity    = "high"
    strings:
        $s1 = "REMCOS" ascii nocase
        $s2 = "Remcos" ascii
        $s3 = "Breaking-Security" ascii nocase
        $reg1 = "Software\\\\Remcos" ascii nocase
        $func1 = "Remcos_Mutex" ascii nocase
        $func2 = "RC4_CRYPT" ascii nocase
        $str1 = "Screenshots" ascii
        $str2 = "Keylogger" ascii nocase
        $str3 = "AudioRecord" ascii
    condition:
        any of ($s*) or $reg1 or (2 of ($str*) and any of ($func*))
}""",

    "GuLoader": """rule GuLoader_Downloader {
    meta:
        description = "Detects GuLoader/CloudEyE malware downloader"
        author      = "BarysGuard"
        severity    = "high"
    strings:
        $s1 = "GuLoader" ascii nocase
        $s2 = "CloudEyE" ascii nocase
        $vbs1 = "WScript.Shell" ascii
        $vbs2 = "CreateObject" ascii
        $vbs3 = "PowerShell" ascii nocase
        $vbs4 = "Chr(" ascii
        $url1 = "OneDrive" ascii nocase
        $url2 = "GoogleDrive" ascii nocase
        $url3 = "Dropbox" ascii nocase
        $anti1 = "IsDebuggerPresent" ascii
        $anti2 = "GetTickCount" ascii
    condition:
        any of ($s*) or (3 of ($vbs*) and any of ($url*)) or (any of ($url*) and 2 of ($anti*))
}""",

    "Log4Shell_CVE_2021_44228": """rule Log4Shell_Exploitation {
    meta:
        description = "Detects Log4Shell CVE-2021-44228 exploitation attempts"
        author      = "BarysGuard"
        severity    = "critical"
    strings:
        $j1 = "${jndi:ldap://" ascii nocase
        $j2 = "${jndi:rmi://" ascii nocase
        $j3 = "${jndi:dns://" ascii nocase
        $j4 = "${jndi:ldaps://" ascii nocase
        $j5 = "${jndi:iiop://" ascii nocase
        $obf1 = "${${lower:" ascii nocase
        $obf2 = "${${upper:" ascii nocase
        $obf3 = "j${::-n}di" ascii nocase
        $obf4 = "%24%7bjndi" ascii nocase
    condition:
        any of ($j*) or 2 of ($obf*)
}""",

    "WebShell_ASPX": """rule WebShell_ASPX {
    meta:
        description = "Detects ASPX/ASP.NET web shells"
        author      = "BarysGuard"
        severity    = "high"
    strings:
        $s1 = "eval(Request" ascii nocase
        $s2 = "eval(base64" ascii nocase
        $s3 = "Response.Write(eval" ascii nocase
        $s4 = "ProcessStartInfo" ascii
        $s5 = "Process.Start" ascii
        $s6 = "cmd.exe /c" ascii nocase
        $s7 = "<%@ Page" ascii nocase
        $s8 = "<%@ WebHandler" ascii nocase
        $shell1 = "cmd_shell" ascii nocase
        $shell2 = "c99shell" ascii nocase
        $shell3 = "r57shell" ascii nocase
    condition:
        any of ($shell*) or (any of ($s7,$s8) and 2 of ($s1,$s2,$s3,$s4,$s5,$s6))
}""",

    "LOLBins_Abuse": """rule LOLBins_Living_Off_The_Land {
    meta:
        description = "Detects abuse of legitimate Windows binaries for malicious purposes"
        author      = "BarysGuard"
        severity    = "medium"
    strings:
        $certutil = "certutil -decode" ascii nocase
        $certutil2 = "certutil -urlcache" ascii nocase
        $mshta = "mshta http" ascii nocase
        $mshta2 = "mshta vbscript" ascii nocase
        $regsvr = "regsvr32 /s /n /u /i:http" ascii nocase
        $wscript = "wscript //e:jscript" ascii nocase
        $cscript = "cscript //e:vbscript" ascii nocase
        $rundll = "rundll32 javascript" ascii nocase
        $installutil = "installutil /logfile= /logtoconsole=false" ascii nocase
        $bitsadmin = "bitsadmin /transfer" ascii nocase
    condition:
        any of them
}""",

    "Packed_UPX": """rule Packed_UPX_Executable {
    meta:
        description = "Detects UPX-packed executables"
        author      = "BarysGuard"
        severity    = "low"
    strings:
        $upx1 = "UPX0" ascii
        $upx2 = "UPX1" ascii
        $upx3 = "UPX!" ascii
        $upx4 = { 55 50 58 21 }
        $upx5 = "This file is packed with the UPX" ascii
    condition:
        2 of ($upx1,$upx2,$upx3) or $upx4 or $upx5
}""",

    "Reverse_Shell_Linux": """rule Reverse_Shell_Linux {
    meta:
        description = "Detects Linux reverse shell patterns"
        author      = "BarysGuard"
        severity    = "high"
    strings:
        $bash1 = "bash -i >& /dev/tcp/" ascii nocase
        $bash2 = "bash -c 'bash -i" ascii nocase
        $nc1 = "nc -e /bin/bash" ascii nocase
        $nc2 = "nc -e /bin/sh" ascii nocase
        $nc3 = "ncat --exec /bin/bash" ascii nocase
        $py1 = "socket.SOCK_STREAM" ascii
        $py2 = "os.dup2(s.fileno()" ascii
        $py3 = "subprocess.call(['/bin/sh'" ascii
        $perl1 = "/bin/sh -i" ascii nocase
        $php1 = "fsockopen" ascii nocase
    condition:
        any of ($bash*,$nc*) or 2 of ($py*) or $perl1 or $php1
}""",

    "Credential_Files_Access": """rule Credential_Files_Access {
    meta:
        description = "Detects access to sensitive credential storage files"
        author      = "BarysGuard"
        severity    = "high"
    strings:
        $f1 = "\\\\Login Data" ascii nocase
        $f2 = "\\\\Cookies" ascii nocase
        $f3 = "\\\\Web Data" ascii nocase
        $f4 = "\\\\Local State" ascii nocase
        $f5 = "id_rsa" ascii nocase
        $f6 = ".ssh\\\\known_hosts" ascii nocase
        $f7 = "credentials.xml" ascii nocase
        $f8 = "KeePass.kdbx" ascii nocase
        $f9 = "wallet.dat" ascii nocase
        $f10 = "secret_key" ascii nocase
        $browser1 = "\\\\Chrome\\\\User Data" ascii nocase
        $browser2 = "\\\\Firefox\\\\Profiles" ascii nocase
        $browser3 = "\\\\Edge\\\\User Data" ascii nocase
    condition:
        3 of ($f*) or 2 of ($browser*)
}""",

    "Scheduled_Task_Abuse": """rule Scheduled_Task_Abuse {
    meta:
        description = "Detects scheduled task-based persistence"
        author      = "BarysGuard"
        severity    = "medium"
    strings:
        $cmd1 = "schtasks /create" ascii nocase
        $cmd2 = "schtasks /run" ascii nocase
        $cmd3 = "at.exe" ascii nocase
        $api1 = "ITaskScheduler" ascii
        $api2 = "ITaskService" ascii
        $api3 = "RegisterTaskDefinition" ascii
        $xml1 = "<ScheduledTask" ascii nocase
        $xml2 = "<Actions>" ascii nocase
        $xml3 = "<Exec>" ascii nocase
        $ps1 = "Register-ScheduledTask" ascii nocase
        $ps2 = "New-ScheduledTask" ascii nocase
    condition:
        any of ($cmd*) or 2 of ($api*) or 2 of ($xml*) or any of ($ps*)
}""",

    "DefenderTampering": """rule Windows_Defender_Tampering {
    meta:
        description = "Detects attempts to disable or tamper with Windows Defender"
        author      = "BarysGuard"
        severity    = "high"
    strings:
        $ps1 = "Set-MpPreference -DisableRealtimeMonitoring" ascii nocase
        $ps2 = "Set-MpPreference -DisableIOAVProtection" ascii nocase
        $ps3 = "Add-MpPreference -ExclusionPath" ascii nocase
        $ps4 = "Set-MpPreference -MAPSReporting 0" ascii nocase
        $reg1 = "SOFTWARE\\\\Policies\\\\Microsoft\\\\Windows Defender" ascii nocase
        $reg2 = "DisableAntiSpyware" ascii nocase
        $reg3 = "DisableRealtimeMonitoring" ascii nocase
        $sc1 = "sc stop WinDefend" ascii nocase
        $sc2 = "sc delete WinDefend" ascii nocase
        $sc3 = "net stop WinDefend" ascii nocase
    condition:
        any of ($ps*) or (2 of ($reg*)) or any of ($sc*)
}""",

    "AMSI_Bypass": """rule AMSI_Bypass_Techniques {
    meta:
        description = "Detects AMSI (Antimalware Scan Interface) bypass techniques"
        author      = "BarysGuard"
        severity    = "high"
    strings:
        $s1 = "amsiInitFailed" ascii nocase
        $s2 = "AmsiScanBuffer" ascii nocase
        $s3 = "amsi.dll" ascii nocase
        $patch1 = { 48 31 C0 C3 }
        $patch2 = { B8 57 00 07 80 C3 }
        $ps1 = "[Ref].Assembly.GetType" ascii nocase
        $ps2 = "System.Management.Automation.AmsiUtils" ascii nocase
        $ps3 = "amsiContext" ascii nocase
        $ps4 = "amsiSession" ascii nocase
    condition:
        2 of ($s*) or any of ($patch*) or (2 of ($ps*))
}""",

    "Vidar_Stealer": """rule Vidar_Stealer {
    meta:
        description = "Detects Vidar information stealer"
        author      = "BarysGuard"
        severity    = "critical"
    strings:
        $s1 = "Vidar" ascii nocase
        $s2 = "vidar_stealer" ascii nocase
        $cfg1 = "sqlite3.dll" ascii nocase
        $cfg2 = "nss3.dll" ascii nocase
        $cfg3 = "softokn3.dll" ascii nocase
        $grab1 = "FileZilla" ascii nocase
        $grab2 = "Telegram Desktop" ascii nocase
        $grab3 = "WinSCP" ascii nocase
        $grab4 = "Total Commander" ascii nocase
        $net1 = "multipart/form-data" ascii
        $net2 = "hwid=" ascii nocase
    condition:
        any of ($s*) or 2 of ($cfg*) or (3 of ($grab*) and ($net1 or $net2))
}""",

    "Chisel_Tunnel": """rule Chisel_Tunneling_Tool {
    meta:
        description = "Detects Chisel network tunneling tool used in attacks"
        author      = "BarysGuard"
        severity    = "medium"
    strings:
        $s1 = "chisel" ascii nocase
        $s2 = "github.com/jpillora/chisel" ascii nocase
        $s3 = "chisel server" ascii nocase
        $s4 = "chisel client" ascii nocase
        $func1 = "socks5 proxy" ascii nocase
        $func2 = "reverse tunnel" ascii nocase
        $func3 = "nhooyr.io/websocket" ascii
    condition:
        any of ($s2,$s3,$s4) or (($s1 or $s2) and any of ($func*))
}""",

    "Sliver_C2": """rule Sliver_C2_Framework {
    meta:
        description = "Detects Sliver C2 framework implants"
        author      = "BarysGuard"
        severity    = "critical"
    strings:
        $s1 = "sliver" ascii nocase
        $s2 = "github.com/bishopfox/sliver" ascii nocase
        $s3 = "sliver-implant" ascii nocase
        $func1 = "SliverRPC" ascii
        $func2 = "PivotConnect" ascii
        $func3 = "PortfwdTunnel" ascii
        $go1 = "BishopFox" ascii nocase
        $go2 = "implant_name" ascii nocase
    condition:
        $s2 or $s3 or 2 of ($func*) or ($s1 and ($go1 or $go2))
}""",

    "Havoc_C2": """rule Havoc_C2_Framework {
    meta:
        description = "Detects Havoc C2 framework demon implants"
        author      = "BarysGuard"
        severity    = "critical"
    strings:
        $s1 = "Havoc" ascii nocase
        $s2 = "HavocFramework" ascii nocase
        $s3 = "HavocC2" ascii nocase
        $demon1 = "DemonID" ascii
        $demon2 = "DemonMetaData" ascii
        $demon3 = "DEMON_MAGIC_VALUE" ascii
        $func1 = "AdjustTokenPrivileges" ascii
        $func2 = "NtCreateSection" ascii
    condition:
        any of ($s2,$s3) or 2 of ($demon*) or ($s1 and 2 of ($func*))
}""",
}

