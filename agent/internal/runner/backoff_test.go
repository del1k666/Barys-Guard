package runner_test

import (
	"math/rand"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/runner"
)

func TestBackoffNeverExceedsGrowingCeiling(t *testing.T) {
	backoff := runner.NewBackoff(time.Second, 300*time.Second, rand.NewSource(1))

	// Полный джиттер: значение лежит в [0, min(max, base*2^n)].
	ceilings := []time.Duration{1, 2, 4, 8, 16, 32}
	for attempt, ceiling := range ceilings {
		got := backoff.Next()
		if got < 0 || got > ceiling*time.Second {
			t.Fatalf("попытка %d: пауза %v вне [0, %v]", attempt, got, ceiling*time.Second)
		}
	}
}

func TestBackoffRespectsCeiling(t *testing.T) {
	backoff := runner.NewBackoff(time.Second, 10*time.Second, rand.NewSource(2))

	for i := 0; i < 50; i++ {
		if got := backoff.Next(); got > 10*time.Second {
			t.Fatalf("пауза %v превысила потолок", got)
		}
	}
}

func TestBackoffIsRandomAcrossSources(t *testing.T) {
	// Без джиттера пять тысяч агентов после перезапуска сервера
	// сохраняют форму волны и укладывают его повторно.
	first := runner.NewBackoff(time.Second, 300*time.Second, rand.NewSource(1))
	second := runner.NewBackoff(time.Second, 300*time.Second, rand.NewSource(2))

	same := 0
	for i := 0; i < 8; i++ {
		if first.Next() == second.Next() {
			same++
		}
	}
	if same == 8 {
		t.Fatal("две независимые последовательности совпали целиком")
	}
}

func TestResetReturnsToTheFirstStep(t *testing.T) {
	backoff := runner.NewBackoff(time.Second, 300*time.Second, rand.NewSource(3))
	for i := 0; i < 6; i++ {
		backoff.Next()
	}

	backoff.Reset()
	if got := backoff.Next(); got > time.Second {
		t.Fatalf("после сброса пауза %v, ожидалось не больше базовой", got)
	}
}

func TestJitterIntervalStaysWithinTenPercent(t *testing.T) {
	random := rand.New(rand.NewSource(4))
	base := 30 * time.Second

	for i := 0; i < 200; i++ {
		got := runner.JitterInterval(base, random)
		if got < 27*time.Second || got > 33*time.Second {
			t.Fatalf("интервал %v вне ±10%% от %v", got, base)
		}
	}
}

func TestJitterIntervalActuallyVaries(t *testing.T) {
	// Флот, развёрнутый одной волной, обязан разойтись во времени.
	random := rand.New(rand.NewSource(5))
	seen := map[time.Duration]bool{}
	for i := 0; i < 50; i++ {
		seen[runner.JitterInterval(30*time.Second, random)] = true
	}
	if len(seen) < 5 {
		t.Fatalf("получено лишь %d различных интервалов", len(seen))
	}
}
