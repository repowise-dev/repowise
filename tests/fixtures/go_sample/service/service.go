// Package service wires a Store into a runnable unit.
package service

import "example.com/gosample/store"

// Service holds the injected store and, as its only use of store.Config, a
// configuration field.
type Service struct {
	backing store.Store
	cfg     *store.Config
}

// New builds a Service from a Store. Called cross-package from cmd/app via
// the package-qualified call service.New(...).
func New(s store.Store) *Service {
	return &Service{backing: s}
}

// Run uses the injected store and a private helper defined in the sibling
// file helpers.go (same-package cross-file reference).
func (s *Service) Run() {
	s.backing.Put("greeting", greeting())
	_ = s.backing.Get("greeting")
}
