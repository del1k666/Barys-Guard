package lifecycle_test

import (
	"context"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/collectors/lifecycle"
	"github.com/barysguard/agent/internal/events"
)

func TestEmitsStartThenStopOnCancel(t *testing.T) {
	collector := lifecycle.New("1.2.3")
	var got []events.Envelope
	emit := func(e events.Envelope) { got = append(got, e) }

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- collector.Run(ctx, emit) }()

	time.Sleep(50 * time.Millisecond)
	cancel()
	if err := <-done; err != nil {
		t.Fatalf("Run: %v", err)
	}

	if len(got) != 2 || got[0].Action != "start" || got[1].Action != "stop" {
		t.Fatalf("события: %+v", got)
	}
	for _, e := range got {
		if e.Channel != events.ChannelAgent || e.Subject["component"] != "agent" {
			t.Errorf("неверный конверт: %+v", e)
		}
	}
	if got[0].Subject["detail"] != "version 1.2.3" {
		t.Errorf("detail = %v", got[0].Subject["detail"])
	}
	if collector.Name() != "lifecycle" {
		t.Errorf("Name = %q", collector.Name())
	}
}
