package netupload

import "testing"

func TestNTToDOS(t *testing.T) {
	devices := map[string]string{
		`\device\harddiskvolume3`:  `C:`,
		`\device\harddiskvolume31`: `E:`,
	}

	cases := []struct {
		in, want string
		ok       bool
	}{
		{`\Device\HarddiskVolume3\Users\a\План.pdf`, `C:\Users\a\План.pdf`, true},
		{`\DEVICE\HARDDISKVOLUME31\x.docx`, `E:\x.docx`, true},
		{`\Device\HarddiskVolume3`, `C:\`, true},
		{`\Device\HarddiskVolume33\x.pdf`, ``, false}, // том 33, а не 3
		{`\Device\Mup\server\share\a.pdf`, ``, false},
		{`C:\already\dos.pdf`, ``, false},
		{``, ``, false},
	}
	for _, tc := range cases {
		got, ok := ntToDOS(tc.in, devices)
		if got != tc.want || ok != tc.ok {
			t.Errorf("ntToDOS(%q) = %q, %v; ждали %q, %v", tc.in, got, ok, tc.want, tc.ok)
		}
	}
}
