def syntax_augmented(source):
    total = source
    total += transform(source, 10, 20, 30)
    total += transform(source, 40, 50, 60)
    total += transform(source, 70, 80, 90)
    total += transform(source, 100, 110, 120)
    total += transform(source, 130, 140, 150)
    total += transform(source, 160, 170, 180)
    total += transform(source, 190, 200, 210)
    return total
