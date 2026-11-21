package calc

import "testing"

func TestPrimes(t *testing.T) {
	if got := Primes(30); got != 10 {
		t.Fatalf("Primes(30) = %d, want 10", got)
	}
	// Enough work for the profiler to take samples.
	total := 0
	for i := 0; i < 300; i++ {
		total += Primes(60000) + SumSquares(2000000)
	}
	if total == 0 {
		t.Fatal("no work done")
	}
}

func BenchmarkPrimes(b *testing.B) {
	for i := 0; i < b.N; i++ {
		Primes(20000)
	}
}
