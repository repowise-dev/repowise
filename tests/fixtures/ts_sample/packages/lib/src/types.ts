// Interface used only as a field type — must NOT be flagged unused_export.
// Phase 2's type_use edges should connect User -> UserService.
export interface User {
  id: string;
  name: string;
}

// Genuinely-dead exported interface — referenced nowhere as a value or as a
// type. This is the planted "true positive" the analyzer must still flag.
export interface UnusedConfigShape {
  flag: boolean;
}
