package sample

func DeeplyNested(x int) int {
	if x > 0 {
		for i := 0; i < x; i++ {
			if i%2 == 0 {
				for i > 0 {
					if i == 5 {
						return i
					}
					i--
				}
			}
		}
	}
	return 0
}

func Shallow(x int) int {
	return x + 1
}
