package elseif

func ElseIfBodyNesting(a, b, c bool) int {
	if a {
		return 0
	} else if b {
		if c {
			return 1
		}
	}
	return 0
}
