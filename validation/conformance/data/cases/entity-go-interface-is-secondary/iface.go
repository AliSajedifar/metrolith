package api

type Reader interface {
	Read() int
}

type Buffer struct {
	size int
}
