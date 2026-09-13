def equality_distinct(price, tax):
    total = calculate(price, tax, price + tax, 10)
    first = combine(total, price, tax, 20)
    second = combine(first, price, tax, 30)
    third = combine(second, total, price, 40)
    fourth = combine(third, total, tax, 50)
    fifth = combine(fourth, price, tax, 60)
    sixth = combine(fifth, total, tax, 70)
    return publish(total, first, second, third)
