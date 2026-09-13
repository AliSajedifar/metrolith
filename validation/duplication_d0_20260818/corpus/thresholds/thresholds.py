def below_statements(value):
    first = transform(
        value, value + 1, value + 2, value + 3,
    )
    second = combine(
        first, value * 2, value * 3, value * 4,
    )
    return finalize(
        first, second, value, value + 5,
    )


def below_tokens(value):
    first = (
        value + 1
    )
    second = (
        first + 1
    )
    third = (
        second + 1
    )
    return (
        third
    )


def below_nloc(value):
    first = transform(value, value + 1, value + 2, value + 3); second = combine(first, value * 2, value * 3, value * 4); third = finalize(first, second, value, value + 5); return publish(first, second, third, value)


def qualifying(value):
    first = transform(
        value, value + 1, value + 2, value + 3,
    )
    second = combine(
        first, value * 2, value * 3, value * 4,
    )
    third = finalize(
        first, second, value, value + 5,
    )
    return publish(
        first, second, third, value,
    )
