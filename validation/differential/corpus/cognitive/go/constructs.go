// Go cognitive-complexity micro-corpus (G1-A).
//
// Every callable pins named rules from FROZEN_RULE_TABLE.md. Expected values in
// expectations.json are authored FROM THAT TABLE and from nothing else: no
// ArchLens cognitive implementation exists, and the reference tools were run
// only after the expectations were frozen.

package cognitive

// Trivial: no construct at all. The zero case.
func Trivial(a int) int {
	return a
}

// PlainIf: S-IF at nesting 0.
func PlainIf(a bool) int {
	if a {
		return 1
	}
	return 0
}

// IfElseIfElse: S-IF + F-ELSEIF + F-ELSE. The chain measures flat.
func IfElseIfElse(a int) int {
	if a == 1 {
		return 1
	} else if a == 2 {
		return 2
	} else {
		return 3
	}
}

// NestedIf: the nesting increment made visible.
func NestedIf(a, b bool) int {
	if a {
		if b {
			return 1
		}
	}
	return 0
}

// Loops: S-LOOP nested inside S-LOOP.
func Loops(xs []int) int {
	total := 0
	for _, x := range xs {
		for i := 0; i < x; i++ {
			total += i
		}
	}
	return total
}

// SwitchWhole: the whole switch scores once; arms score nothing.
func SwitchWhole(a int) int {
	switch a {
	case 1:
		return 1
	case 2:
		return 2
	default:
		return 3
	}
}

// TypeSwitch: a type switch is an S-SWITCH like any other.
func TypeSwitch(v interface{}) int {
	switch v.(type) {
	case int:
		return 1
	case string:
		return 2
	}
	return 0
}

// SelectStmt: S-SELECT, the Go-only structural construct.
func SelectStmt(ch chan int) int {
	select {
	case <-ch:
		return 1
	default:
		return 0
	}
}

// BoolSameOperator: one maximal && sequence, however many operands.
func BoolSameOperator(a, b, c bool) int {
	if a && b && c {
		return 1
	}
	return 0
}

// BoolMixedOperators: two maximal sequences, one per operator.
func BoolMixedOperators(a, b, c bool) int {
	if a && b || c {
		return 1
	}
	return 0
}

// BitwiseNotCounted: Z-BITWISE. `&` is arithmetic, not short-circuit.
func BitwiseNotCounted(a, b int) int {
	if a&b != 0 {
		return 1
	}
	return 0
}

// LabelledFlow: F-LABELJUMP twice, under two loops and an if.
func LabelledFlow(xs []int) int {
outer:
	for _, x := range xs {
		for range xs {
			if x > 0 {
				break outer
			}
			continue outer
		}
	}
	return 0
}

// UnlabelledJump: Z-JUMP. A bare break scores nothing.
func UnlabelledJump(xs []int) int {
	for range xs {
		break
	}
	return 0
}

// GotoJump: F-LABELJUMP for Go's goto, which no other corpus language has.
func GotoJump(a bool) int {
	if a {
		goto done
	}
	return 0
done:
	return 1
}

// Recursive: F-RECURSION by lexical name match.
func Recursive(n int) int {
	if n <= 0 {
		return 0
	}
	return Recursive(n - 1)
}

// FuncLiteralExcluded: B-NESTED. The literal's `if` belongs to no record here.
func FuncLiteralExcluded(xs []int) func() int {
	return func() int {
		if len(xs) > 0 {
			return 1
		}
		return 0
	}
}

// DeferredLiteral: Z-DEFER plus B-NESTED. Neither contributes.
func DeferredLiteral(a bool) {
	defer func() {
		if a {
			_ = a
		}
	}()
}
