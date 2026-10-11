package service

// greeting is a private sibling-file helper, called by Service.Run in
// service.go. It must not be flagged as unused_internal.
func greeting() string {
	return "hello"
}

// DeadExport is exported but referenced from nowhere in the module — the
// planted genuinely-dead symbol the analyzer MUST still surface. If this
// stops being reported, the Go dead-code pass has gone silent.
func DeadExport() {}
