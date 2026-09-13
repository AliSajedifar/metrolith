class Thresholds {
    int belowStatements(int value) {
        int first = transform(
            value, value + 1,
            value + 2, value + 3);
        int second = combine(
            first, value * 2,
            value * 3, value * 4);
        return finalize(
            first, second,
            value, value + 5);
    }
    int belowTokens(int value) {
        int first =
            value + 1;
        int second =
            first + 1;
        int third =
            second + 1;
        return
            third;
    }
    int belowNloc(int value) { int first = transform(value, value + 1, value + 2, value + 3); int second = combine(first, value * 2, value * 3, value * 4); int third = finalize(first, second, value, value + 5); return publish(first, second, third, value); }
    int qualifying(int value) {
        int first = transform(
            value, value + 1, value + 2, value + 3);
        int second = combine(
            first, value * 2, value * 3, value * 4);
        int third = finalize(
            first, second, value, value + 5);
        return publish(
            first, second, third, value);
    }
}
