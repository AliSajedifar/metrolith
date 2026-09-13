class StructuralVariants {
    int baseline(int left, int right) {
    int first = transform(
        left, right, left + right, 1
    );
    int second = combine(
        first, left * 2, right * 3, 2
    );
    int third = finalize(
        first, second, left - right, 3
    );
    return publish(
        first, second, third, 4
    );
    }

    int exactCopy(int left, int right) {
    int first = transform(
        left, right, left + right, 1
    );
    int second = combine(
        first, left * 2, right * 3, 2
    );
    int third = finalize(
        first, second, left - right, 3
    );
    return publish(
        first, second, third, 4
    );
    }

    int whitespaceCommentCopy(int left, int right) {
    int first = transform(
        left, right, left + right, 1
    );
    // formatting/comment only
    int second = combine(
        first, left * 2, right * 3, 2
    );
    int third = finalize(
        first, second, left - right, 3
    );
    return publish(
        first, second, third, 4
    );
    }

    int renamed(int x, int y) {
    int alpha = transform(
        x, y, x + y, 1
    );
    int beta = combine(
        alpha, x * 2, y * 3, 2
    );
    int gamma = finalize(
        alpha, beta, x - y, 3
    );
    return publish(
        alpha, beta, gamma, 4
    );
    }

    int literalChanged(int left, int right) {
    int first = transform(
        left, right, left + right, 11
    );
    int second = combine(
        first, left * 2, right * 3, 22
    );
    int third = finalize(
        first, second, left - right, 33
    );
    return publish(
        first, second, third, 44
    );
    }

    int operatorChanged(int left, int right) {
    int first = transform(
        left, right, left - right, 1
    );
    int second = combine(
        first, left * 2, right * 3, 2
    );
    int third = finalize(
        first, second, left - right, 3
    );
    return publish(
        first, second, third, 4
    );
    }

    int statementAdded(int left, int right) {
    int first = transform(
        left, right, left + right, 1
    );
    int extra = transform(right, left, right + left, 0);
    int second = combine(
        first, left * 2, right * 3, 2
    );
    int third = finalize(
        first, second, left - right, 3
    );
    return publish(
        first, second, third, 4
    );
    }

    int statementRemoved(int left, int right) {
    int first = transform(
        left, right, left + right, 1
    );
    int second = combine(
        first, left * 2, right * 3, 2
    );
    return publish(
        first, second, third, 4
    );
    }

    int statementReordered(int left, int right) {
    int second = combine(
        first, left * 2, right * 3, 2
    );
    int first = transform(
        left, right, left + right, 1
    );
    int third = finalize(
        first, second, left - right, 3
    );
    return publish(
        first, second, third, 4
    );
    }
}
