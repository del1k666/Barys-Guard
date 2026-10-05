package transport_test

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"

	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/transport"
)

type openAPI struct {
	Components struct {
		Schemas map[string]struct {
			Properties map[string]any `yaml:"properties"`
			Required   []string       `yaml:"required"`
		} `yaml:"schemas"`
	} `yaml:"components"`
}

func loadContract(t *testing.T) openAPI {
	t.Helper()
	// Контракт лежит вне модуля агента: он общий с сервером.
	path := filepath.Join("..", "..", "..", "api", "gateway-v1.yaml")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("чтение контракта %s: %v", path, err)
	}
	var document openAPI
	if err := yaml.Unmarshal(raw, &document); err != nil {
		t.Fatalf("разбор контракта: %v", err)
	}
	return document
}

// jsonFields собирает имена из тегов json структуры.
func jsonFields(value any) map[string]bool {
	fields := map[string]bool{}
	typ := reflect.TypeOf(value)
	for i := 0; i < typ.NumField(); i++ {
		tag := typ.Field(i).Tag.Get("json")
		if tag == "" || tag == "-" {
			continue
		}
		fields[strings.Split(tag, ",")[0]] = true
	}
	return fields
}

func TestStructsCoverEveryRequiredContractField(t *testing.T) {
	contract := loadContract(t)

	cases := []struct {
		schema string
		value  any
	}{
		{"EnrollRequest", transport.EnrollRequest{}},
		{"EnrollResponse", transport.EnrollResponse{}},
		{"RenewRequest", transport.RenewRequest{}},
		{"RenewResponse", transport.RenewResponse{}},
		{"HeartbeatRequest", transport.HeartbeatRequest{}},
		{"HeartbeatResponse", transport.HeartbeatResponse{}},
		{"QueuedCommand", transport.QueuedCommand{}},
		{"CommandResultRequest", transport.CommandResultRequest{}},
		{"EventEnvelope", events.Envelope{}},
		{"EventsResult", transport.EventsResult{}},
	}

	for _, testCase := range cases {
		t.Run(testCase.schema, func(t *testing.T) {
			schema, ok := contract.Components.Schemas[testCase.schema]
			if !ok {
				t.Fatalf("схемы %s нет в контракте", testCase.schema)
			}
			fields := jsonFields(testCase.value)
			for _, required := range schema.Required {
				if !fields[required] {
					t.Errorf("обязательное поле %q отсутствует в структуре Go", required)
				}
			}
		})
	}
}

func TestNoStructFieldIsAbsentFromContract(t *testing.T) {
	// Обратная проверка: поле, которого нет в контракте, сервер проигнорирует,
	// и агент будет считать, что сообщил то, чего не сообщал.
	contract := loadContract(t)

	cases := []struct {
		schema string
		value  any
	}{
		{"HeartbeatRequest", transport.HeartbeatRequest{}},
		{"EnrollRequest", transport.EnrollRequest{}},
		{"CommandResultRequest", transport.CommandResultRequest{}},
		{"EventEnvelope", events.Envelope{}},
		{"EventsResult", transport.EventsResult{}},
	}

	for _, testCase := range cases {
		t.Run(testCase.schema, func(t *testing.T) {
			schema := contract.Components.Schemas[testCase.schema]
			for field := range jsonFields(testCase.value) {
				if _, ok := schema.Properties[field]; !ok {
					t.Errorf("поле %q отсутствует в схеме %s контракта", field, testCase.schema)
				}
			}
		})
	}
}
