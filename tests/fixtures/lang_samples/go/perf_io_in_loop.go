// Fixture for the Go performance dialect (io_in_loop / string_concat /
// defer_in_loop / regex_compile_in_loop). Every POSITIVE is a genuine
// per-iteration cost; every NEGATIVE is a shape the dialect must NOT flag.
// Hand-counted expectations live in test_perf_go.py. NOTE: this file imports
// database/sql, so file-level db evidence is present; ambiguous-verb gating
// (GORM Find without a db import) is tested inline instead.
package sample

import (
	"database/sql"
	"net/http"
	"os"
	"regexp"
)

// --- POSITIVES -------------------------------------------------------------

func dbQueryInRange(db *sql.DB, ids []int) {
	for _, id := range ids {
		db.Query("SELECT 1", id) // POSITIVE: database/sql round-trip per item
	}
}

func httpGetInRange(urls []string) {
	for _, u := range urls {
		http.Get(u) // POSITIVE: network per item
	}
}

func osOpenInRange(paths []string) {
	for _, p := range paths {
		os.Open(p) // POSITIVE: filesystem per item
	}
}

func deferInLoop(paths []string) {
	for _, p := range paths {
		f, _ := os.Open(p) // POSITIVE: filesystem (also the defer below)
		defer f.Close()    // POSITIVE: defer in loop leaks the handle
	}
}

func regexpCompileInRange(ids []string) {
	for _, id := range ids {
		regexp.MustCompile(`^[a-z]+$`) // POSITIVE: a static literal pattern, hoistable
		regexp.MustCompile(id)         // NEGATIVE: a dynamic per-iteration arg cannot be hoisted
	}
}

func stringConcatInRange(rows []string) string {
	s := ""
	for range rows {
		s += "row" // POSITIVE: quadratic string build (literal RHS)
	}
	return s
}

// --- NEGATIVES -------------------------------------------------------------

func dbQueryConstantLoop(db *sql.DB) {
	for i := 0; i < 10; i++ { // NEGATIVE: constant-bound three-clause loop
		db.Query("SELECT 1")
	}
}

func pureCallInRange(items []int) {
	total := 0
	for _, x := range items {
		total += x // NEGATIVE: numeric +=, no I/O
	}
	_ = total
}

func queryOutsideLoop(db *sql.DB) {
	db.Query("SELECT 1") // NEGATIVE: runs once, not in a loop
}
