def inner_only_alpha(source, enabled):
    before = source + 1
    if enabled:
        inner_a = source + 10
        inner_b = source + 20
        inner_c = source + 30
        inner_d = source + 40
        inner_e = source + 50
        inner_f = source + 60
        inner_g = source + 70
        inner_h = source + 80
        inner_i = source + 90
    after_a = before + 2
    after_b = after_a * 3
    return after_b
