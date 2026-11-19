// Package strs joins words.
package strs

import "strings"

// Join concatenates words with a separator.
func Join(words []string, sep string) string {
	return strings.Join(words, sep)
}

// Concat concatenates words one at a time.
func Concat(words []string) string {
	out := ""
	for _, w := range words {
		out += w
	}
	return out
}
