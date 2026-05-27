package store

// MemStore is an in-memory Store implementation. It satisfies Store
// structurally via its Get/Put method set.
type MemStore struct {
	data map[string]string
}

// NewMemStore constructs a MemStore. Called cross-package from cmd/app.
func NewMemStore() *MemStore {
	return &MemStore{data: make(map[string]string)}
}

func (m *MemStore) Get(key string) string { return m.data[key] }

func (m *MemStore) Put(key, value string) { m.data[key] = value }
