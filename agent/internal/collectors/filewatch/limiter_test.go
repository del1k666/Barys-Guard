package filewatch

import (
	"testing"
	"time"
)

func TestLimiterAllowsPerSecondBudgetThenRefills(t *testing.T) {
	limiter := NewLimiter(3)
	allowed := 0
	for i := 0; i < 10; i++ {
		if limiter.Allow(t0) {
			allowed++
		}
	}
	if allowed != 3 {
		t.Fatalf("разрешено %d, ожидалось 3", allowed)
	}
	if !limiter.Allow(t0.Add(1100 * time.Millisecond)) {
		t.Fatal("после секунды бюджет должен восстановиться")
	}
}
