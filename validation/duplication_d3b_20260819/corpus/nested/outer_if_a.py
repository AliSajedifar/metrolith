def outer_alpha(source, enabled):
    seed = source + 1
    first = seed * 2
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
    tail_a = first + 3
    tail_b = tail_a * 4
    tail_c = tail_b - 5
    return tail_c
