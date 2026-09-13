# Isolating the elif-body nesting question. One construct per function.
def if_body_nesting(a, b):
    if a:
        if b:            # rule table: S-IF at nesting 1 -> 2
            return 1
    return 0


def elif_body_nesting(a, b, c):
    if a:
        return 0
    elif b:
        if c:            # rule table 2.2: F-ELSEIF raises nesting for its OWN body
            return 1
    return 0


def else_body_nesting(a, c):
    if a:
        return 0
    else:
        if c:            # bare else, same question
            return 1
    return 0
