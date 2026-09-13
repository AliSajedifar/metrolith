"""Zero-suppression probe, Python / cognitive_complexity 1.3.0.

Same design as the other four probes: a genuine zero and a control scoring 1 in
one module. The Python reference is an API returning an ``int`` rather than a
reporting tool with a threshold, so this probe is expected to OBSERVE the zero —
and that expectation is exactly what has to be measured rather than assumed.
"""


def zero_callable(a):
    """No rule-bearing construct. Frozen table: 0."""
    b = a + 1
    return b


def non_zero_callable(a):
    """Exactly one S-IF at nesting 0. Frozen table: 1."""
    if a > 0:
        return a
    return 0
