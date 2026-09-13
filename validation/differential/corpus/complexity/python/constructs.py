"""Synthetic Complexity Contract 1.0.0 micro-corpus for Python.

Every callable isolates one construct family. Expected per-construct
contributions are in expectations.json, authored from
docs/COMPLEXITY_CONTRACT_V1.md BEFORE any reference adapter existed.

This file is parsed, never imported or executed.
"""


def trivial():
    return 1


def sequential_branches(a, b):
    if a > 0:
        a += 1
    if b > 0:
        b += 1
    return a + b


def elif_chain(v):
    if v < 0:
        return "neg"
    elif v == 0:
        return "zero"
    else:
        return "pos"


def nested_branches(a, b):
    if a > 0:
        if b > 0:
            return a * b
    return 0


def single_loop(items):
    total = 0
    for item in items:
        total += item
    return total


def nested_loops(rows, cols):
    count = 0
    for _ in range(rows):
        for _ in range(cols):
            count += 1
    return count


def while_loop(a):
    while a > 0:
        a -= 1
    return a


def exception_handling(a):
    try:
        a += 1
    except ValueError:
        a -= 1
    except (TypeError, KeyError):
        a = 0
    else:
        a += 2
    finally:
        a += 3
    return a


def ternary(a, b):
    return 1 if a and b else 2


def boolean_operators(a, b, c):
    if a and b:
        return True
    if a or b or c:
        return True
    return a and b or c


def comprehensions(xs, ys):
    listed = [x for x in xs if x > 0 if x < 9]
    mapped = {k: v for k, v in ys}
    generated = (y for y in ys)
    return listed, mapped, generated


def match_statement(v):
    match v:
        case 1:
            return 1
        case int() as n if n > 0 and n < 9:
            return 2
        case _:
            return 3


def with_statement(path):
    with open(path) as handle:
        return handle.read()


def excluded_lambda(xs):
    predicate = lambda value: value > 0 and value < 100
    if not xs:
        return None
    return predicate


def excluded_nested_function(xs):
    def helper(value):
        if value > 0 and value < 9:
            for _ in range(value):
                pass
        return value

    if xs:
        return helper
    return None


def measured_inner_class(flag):
    class Inner:
        def measured_method(self, value):
            if value > 0:
                return value
            return -value

    if flag:
        return Inner
    return None


def parameters(a, /, b, *args, c=1, **kw):
    return a


def comments_and_blanks(a):
    """A docstring counts as code."""
    # a comment does not

    value = a  # trailing comment

    return value


async def async_function(items):
    async for item in items:
        if item:
            return item
    return None


class Holder:
    def method(self, a):
        if a:
            return 1
        return 0

    def __init__(self):
        self.value = 1

    @staticmethod
    def static_method(a, b):
        return a + b
