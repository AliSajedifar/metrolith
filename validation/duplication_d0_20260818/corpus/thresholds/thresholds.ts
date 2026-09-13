function belowStatements(value: number): number {
    const first = transform(
        value, value + 1, value + 2, value + 3,
    );
    const second = combine(
        first, value * 2, value * 3, value * 4,
    );
    return finalize(
        first, second, value, value + 5,
    );
}
function belowTokens(value: number): number {
    const first =
        value + 1;
    const second =
        first + 1;
    const third =
        second + 1;
    return (
        third
    );
}
function belowNloc(value: number): number { const first = transform(value, value + 1, value + 2, value + 3); const second = combine(first, value * 2, value * 3, value * 4); const third = finalize(first, second, value, value + 5); return publish(first, second, third, value); }
function qualifying(value: number): number {
    const first = transform(
        value, value + 1, value + 2, value + 3,
    );
    const second = combine(
        first, value * 2, value * 3, value * 4,
    );
    const third = finalize(
        first, second, value, value + 5,
    );
    return publish(
        first, second, third, value,
    );
}
