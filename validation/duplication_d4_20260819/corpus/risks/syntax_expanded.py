def syntax_expanded(value):
    amount = value
    amount = amount + convert(value, 101, 202, 303)
    amount = amount + convert(value, 404, 505, 606)
    amount = amount + convert(value, 707, 808, 909)
    amount = amount + convert(value, 1001, 1101, 1201)
    amount = amount + convert(value, 1301, 1401, 1501)
    amount = amount + convert(value, 1601, 1701, 1801)
    amount = amount + convert(value, 1901, 2001, 2101)
    return amount
