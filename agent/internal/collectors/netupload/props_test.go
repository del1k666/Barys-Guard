package netupload

import (
	"net"
	"net/netip"
	"testing"
)

func TestAsUint(t *testing.T) {
	cases := []struct {
		in   any
		want uint64
		ok   bool
	}{
		{"42", 42, true},
		{" 42 ", 42, true},
		{"0x1F", 31, true},
		{"0xffffffffffffffff", 1<<64 - 1, true},
		{"", 0, false},
		{"abc", 0, false},
		{"-1", 0, false},
		{uint32(5), 5, true},
		{uint64(7), 7, true},
		{int32(-1), 0, false},
		{int64(9), 9, true},
		{nil, 0, false},
		{3.5, 0, false},
	}
	for _, c := range cases {
		got, ok := asUint(c.in)
		if ok != c.ok || got != c.want {
			t.Errorf("asUint(%#v) = %d, %v; want %d, %v", c.in, got, ok, c.want, c.ok)
		}
	}
}

func TestAsAddr(t *testing.T) {
	want := netip.MustParseAddr("1.2.3.4")
	cases := []any{
		net.ParseIP("1.2.3.4"),
		net.ParseIP("1.2.3.4").To4(),
		"1.2.3.4",
		" 1.2.3.4 ",
		"::ffff:1.2.3.4",
		"1.2.3.4:443",
		"[::ffff:1.2.3.4]:443",
	}
	for _, in := range cases {
		got, ok := asAddr(in)
		if !ok || got != want {
			t.Errorf("asAddr(%#v) = %v, %v; want %v", in, got, ok, want)
		}
	}
	v6, ok := asAddr("2001:db8::1")
	if !ok || v6 != netip.MustParseAddr("2001:db8::1") {
		t.Errorf("ipv6: %v %v", v6, ok)
	}
	for _, bad := range []any{"", "not-an-ip", nil, 12345, uint32(1)} {
		if _, ok := asAddr(bad); ok {
			t.Errorf("asAddr(%#v) должен отказать", bad)
		}
	}
}

func TestFileTableFirstReadOnce(t *testing.T) {
	table := newFileTable()
	if _, ok := table.firstRead(1); ok {
		t.Fatal("неизвестный объект не должен читаться")
	}
	table.set(1, `C:\a.pdf`)
	path, ok := table.firstRead(1)
	if !ok || path != `C:\a.pdf` {
		t.Fatalf("первое чтение: %q %v", path, ok)
	}
	if _, ok := table.firstRead(1); ok {
		t.Fatal("второе чтение того же объекта не должно отдаваться")
	}
}

func TestFileTableDrop(t *testing.T) {
	table := newFileTable()
	table.set(1, `C:\a.pdf`)
	table.drop(1)
	if _, ok := table.firstRead(1); ok {
		t.Fatal("после drop чтение не должно отдаваться")
	}
	// повторное создание объекта с тем же номером снова даёт одно чтение
	table.set(1, `C:\b.pdf`)
	if path, ok := table.firstRead(1); !ok || path != `C:\b.pdf` {
		t.Fatalf("после повторного set: %q %v", path, ok)
	}
}

func TestFileTableResetsAtBound(t *testing.T) {
	table := newFileTableLimit(3)
	for i := uint64(1); i <= 3; i++ {
		table.set(i, "p")
	}
	if table.size() != 3 {
		t.Fatalf("размер = %d, ожидалось 3", table.size())
	}
	// перезапись существующего ключа не сбрасывает таблицу
	table.set(2, "q")
	if table.size() != 3 {
		t.Fatalf("после перезаписи размер = %d", table.size())
	}
	table.set(4, "p")
	if table.size() != 1 {
		t.Fatalf("после переполнения размер = %d, ожидалось 1", table.size())
	}
	if _, ok := table.firstRead(1); ok {
		t.Fatal("старые записи должны быть сброшены")
	}
	if _, ok := table.firstRead(4); !ok {
		t.Fatal("новая запись должна сохраниться")
	}
	for i := uint64(10); i < 1000; i++ {
		table.set(i, "p")
		if table.size() > 3 {
			t.Fatalf("размер %d превысил предел", table.size())
		}
	}
}
