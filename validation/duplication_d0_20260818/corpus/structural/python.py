def baseline(left, right):
    first = transform(
        left, right, left + right, 1,
    )
    second = combine(
        first, left * 2, right * 3, 2,
    )
    third = finalize(
        first, second, left - right, 3,
    )
    return publish(
        first, second, third, 4,
    )

def exact_copy(left, right):
    first = transform(
        left, right, left + right, 1,
    )
    second = combine(
        first, left * 2, right * 3, 2,
    )
    third = finalize(
        first, second, left - right, 3,
    )
    return publish(
        first, second, third, 4,
    )

def whitespace_comment_copy(left, right):
    first=transform( left, right, left + right, 1 )  # formatting/comment
    second = combine(
        first,left*2,right*3,2,
    )
    third=finalize(first, second, left-right, 3)
    return publish(first, second, third, 4)

def renamed(left, right):
    alpha = transform(
        x, y, x + y, 1,
    )
    beta = combine(
        alpha, x * 2, y * 3, 2,
    )
    gamma = finalize(
        alpha, beta, x - y, 3,
    )
    return publish(
        alpha, beta, gamma, 4,
    )

def literal_changed(left, right):
    first = transform(
        left, right, left + right, 11,
    )
    second = combine(
        first, left * 2, right * 3, 22,
    )
    third = finalize(
        first, second, left - right, 33,
    )
    return publish(
        first, second, third, 44,
    )

def operator_changed(left, right):
    first = transform(
        left, right, left - right, 1,
    )
    second = combine(
        first, left * 2, right * 3, 2,
    )
    third = finalize(
        first, second, left - right, 3,
    )
    return publish(
        first, second, third, 4,
    )

def statement_added(left, right):
    first = transform(left, right, left + right, 1)
    extra = transform(right, left, right + left, 0)
    second = combine(first, left * 2, right * 3, 2)
    third = finalize(first, second, left - right, 3)
    return publish(first, second, third, 4)

def statement_removed(left, right):
    first = transform(left, right, left + right, 1)
    second = combine(first, left * 2, right * 3, 2)
    return publish(first, second, left - right, 4)

def statement_reordered(left, right):
    second = combine(left, left * 2, right * 3, 2)
    first = transform(left, right, left + right, 1)
    third = finalize(first, second, left - right, 3)
    return publish(first, second, third, 4)

def nested_outer_a(left, right):
    def inner(value):
        first = transform(value, left, right, 1)
        second = combine(first, value, left, 2)
        third = finalize(first, second, right, 3)
        return publish(first, second, third, 4)
    first = inner(left)
    second = inner(right)
    third = combine(first, second, left, right)
    return publish(first, second, third, 4)

def nested_outer_b(left, right):
    def inner(value):
        first = transform(value, left, right, 1)
        second = combine(first, value, left, 2)
        third = finalize(first, second, right, 3)
        return publish(first, second, third, 4)
    first = inner(left)
    second = inner(right)
    third = combine(first, second, left, right)
    return publish(first, second, third, 4)
