function baseline(left, right) {
    const first = transform(
        left, right, left + right, 1,
    );
    const second = combine(
        first, left * 2, right * 3, 2,
    );
    const third = finalize(
        first, second, left - right, 3,
    );
    return publish(
        first, second, third, 4,
    );
}

function exactCopy(left, right) {
    const first = transform(
        left, right, left + right, 1,
    );
    const second = combine(
        first, left * 2, right * 3, 2,
    );
    const third = finalize(
        first, second, left - right, 3,
    );
    return publish(
        first, second, third, 4,
    );
}

function whitespaceCommentCopy(left, right) {
    const first = transform(
        left, right, left + right, 1,
    );
    // formatting/comment only
    const second = combine(
        first, left * 2, right * 3, 2,
    );
    const third = finalize(
        first, second, left - right, 3,
    );
    return publish(
        first, second, third, 4,
    );
}

function renamed(x, y) {
    const alpha = transform(
        x, y, x + y, 1,
    );
    const beta = combine(
        alpha, x * 2, y * 3, 2,
    );
    const gamma = finalize(
        alpha, beta, x - y, 3,
    );
    return publish(
        alpha, beta, gamma, 4,
    );
}

function literalChanged(left, right) {
    const first = transform(
        left, right, left + right, 11,
    );
    const second = combine(
        first, left * 2, right * 3, 22,
    );
    const third = finalize(
        first, second, left - right, 33,
    );
    return publish(
        first, second, third, 44,
    );
}

function operatorChanged(left, right) {
    const first = transform(
        left, right, left - right, 1,
    );
    const second = combine(
        first, left * 2, right * 3, 2,
    );
    const third = finalize(
        first, second, left - right, 3,
    );
    return publish(
        first, second, third, 4,
    );
}

function statementAdded(left, right) {
    const first = transform(
        left, right, left + right, 1,
    );
    const extra = transform(right, left, right + left, 0);
    const second = combine(
        first, left * 2, right * 3, 2,
    );
    const third = finalize(
        first, second, left - right, 3,
    );
    return publish(
        first, second, third, 4,
    );
}

function statementRemoved(left, right) {
    const first = transform(
        left, right, left + right, 1,
    );
    const second = combine(
        first, left * 2, right * 3, 2,
    );
    return publish(
        first, second, third, 4,
    );
}

function statementReordered(left, right) {
    const second = combine(
        first, left * 2, right * 3, 2,
    );
    const first = transform(
        left, right, left + right, 1,
    );
    const third = finalize(
        first, second, left - right, 3,
    );
    return publish(
        first, second, third, 4,
    );
}
