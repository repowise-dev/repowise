package conditionals

func ThreeOps(a, b, c, d bool) int {
	if a && b && c && d {
		return 1
	}
	return 0
}

func SixOps(a, b, c, d, e, f, g bool) int {
	for a && b && c && d && e && f && g {
		return 1
	}
	return 0
}

func TwoOps(a, b, c bool) int {
	if a && b || c {
		return 1
	}
	return 0
}
