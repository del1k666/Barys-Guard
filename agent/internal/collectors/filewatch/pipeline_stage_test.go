package filewatch

import (
	"strings"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/artifacts"
)

func TestCreateOnAFlashDriveIsStaged(t *testing.T) {
	stager := &fakeStager{sink: &fakeSink{}}
	h := newHarness(t, func(d *PipelineDeps, _ *Config) { d.Stager = stager })
	h.files[dst] = strings.Repeat("секретные данные ", 200)

	h.pipeline.Handle(Raw{Kind: Created, Path: dst})
	h.advance(2 * time.Second)

	if len(stager.calls) != 1 {
		t.Fatalf("Begin вызван %d раз, ожидалось 1", len(stager.calls))
	}
	if stager.sink.committed == "" {
		t.Fatal("копия не завершена")
	}
}

func TestFileOutsideRemovableVolumesIsNeverStaged(t *testing.T) {
	stager := &fakeStager{sink: &fakeSink{}}
	h := newHarness(t, func(d *PipelineDeps, _ *Config) { d.Stager = stager })
	h.files[src] = strings.Repeat("данные ", 300)

	h.pipeline.Handle(Raw{Kind: Modified, Path: src})
	h.advance(2 * time.Second)

	if len(stager.calls) != 0 {
		t.Fatalf("файл наблюдаемой папки скопирован для загрузки (%d вызовов)", len(stager.calls))
	}
}

func TestDeleteIsNeverStaged(t *testing.T) {
	stager := &fakeStager{sink: &fakeSink{}}
	h := newHarness(t, func(d *PipelineDeps, _ *Config) { d.Stager = stager })

	h.pipeline.Handle(Raw{Kind: Deleted, Path: dst})
	h.advance(2 * time.Second)

	if len(stager.calls) != 0 {
		t.Fatal("удаление породило копию")
	}
}

func TestRateSkipIsMarkedOnTheEvent(t *testing.T) {
	stager := &fakeStager{skip: artifacts.SkipRate}
	h := newHarness(t, func(d *PipelineDeps, _ *Config) { d.Stager = stager })
	h.files[dst] = strings.Repeat("данные ", 300)

	h.pipeline.Handle(Raw{Kind: Created, Path: dst})
	h.advance(2 * time.Second)

	if len(h.emitted) != 1 {
		t.Fatalf("событий %d", len(h.emitted))
	}
	if h.emitted[0].Labels["artifact_skipped"] != "rate" {
		t.Fatalf("метки = %+v: нет artifact_skipped=rate", h.emitted[0].Labels)
	}
	if h.emitted[0].Artifact == nil {
		t.Fatal("событие потеряло artifact: пропуск копии не должен лишать его хеша")
	}
}

func TestOtherSkipReasonsLeaveNoLabel(t *testing.T) {
	stager := &fakeStager{skip: artifacts.SkipSize}
	h := newHarness(t, func(d *PipelineDeps, _ *Config) { d.Stager = stager })
	h.files[dst] = strings.Repeat("данные ", 300)

	h.pipeline.Handle(Raw{Kind: Created, Path: dst})
	h.advance(2 * time.Second)

	if _, has := h.emitted[0].Labels["artifact_skipped"]; has {
		t.Fatal("метка artifact_skipped нужна только для rate")
	}
}
