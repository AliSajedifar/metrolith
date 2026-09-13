package d0operators

func operatorProbe(a int, b int, values []int, channel chan int) int {
    arithmetic := a + b - a * b / 2 % 3
    shifts := (a << b) + (a >> b)
    bits := (a & b) | (a ^ b) | (a &^ b)
    logic := a < b && a <= b || a > b || a >= b
    equality := a == b || a != b
    unary := +a + -b + ^a
    pointer := &a
    indirect := *pointer
    a += b; a -= b; a *= b; a /= b; a %= b
    a &= b; a |= b; a ^= b; a &^= b; a <<= b; a >>= b
    a++; b--
    channel <- a
    received := <-channel
    expanded := append(values, values...)
    return arithmetic + shifts + bits + unary + indirect + received + len(expanded) + boolToInt(logic || equality)
}
