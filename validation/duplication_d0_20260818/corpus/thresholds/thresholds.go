package thresholds

func belowStatements(value int) int {
    first := transform(
        value, value + 1, value + 2, value + 3,
    )
    second := combine(
        first, value * 2, value * 3, value * 4,
    )
    return finalize(
        first, second, value, value + 5,
    )
}
func belowTokens(value int) int {
    first :=
        value + 1
    second :=
        first + 1
    third :=
        second + 1
    return (
        third
    )
}
func belowNloc(value int) int { first := transform(value, value + 1, value + 2, value + 3); second := combine(first, value * 2, value * 3, value * 4); third := finalize(first, second, value, value + 5); return publish(first, second, third, value) }
func qualifying(value int) int {
    first := transform(
        value, value + 1, value + 2, value + 3,
    )
    second := combine(
        first, value * 2, value * 3, value * 4,
    )
    third := finalize(
        first, second, value, value + 5,
    )
    return publish(
        first, second, third, value,
    )
}
