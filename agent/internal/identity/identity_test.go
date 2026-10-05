package identity

import "testing"

func TestIsServiceSID(t *testing.T) {
	service := []string{"S-1-5-18", "S-1-5-32-544", "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464", "S-1-5-19", "S-1-5-20"}
	for _, sid := range service {
		if !IsServiceSID(sid) {
			t.Errorf("%s должен считаться служебным", sid)
		}
	}
	user := []string{"S-1-5-21-1004336348-1177238915-682003330-1013", "S-1-12-1-111-222-333-444", ""}
	for _, sid := range user {
		if IsServiceSID(sid) {
			t.Errorf("%q не служебный", sid)
		}
	}
}

func TestPickPrefersARealOwnerOverTheConsoleUser(t *testing.T) {
	owner := map[string]any{"user_sid": "S-1-5-21-1-2-3-1001", "user_name": "PC\\owner"}
	console := map[string]any{"user_sid": "S-1-5-21-1-2-3-1002", "user_name": "PC\\console"}

	if got := Pick(owner, console); got["user_name"] != "PC\\owner" {
		t.Fatalf("выбран %v", got)
	}
}

func TestPickFallsBackToConsoleForServiceOrUnknownOwner(t *testing.T) {
	service := map[string]any{"user_sid": "S-1-5-18", "user_name": "NT AUTHORITY\\SYSTEM"}
	console := map[string]any{"user_sid": "S-1-5-21-1-2-3-1002", "user_name": "PC\\console"}

	if got := Pick(service, console); got["user_name"] != "PC\\console" {
		t.Fatalf("для SYSTEM выбран %v", got)
	}
	if got := Pick(nil, console); got["user_name"] != "PC\\console" {
		t.Fatalf("без владельца выбран %v", got)
	}
	if got := Pick(nil, nil); got != nil {
		t.Fatalf("без данных выбран %v", got)
	}
	// Служебный владелец лучше пустоты: хотя бы видно, что файл системный.
	if got := Pick(service, nil); got["user_name"] != "NT AUTHORITY\\SYSTEM" {
		t.Fatalf("без консоли выбран %v", got)
	}
}
