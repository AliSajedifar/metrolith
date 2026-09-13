package structural

func baseline(left int, right int) int {
    first := transform(
        left, right, left + right, 1,
    )
    second := combine(
        first, left * 2, right * 3, 2,
    )
    third := finalize(
        first, second, left - right, 3,
    )
    return publish(
        first, second, third, 4,
    )
}

func exactCopy(left int, right int) int {
    first := transform(
        left, right, left + right, 1,
    )
    second := combine(
        first, left * 2, right * 3, 2,
    )
    third := finalize(
        first, second, left - right, 3,
    )
    return publish(
        first, second, third, 4,
    )
}

func whitespaceCommentCopy(left int, right int) int {
    first := transform(
        left, right, left + right, 1,
    )
    // formatting/comment only
    second := combine(
        first, left * 2, right * 3, 2,
    )
    third := finalize(
        first, second, left - right, 3,
    )
    return publish(
        first, second, third, 4,
    )
}

func renamed(x int, y int) int {
    alpha := transform(
        x, y, x + y, 1,
    )
    beta := combine(
        alpha, x * 2, y * 3, 2,
    )
    gamma := finalize(
        alpha, beta, x - y, 3,
    )
    return publish(
        alpha, beta, gamma, 4,
    )
}

func literalChanged(left int, right int) int {
    first := transform(
        left, right, left + right, 11,
    )
    second := combine(
        first, left * 2, right * 3, 22,
    )
    third := finalize(
        first, second, left - right, 33,
    )
    return publish(
        first, second, third, 44,
    )
}

func operatorChanged(left int, right int) int {
    first := transform(
        left, right, left - right, 1,
    )
    second := combine(
        first, left * 2, right * 3, 2,
    )
    third := finalize(
        first, second, left - right, 3,
    )
    return publish(
        first, second, third, 4,
    )
}

func statementAdded(left int, right int) int {
    first := transform(
        left, right, left + right, 1,
    )
    extra := transform(right, left, right + left, 0)
    second := combine(
        first, left * 2, right * 3, 2,
    )
    third := finalize(
        first, second, left - right, 3,
    )
    return publish(
        first, second, third, 4,
    )
}

func statementRemoved(left int, right int) int {
    first := transform(
        left, right, left + right, 1,
    )
    second := combine(
        first, left * 2, right * 3, 2,
    )
    return publish(
        first, second, third, 4,
    )
}

func statementReordered(left int, right int) int {
    second := combine(
        first, left * 2, right * 3, 2,
    )
    first := transform(
        left, right, left + right, 1,
    )
    third := finalize(
        first, second, left - right, 3,
    )
    return publish(
        first, second, third, 4,
    )
}
