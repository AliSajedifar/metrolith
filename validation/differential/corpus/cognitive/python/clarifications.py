"""Python clarification corpus — Amendment 002 (owner-review closure).

Additive: constructs.py is untouched, so its line citations stay valid. Every
callable here pins a clarification introduced in revision 3 of
FROZEN_RULE_TABLE.md. Expectations are authored from that table.
"""


def seq_paren_same(a, b, c):
    if a and (b and c):
        return 1
    return 0


def seq_paren_mixed(a, b, c):
    if a and (b or c):
        return 1
    return 0


def seq_three_runs(a, b, c, d):
    if a or b and c or d:
        return 1
    return 0


def recursion_twice(n):
    if n <= 0:
        return 0
    return recursion_twice(n - 1) + recursion_twice(n - 2)


def comp_single(xs):
    return [x for x in xs]


def comp_two_generators(xs, ys):
    return [x for x in xs for y in ys]


def comp_two_generators_filtered(xs, ys, p):
    return [x for x in xs for y in ys if p(y)]


def comp_boolean_condition(xs, a, b):
    return [x for x in xs if a and b]


def comp_nested(xs, ys):
    return [[y for y in ys] for x in xs]


def return_traversed(a, b, c):
    return (b if a else c)


class Holder:
    def recursive_method(self, n):
        if n <= 0:
            return 0
        return self.recursive_method(n - 1)

    def same_name_other_receiver(self, other, n):
        return other.recursive_method(n)


def discovery_def_class_method(flag):
    class Inner:
        def measured(self, a):
            if a:
                return 1
            return 0

    return Inner if flag else None
