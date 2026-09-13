# G2-B minimal reproductions for the Python "no construct detected" residue.
def paren_split_or(a, b, c):
    if a or not (b or c):        # one `or` run: rule table 3.2/3.3
        return 1
    return 0


def elif_chain(x):
    if x == 1:
        return 1
    elif x == 2:
        return 2
    return 0


def elif_with_nested_if(xs, commit):
    for f in xs:
        if f:
            if f.pk is None:
                return 1
        elif f.changed():
            if not commit:
                return 2
    return 0


def bool_run_in_loop_condition(items, a, b):
    for i in items:
        if a and b:
            return i
    return 0
