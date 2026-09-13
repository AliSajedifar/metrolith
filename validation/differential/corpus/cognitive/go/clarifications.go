// Go clarification corpus — Amendment 002 (owner-review closure).
//
// Additive: constructs.go is untouched, so its line citations stay valid. Every
// callable here pins a clarification introduced in revision 3 of
// FROZEN_RULE_TABLE.md. Expectations are authored from that table.

package cognitive

// SeqParenSame: parentheses do NOT split a same-operator sequence.
func SeqParenSame(a, b, c bool) int {
	if a && (b && c) {
		return 1
	}
	return 0
}

// SeqParenMixed: changing operator kind starts a new sequence.
func SeqParenMixed(a, b, c bool) int {
	if a && (b || c) {
		return 1
	}
	return 0
}

// SeqThreeRuns: || then && then || is three sequences.
func SeqThreeRuns(a, b, c, d bool) int {
	if a || b && c || d {
		return 1
	}
	return 0
}

// RecursionTwice: two self-call sites, still ONE F-RECURSION.
func RecursionTwice(n int) int {
	if n <= 0 {
		return 0
	}
	return RecursionTwice(n-1) + RecursionTwice(n-2)
}

type receiverHolder struct{ n int }

// RecursiveMethod: self-call through the DECLARED receiver identifier.
func (r receiverHolder) RecursiveMethod(n int) int {
	if n <= 0 {
		return 0
	}
	return r.RecursiveMethod(n - 1)
}

type otherHolder struct{}

func (o otherHolder) RecursiveMethod(n int) int { return n }

// SameNameOtherReceiver: the member name matches, the receiver does not. NOT
// recursion -- the false-positive class Amendment 002 removed.
func (r receiverHolder) SameNameOtherReceiver(n int) int {
	var o otherHolder
	return o.RecursiveMethod(n)
}

// DeferPlainCall: `defer` is zero, and it carries NO literal -- but its
// argument expression is still traversed (default traversal, section 6.2).
func DeferPlainCall(a, b bool) {
	defer consume(a && b)
}

func consume(v bool) {}

// ReturnTraversed: `return` is zero; there is no ternary in Go, so the
// traversal is shown through a boolean sequence inside the returned call.
func ReturnTraversed(a, b bool) bool {
	return identity(a || b)
}

func identity(v bool) bool { return v }
