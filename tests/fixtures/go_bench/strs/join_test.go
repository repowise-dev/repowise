package strs

import (
	"fmt"
	"testing"
)

var words = []string{"a", "bb", "ccc", "dddd", "eeeee"}

func BenchmarkJoin(b *testing.B) {
	for i := 0; i < b.N; i++ {
		Join(words, ",")
	}
}

func BenchmarkConcat(b *testing.B) {
	for _, n := range []int{2, 5} {
		b.Run(fmt.Sprintf("n=%d", n), func(b *testing.B) {
			for i := 0; i < b.N; i++ {
				Concat(words[:n])
			}
		})
	}
}
