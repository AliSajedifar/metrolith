package shape

type Point struct {
	X int
}

func (p Point) Norm() int {
	return p.X
}

func Helper() int {
	return 0
}
