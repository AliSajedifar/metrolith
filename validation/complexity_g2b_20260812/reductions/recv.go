package recv

type holder struct{}
type other struct{}

func (o other) SameName(n int) int { return n }

// Bare self-name call. Expected 1.
func BareSelf(n int) int { return BareSelf(n) }

// Receiver-qualified self-call. Expected 1.
func (h holder) RecvSelf(n int) int { return h.RecvSelf(n) }

// Same member NAME, another receiver. Expected 0.
func (h holder) SameName(n int) int {
	var o other
	return o.SameName(n)
}
