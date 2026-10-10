package netupload

import (
	"testing"
	"time"
)

func TestFamilyRootClimbsThroughSameImage(t *testing.T) {
	parents := map[uint32]uint32{12920: 3500, 3500: 10644, 10644: 4}
	images := map[uint32]string{12920: `C:\Brave\brave.exe`, 3500: `c:\brave\BRAVE.exe`, 10644: `C:\Windows\explorer.exe`, 4: ""}
	root := familyRoot(12920,
		func(p uint32) (uint32, bool) { v, ok := parents[p]; return v, ok },
		func(p uint32) string { return images[p] })
	if root != 3500 {
		t.Fatalf("root = %d, want 3500", root)
	}
}

func TestFamilyRootUnknownImageStaysAlone(t *testing.T) {
	root := familyRoot(7, func(uint32) (uint32, bool) { return 1, true }, func(uint32) string { return "" })
	if root != 7 {
		t.Fatalf("root = %d, want 7", root)
	}
}

func TestFamilyRootStopsOnCycle(t *testing.T) {
	root := familyRoot(5,
		func(p uint32) (uint32, bool) { return map[uint32]uint32{5: 6, 6: 5}[p], true },
		func(uint32) string { return "a.exe" })
	if root != 5 && root != 6 {
		t.Fatalf("unexpected root %d", root)
	}
}

func TestFamilyCacheResolvesOncePerTTL(t *testing.T) {
	now := time.Unix(0, 0)
	calls := 0
	cache := newFamilyCache(func(p uint32) uint32 { calls++; return p + 1 }, func() time.Time { return now })
	if cache.Root(1) != 2 || cache.Root(1) != 2 || calls != 1 {
		t.Fatalf("calls = %d", calls)
	}
	now = now.Add(familyTTL + time.Second)
	cache.Root(1)
	if calls != 2 {
		t.Fatalf("calls after ttl = %d", calls)
	}
}
