def sibling_alpha(source, first_flag, second_flag):
    seed = source + 1
    if first_flag:
        one_a = source + 10
        one_b = source + 20
        one_c = source + 30
        one_d = source + 40
        one_e = source + 50
        one_f = source + 60
        one_g = source + 70
        one_h = source + 80
        one_i = source + 90
    if second_flag:
        two_a = transform(source, 10)
        two_b = transform(source, 20)
        two_c = transform(source, 30)
        two_d = transform(source, 40)
        two_e = transform(source, 50)
        two_f = transform(source, 60)
        two_g = transform(source, 70)
        two_h = transform(source, 80)
        two_i = transform(source, 90)
    tail = seed + 2
    return tail
