package events

import (
	"errors"
	"sync"
	"testing"
)

func envelope(t *testing.T, action string) Envelope {
	t.Helper()
	env, err := NewEnvelope(ChannelAgent, action, SeverityInfo, nil)
	if err != nil {
		t.Fatal(err)
	}
	return env
}

func TestDrainDeliversEveryEmittedEventInOrder(t *testing.T) {
	queue := NewQueue(10)
	var got []string
	done := make(chan struct{})
	go func() {
		queue.Drain(func(e Envelope) error { got = append(got, e.Action); return nil }, nil)
		close(done)
	}()

	for _, action := range []string{"a", "b", "c"} {
		queue.Emit(envelope(t, action))
	}
	queue.Close()
	<-done

	if len(got) != 3 || got[0] != "a" || got[1] != "b" || got[2] != "c" {
		t.Fatalf("получено %v", got)
	}
}

func TestEmitNeverBlocksAndOverflowIsReportedAsDroppedEvent(t *testing.T) {
	queue := NewQueue(2)
	// Приёмника нет: очередь переполняется. Emit не должен зависнуть.
	for i := 0; i < 5; i++ {
		queue.Emit(envelope(t, "x"))
	}

	var actions []string
	var counts []any
	queue.Close()
	queue.Drain(func(e Envelope) error {
		actions = append(actions, e.Action)
		if e.Action == "events_dropped" {
			counts = append(counts, e.Subject["count"])
		}
		return nil
	}, nil)

	// Две принятые и ровно один отчёт о трёх потерянных; где именно в потоке
	// окажется отчёт, не оговаривается.
	if len(actions) != 3 {
		t.Fatalf("действия: %v", actions)
	}
	if len(counts) != 1 || counts[0] != uint64(3) {
		t.Fatalf("отчёты о потерях: %v, ожидался один с count=3", counts)
	}
}

func TestEmitAfterCloseDoesNotPanic(t *testing.T) {
	queue := NewQueue(1)
	queue.Close()

	queue.Emit(envelope(t, "late"))
}

func TestAppendFailureAndBufferLossAreBothCounted(t *testing.T) {
	queue := NewQueue(10)
	queue.Emit(envelope(t, "fails"))
	queue.Close()

	var reported []Envelope
	lostOnce := uint64(2)
	queue.Drain(func(e Envelope) error {
		if e.Action == "fails" {
			return errors.New("диск полон")
		}
		reported = append(reported, e)
		return nil
	}, func() uint64 { n := lostOnce; lostOnce = 0; return n })

	if len(reported) != 1 || reported[0].Subject["count"] != uint64(3) {
		t.Fatalf("отчёт: %+v", reported)
	}
}

func TestEmitIsSafeFromManyGoroutines(t *testing.T) {
	queue := NewQueue(1000)
	var wg sync.WaitGroup
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 20; j++ {
				queue.Emit(envelope(t, "p"))
			}
		}()
	}
	wg.Wait()
	queue.Close()

	count := 0
	queue.Drain(func(Envelope) error { count++; return nil }, nil)
	if count != 400 {
		t.Fatalf("доставлено %d из 400", count)
	}
}
