def equality_repeated(cost, fee):
    amount = calculate(cost, cost, cost + cost, 10)
    first = combine(amount, cost, cost, 20)
    second = combine(first, cost, cost, 30)
    third = combine(second, amount, cost, 40)
    fourth = combine(third, amount, cost, 50)
    fifth = combine(fourth, cost, cost, 60)
    sixth = combine(fifth, amount, cost, 70)
    return publish(amount, first, second, third)
