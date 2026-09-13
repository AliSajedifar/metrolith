"""Python cognitive-complexity micro-corpus (G1-A).

Every callable pins named rules from FROZEN_RULE_TABLE.md. Expected values in
expectations.json are authored FROM THAT TABLE and from nothing else: no
ArchLens cognitive implementation exists, and the reference tool was run only
after the expectations were frozen.
"""


def trivial(a):
    return a


def plain_if(a):
    if a:
        return 1
    return 0


def if_elif_else(a):
    if a == 1:
        return 1
    elif a == 2:
        return 2
    else:
        return 3


def nested_if(a, b):
    if a:
        if b:
            return 1
    return 0


def loops(xs):
    total = 0
    for x in xs:
        while x > 0:
            x -= 1
            total += 1
    return total


def loop_else(xs):
    for x in xs:
        if x:
            break
    else:
        return 0
    return 1


def exception_handling(a):
    try:
        return int(a)
    except ValueError:
        return 0
    except TypeError:
        return -1
    else:
        return 1
    finally:
        if a:
            pass


def ternary(a):
    return 1 if a else 0


def bool_same_operator(a, b, c):
    if a and b and c:
        return 1
    return 0


def bool_mixed_operators(a, b, c):
    if a and b or c:
        return 1
    return 0


def bitwise_not_counted(a, b):
    if a & b:
        return 1
    return 0


def comprehension(xs):
    return [x for x in xs if x if x > 1]


def match_statement(a):
    match a:
        case 1:
            return 1
        case int() if a > 5:
            return 2
        case _:
            return 0


def with_statement(path):
    with open(path) as handle:
        if handle:
            return 1
    return 0


def assert_and_raise(a):
    assert a
    raise ValueError(a)


def unlabelled_jump(xs):
    for x in xs:
        if x:
            continue
        break
    return 0


def recursive(n):
    if n <= 0:
        return 0
    return recursive(n - 1)


def nested_def_excluded(a):
    def helper(b):
        if b:
            return 1
        return 0

    return helper


def lambda_excluded(xs):
    return sorted(xs, key=lambda t: 1 if t else 0)
