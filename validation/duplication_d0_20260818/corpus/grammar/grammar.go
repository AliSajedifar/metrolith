package d0

import "fmt"

type TypeName struct {
    Field int
}

func (RoleName *TypeName) Method(parameter int, rest ...string) int {
    var nilValue any = nil
    truth := true
    integer := 0x2A
    floating := 3.5e2
    imaginary := 4i
    character := 'λ'
    interpreted := "value\n"
    raw := `raw string`
    local := parameter

    nested := func(shadow int) int {
        RoleName := shadow
        first := RoleName + 1
        second := first * 2
        return second / 3
    }

    if truth && integer > 0 {
        local += nested(integer)
        RoleName.Field = local
        interpreted += fmt.Sprint(local)
        truth = !truth
    } else {
        local -= 1
        RoleName.Field = 0
        interpreted = raw
        truth = false
    }

outer:
    for index := 0; index < local; index++ {
        integer += index
        floating += float64(index)
        truth = truth || index > 1
        if index == 2 { continue outer }
    }

    for index, item := range rest {
        local += index
        integer += len(item)
        truth = truth || item == ""
        interpreted += item
    }

    {
        local += 1
        integer ^= local
        truth = truth && local != 0
        interpreted += "."
    }

    switch value := any(local).(type) {
    case int:
        local = value
        integer += value
        truth = true
        interpreted += "int"
    default:
        local = 0
        integer = 0
        truth = false
        interpreted = "default"
    }

    switch integer {
    case 1, 2:
        local += integer
        integer += 1
        truth = true
        interpreted += "small"
    default:
        local = 0
        integer = 0
        truth = false
        interpreted = "other"
    }

    select {
    case value := <-make(chan int):
        local = value
        integer += value
        truth = true
        interpreted += "received"
    default:
        local += 1
        integer += 1
        truth = false
        interpreted += "default"
    }

    defer fmt.Println(nilValue, floating, imaginary, character, raw, rest)
    go fmt.Println(local)
    return local
}

func ModuleFunction(value int) int {
    first := value + 1
    second := first * 2
    third := second / 3
    return third
}
