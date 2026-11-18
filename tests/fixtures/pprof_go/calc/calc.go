// Package calc is a CPU-bound fixture for reading Go pprof profiles.
package calc

// Primes counts the primes below n by trial division.
func Primes(n int) int {
	count := 0
	for i := 2; i < n; i++ {
		if isPrime(i) {
			count++
		}
	}
	return count
}

// isPrime is small enough for the compiler to inline into Primes.
func isPrime(n int) bool {
	for d := 2; d*d <= n; d++ {
		if n%d == 0 {
			return false
		}
	}
	return true
}

// SumSquares adds the squares of 0..n-1, with an inlined helper per term.
func SumSquares(n int) int {
	total := 0
	for i := 0; i < n; i++ {
		total += square(i) % 7
	}
	return total
}

func square(x int) int { return x * x }
