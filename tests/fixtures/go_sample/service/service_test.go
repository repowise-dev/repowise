package service

import "testing"

// TestRun is present so the fixture has a _test.go file; the file (and its
// Test function) must be exempt from dead-code flagging by convention.
func TestRun(t *testing.T) {
	svc := New(nil)
	if svc == nil {
		t.Fatal("expected a service")
	}
}
