// Package constructs is a synthetic Complexity Contract 1.0.0 micro-corpus.
//
// Every callable below isolates one construct family. Expected per-construct
// contributions are in expectations.json, authored from
// docs/COMPLEXITY_CONTRACT_V1.md BEFORE any implementation existed.
//
// This file is parsed, never compiled or executed.
package constructs

// Trivial has no decision and no boolean operator.
func Trivial() int {
	return 1
}

// SequentialBranches has two independent decisions at the same depth.
func SequentialBranches(a int, b int) int {
	if a > 0 {
		a++
	}
	if b > 0 {
		b++
	}
	return a + b
}

// ElseIfChain reads flat and must measure flat: an `else if` alternative is a
// nested if_statement, not a block, so it adds no nesting.
func ElseIfChain(v int) string {
	if v < 0 {
		return "neg"
	} else if v == 0 {
		return "zero"
	} else {
		return "pos"
	}
}

// NestedBranches has the same decision count as SequentialBranches and twice
// the nesting. Preserving that difference is the point of the bundle.
func NestedBranches(a int, b int) int {
	if a > 0 {
		if b > 0 {
			return a * b
		}
	}
	return 0
}

// SingleLoop contributes one decision.
func SingleLoop(items []int) int {
	total := 0
	for _, item := range items {
		total += item
	}
	return total
}

// NestedLoops contributes two decisions and depth 2.
func NestedLoops(rows int, cols int) int {
	count := 0
	for i := 0; i < rows; i++ {
		for j := 0; j < cols; j++ {
			count++
		}
	}
	return count
}

// SwitchWithDefault pins that `default` contributes nothing.
func SwitchWithDefault(code int) string {
	switch code {
	case 1:
		return "one"
	case 2:
		return "two"
	default:
		return "other"
	}
}

// SwitchWithoutDefault pins that a multi-value case is ONE arm.
func SwitchWithoutDefault(code int) string {
	switch code {
	case 1:
		return "one"
	case 2, 3:
		return "few"
	}
	return "none"
}

// TypeSwitch behaves as an expression switch for this contract.
func TypeSwitch(value interface{}) string {
	switch value.(type) {
	case int:
		return "int"
	case string:
		return "string"
	default:
		return "other"
	}
}

// SelectWithDefault pins that a select `default` contributes nothing.
func SelectWithDefault(ch chan int) int {
	select {
	case v := <-ch:
		return v
	default:
		return -1
	}
}

// BooleanOperators separates the two condition metrics: boolean_operator_count
// counts every short-circuit token anywhere, while max_condition_operator_count
// inspects decision expressions only. The operators in the return statement
// raise the first and not the second.
func BooleanOperators(a bool, b bool, c bool) bool {
	if a && b {
		return true
	}
	if a || b || c {
		return true
	}
	return a && b || c
}

// ExcludedFuncLiteral is the keystone case for the traversal rule and the NLOC
// asymmetry together: the literal's `&&`, its `if` and its `for` belong to no
// record, yet its lines are inside this callable's span.
func ExcludedFuncLiteral(items []int) func(int) int {
	if len(items) == 0 {
		return nil
	}
	return func(x int) int {
		if x > 0 && x < 100 {
			for i := 0; i < x; i++ {
				x--
			}
		}
		return x
	}
}

// GoroutineLiteral has all of its control flow inside a boundary, so it is
// exactly 1.
func GoroutineLiteral(done chan bool) {
	go func() {
		if true {
			done <- true
		}
	}()
}

// LabelledFlow pins that labelled break/continue contribute nothing.
func LabelledFlow(rows [][]int) int {
outer:
	for _, row := range rows {
		for _, value := range row {
			if value < 0 {
				break outer
			}
			if value == 0 {
				continue outer
			}
		}
	}
	return 0
}

// Server exists so the receiver-method family has an owner.
type Server struct {
	name string
}

// ValueReceiver has a value receiver, which is excluded from the parameter
// count.
func (s Server) ValueReceiver() string {
	return s.name
}

// PointerReceiver has a pointer receiver, also excluded, and two declared
// parameters.
func (s *Server) PointerReceiver(prefix string, suffix string) string {
	if prefix == "" {
		return s.name
	}
	return prefix + s.name + suffix
}

// GroupedParameters declares three parameters across two declarations.
func GroupedParameters(a, b int, c string) int {
	return a + b + len(c)
}

// UnnamedParameters declares two parameters and no names.
func UnnamedParameters(int, string) int {
	return 0
}

// VariadicParameters counts the variadic declaration as one.
func VariadicParameters(prefix string, rest ...int) int {
	total := 0
	for _, value := range rest {
		total += value
	}
	return total
}

// CommentsAndBlanks pins that function NLOC uses the repository line
// classification: blank and comment-only lines are excluded, and a line mixing
// code with a trailing comment counts once.
func CommentsAndBlanks(a int) int {
	// leading comment

	value := a // trailing comment

	/* block
	   comment */
	return value
}

// RawStringContent pins that a comment marker inside a raw string literal is
// string content, not a comment.
func RawStringContent() string {
	return `line one
// not a comment
line three`
}

// AssemblyStub has no body and is therefore OUTSIDE the canonical population.
// It must emit no record at all.
func AssemblyStub(a int) int
