package filewatch

import (
	"fmt"
	"testing"
	"time"
)

func TestIndexFindsByHashAndSize(t *testing.T) {
	idx := NewHashIndex(10, time.Hour)
	idx.Put("aa", 100, `C:\Users\u\Documents\a.docx`, t0)

	if path, ok := idx.Lookup("aa", 100, t0.Add(time.Minute)); !ok || path != `C:\Users\u\Documents\a.docx` {
		t.Fatalf("lookup: %q %v", path, ok)
	}
	if _, ok := idx.Lookup("aa", 101, t0); ok {
		t.Fatal("другой размер не должен совпадать")
	}
	if _, ok := idx.Lookup("bb", 100, t0); ok {
		t.Fatal("другой хеш не должен совпадать")
	}
}

func TestIndexEntriesExpire(t *testing.T) {
	idx := NewHashIndex(10, time.Hour)
	idx.Put("aa", 1, `C:\a`, t0)

	if _, ok := idx.Lookup("aa", 1, t0.Add(2*time.Hour)); ok {
		t.Fatal("просроченная запись найдена")
	}
}

func TestIndexEvictsTheLeastRecentlyUsed(t *testing.T) {
	idx := NewHashIndex(3, time.Hour)
	for i := 0; i < 3; i++ {
		idx.Put(fmt.Sprint("h", i), 1, fmt.Sprint(`C:\f`, i), t0)
	}
	idx.Lookup("h0", 1, t0) // h0 стал свежим, вытеснится h1
	idx.Put("h3", 1, `C:\f3`, t0)

	if _, ok := idx.Lookup("h1", 1, t0); ok {
		t.Fatal("h1 должен был вытесниться")
	}
	for _, key := range []string{"h0", "h2", "h3"} {
		if _, ok := idx.Lookup(key, 1, t0); !ok {
			t.Fatalf("%s вытеснен напрасно", key)
		}
	}
}

func TestPutOfTheSameContentUpdatesThePath(t *testing.T) {
	idx := NewHashIndex(3, time.Hour)
	idx.Put("aa", 1, `C:\old`, t0)
	idx.Put("aa", 1, `C:\new`, t0.Add(time.Second))

	if path, _ := idx.Lookup("aa", 1, t0.Add(2*time.Second)); path != `C:\new` {
		t.Fatalf("путь: %q", path)
	}
}
