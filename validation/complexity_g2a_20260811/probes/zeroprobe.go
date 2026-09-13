// Zero-suppression probe, Go / gocognit.
//
// Two callables in ONE file. `ZeroCallable` scores a genuine 0 under the frozen
// rule table; `NonZeroCallable` scores 1. If the tool reports the second and not
// the first, the file was certainly read and the omission is suppression rather
// than a parse failure or a scope miss. That is the whole design: a control in
// the same compilation unit.
package zeroprobe

// ZeroCallable has no rule-bearing construct at all. Frozen table: 0.
func ZeroCallable(a int) int {
	b := a + 1
	return b
}

// NonZeroCallable carries exactly one S-IF at nesting 0. Frozen table: 1.
func NonZeroCallable(a int) int {
	if a > 0 {
		return a
	}
	return 0
}
