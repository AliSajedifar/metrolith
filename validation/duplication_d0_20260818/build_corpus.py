"""Materialize the reviewed D0-only source corpus, including byte edge cases.

This is evidence generation, not a candidate extractor or duplication engine.
It writes only below this validation directory and is deterministic.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "corpus"


GRAMMAR_SOURCES = {
    "grammar/operators.py": '''async def operator_probe(a, b, c, awaitable, *args, **kwargs):
    arithmetic = (a + b, a - b, a * b, a @ b, a / b, a // b, a % b, a ** b)
    bitwise = (a << b, a >> b, a | b, a ^ b, a & b)
    boolean = (a and b, a or b, not a)
    comparisons = (a == b, a != b, a < b, a <= b, a > b, a >= b, a is b, a is not b, a in c, a not in c)
    unary = (+a, -a, ~a)
    a += b
    a -= b
    a *= b
    a @= b
    a /= b
    a //= b
    a %= b
    a **= b
    a <<= b
    a >>= b
    a |= b
    a ^= b
    a &= b
    captured = (named := a)
    expanded = [*args, a]
    mapping = {**kwargs, "value": b}
    awaited = await awaitable
    yield from_value
    yield awaited
    return arithmetic, bitwise, boolean, comparisons, unary, captured, expanded, mapping, named
''',
    "grammar/python.py": '''import contextlib
from typing import TypeAlias

TypeName: TypeAlias = int
MODULE_VALUE = 1


class RoleName:
    field = 2

    def method(self, RoleName: TypeName, *items: int, flag: bool = True, **options: str):
        local = None
        truth = True
        integer = 0x2A
        floating = 3.5e2
        complex_value = 4j
        string = "value\\n"
        raw_bytes = b"bytes"
        formatted = f"{RoleName!r:>10}-{integer}"
        self.field += integer
        self.method_name = options.get("method_name")

        def nested(shadow=RoleName):
            RoleName = shadow
            first = RoleName + 1
            second = first * 2
            third = second // 3
            return third

        anonymous = lambda value: value + integer
        if flag and integer > 0:
            local = nested()
            integer += 1
            string = string.strip()
            truth = not truth
        else:
            local = 0
            integer -= 1
            string = "fallback"
            truth = False

        for item in items:
            integer += item
            local = integer
            string += str(item)
            truth = truth or bool(item)
        else:
            integer += 1
            local = integer
            string += "done"
            truth = bool(integer)

        while integer < 100:
            integer += 2
            local = integer
            string += "."
            truth = truth and integer < 200
        else:
            integer += 1
            local = integer
            string += "while-done"
            truth = True

        with contextlib.nullcontext(local) as same:
            local = same
            integer += 1
            string += str(same)
            truth = same is not None

        try:
            local = options["value"]
            integer += int(local)
            string += str(local)
            truth = bool(local)
        except (KeyError, ValueError) as error:
            local = error
            integer = -1
            string = repr(error)
            truth = False
        else:
            integer += 1
            local = integer
            string += "ok"
            truth = True
        finally:
            self.field = integer
            self.method_name = string
            local = local or 0
            truth = bool(truth)

        match local:
            case {"kind": kind, **rest} if kind:
                integer += len(rest)
                string += kind
                truth = True
                local = rest
            case [head, *tail]:
                integer += len(tail)
                string += str(head)
                truth = bool(tail)
                local = tail
            case _:
                integer = 0
                string = "none"
                truth = False
                local = None
        return local, truth, integer, floating, complex_value, raw_bytes, formatted, anonymous


async def async_generator(stream):
    total = 0
    async for item in stream:
        total += item
        await stream.checkpoint()
        yield total
        total = +total
    async with stream.context() as resource:
        total += 1
        await resource.checkpoint()
        yield total
        total = +total
    try:
        total += 1
        await stream.checkpoint()
        yield total
        total = +total
    except* ValueError as error_group:
        total = len(error_group.exceptions)
        await stream.checkpoint()
        yield total
        total = -total
    return
''',
    "grammar/GrammarProbe.java": '''package d0;

import java.util.List;

final class TypeName {}

record CompactRecord(int value) {
    CompactRecord {
        int first = value + 1;
        int second = first + 1;
        int third = second + 1;
        value = third + 1;
    }
}

public class GrammarProbe<T extends TypeName> {
    static final long FIELD = 1L;
    private String member = "member";

    static {
        int first = 1;
        int second = first + 1;
        int third = second + 1;
        int fourth = third + 1;
    }

    {
        member = "one";
        member += "two";
        member = member.trim();
        member = member.toUpperCase();
    }

    public GrammarProbe() {
        this.member = "constructed";
        this.member += "!";
        this.member = this.member.trim();
        this.member = String.valueOf(this.member);
    }

    public synchronized int method(TypeName RoleName, int limit, String... rest) {
        Object nil = null;
        boolean truth = true;
        char character = '\u03bb';
        String string = "value\\n";
        String text = """
                line one
                line two
                """;
        int integer = 0x2A;
        int decimal = 42;
        int octal = 052;
        int binary = 0b101010;
        double floating = 3.5e2;
        float single = 1.25f;
        double hexadecimalFloating = 0x1.0p2;
        long widened = 9L;

        class LocalType {
            int nestedMethod(int value) {
                int first = value + 1;
                int second = first * 2;
                int third = second / 3;
                return third;
            }
        }

        Runnable blockLambda = () -> {
            member = "lambda";
            integer++;
            truth = !truth;
            string += member;
        };
        Runnable expressionLambda = () -> member.trim();
        Runnable anonymous = new Runnable() {
            @Override public void run() {
                member = "anonymous";
                member += "!";
                member = member.trim();
                member = member.toUpperCase();
            }
        };

        if (truth && integer > 0) {
            integer += limit;
            member = string;
            truth = false;
            widened <<= 1;
        } else {
            integer -= limit;
            member = text;
            truth = true;
            widened >>= 1;
        }

        outer: for (int index = 0; index < limit; index++) {
            integer += index;
            member += index;
            truth |= index > 1;
            if (index == 2) continue outer;
        }

        for (String item : rest) {
            integer += item.length();
            member += item;
            truth = truth || item.isEmpty();
            widened += integer;
        }

        do {
            integer += 1;
            widened += 1L;
            truth = truth && integer < 1000;
            member += "d";
        } while (integer < 10);

        while (integer < 100) {
            integer++;
            widened--;
            truth = truth || integer == limit;
            member += ".";
        }

        try {
            integer = Integer.parseInt(member);
            widened += integer;
            truth = integer instanceof Integer;
            member = member.strip();
        } catch (RuntimeException error) {
            integer = -1;
            widened = 0L;
            truth = false;
            member = error.getMessage();
        } finally {
            integer += 1;
            widened += 1L;
            truth = !truth;
            member = String.valueOf(integer);
        }

        try (var resource = new java.io.ByteArrayInputStream(new byte[0])) {
            integer += resource.available();
            widened += integer;
            truth = resource.markSupported();
            member += "resource";
        } catch (java.io.IOException error) {
            integer = -2;
            widened = -2L;
            truth = false;
            member = error.getMessage();
        }

        synchronized (this) {
            integer ^= limit;
            widened >>>= 1;
            truth &= integer != 0;
            member += string;
        }

        {
            integer += 1;
            widened += 1L;
            truth = truth || integer > 0;
            member += "block";
        }

        switch (integer) {
            case 1:
                integer += 1;
                member = "one";
                truth = true;
                break;
            default:
                integer = 0;
                member = "default";
                truth = false;
                break;
        }

        int selected = switch (integer) {
            case 2 -> {
                int first = integer + 1;
                int second = first + 1;
                int third = second + 1;
                yield third + 1;
            }
            default -> 0;
        };
        return integer + selected + character + rest.length + List.of(rest).size();
    }
}
''',
    "grammar/OperatorProbe.java": '''package d0;

@Deprecated
final class OperatorProbe {
    static final int CONSTANT = 1;
    int helper() { return 1; }
    int probe(int a, int b, Object value, int... rest) {
        int arithmetic = a + b - a * b / 2 % 3;
        int shifts = (a << b) + (a >> b) + (a >>> b);
        int bits = (a & b) | (a ^ b);
        boolean logic = a < b && a <= b || a > b || a >= b;
        boolean equality = a == b || a != b;
        boolean typed = value instanceof String;
        int unary = +a + -b + ~a;
        boolean negated = !logic;
        a += b; a -= b; a *= b; a /= b; a %= b;
        a &= b; a |= b; a ^= b; a <<= b; a >>= b; a >>>= b;
        a++; b--;
        int ternary = logic ? a : b;
        Runnable lambda = () -> helper();
        java.util.function.IntSupplier reference = this::helper;
        Object created = new Object();
        return arithmetic + shifts + bits + unary + ternary + rest.length + (negated ? 1 : 0);
    }
}
''',
    "grammar/grammar.go": '''package d0

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
    character := '\u03bb'
    interpreted := "value\\n"
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
''',
    "grammar/operators.go": '''package d0operators

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
''',
    "grammar/javascript.js": '''import value from "module";

const FIELD = 1;
const concise = item => item + FIELD;
const blockArrow = (item, ...rest) => {
    const first = item + 1;
    const second = first * 2;
    const third = second / 3;
    return third + rest.length;
};

const objectValue = {
    member: 1,
    method(parameter) {
        let first = parameter + 1;
        let second = first * 2;
        let third = second / 3;
        return third;
    },
};

class RoleName {
    static field = 1;
    #privateField = 2;
    static {
        this.field += 1;
        this.field *= 2;
        this.field -= 1;
        this.field ||= 1;
    }
    constructor(value) {
        this.value = value;
        this.value += 1;
        this.value *= 2;
        this.value ??= 0;
    }
    static method(parameter, ...rest) {
        let nilValue = null;
        let truth = true;
        let number = 3.5e2;
        let bigint = 42n;
        let string = "value\\n";
        let regex = /a+b?/giu;
        let template = `value ${parameter} ${rest.length}`;
        let RoleName = parameter;

        function nested(shadow) {
            let RoleName = shadow;
            let first = RoleName + 1;
            let second = first * 2;
            return second / 3;
        }

        const anonymous = function (item) {
            const first = item + 1;
            const second = first * 2;
            const third = second / 3;
            return third;
        };

        if (truth && number > 0) {
            number += nested(parameter);
            string += template;
            truth = !truth;
            bigint <<= 1n;
        } else {
            number -= 1;
            string = "fallback";
            truth = false;
            bigint >>= 1n;
        }

        outer: for (let index = 0; index < number; index++) {
            number += index;
            string += index;
            truth ||= index > 1;
            if (index === 2) continue outer;
        }

        for (const key in objectValue) {
            number += key.length;
            string += key;
            truth = truth || key === "member";
            bigint += 1n;
        }

        for (const item of rest) {
            number += item.length;
            string += item;
            truth = truth && item.length > 0;
            bigint += 1n;
        }

        do {
            number += 1;
            bigint += 1n;
            truth = truth && number < 1000;
            string += "d";
        } while (number < 10);

        while (number < 100) {
            number++;
            bigint--;
            truth &&= number !== 0;
            string += ".";
        }

        try {
            number = Number(string);
            bigint += 1n;
            truth = regex.test(string);
            string = string.trim();
        } catch (error) {
            number = -1;
            bigint = 0n;
            truth = false;
            string = error?.message ?? "error";
        } finally {
            number += 1;
            bigint += 1n;
            truth = !truth;
            string = String(number);
        }

        switch (number) {
            case 1:
                number += 1;
                string = "one";
                truth = true;
                break;
            default:
                number = 0;
                string = "default";
                truth = false;
                break;
        }

        {
            number ^= parameter;
            bigint **= 2n;
            truth = truth || false;
            string = objectValue?.method?.(number);
        }
        return { ...objectValue, number, bigint, string, truth, nilValue, regex, anonymous, concise, blockArrow, value };
    }
}

async function* asyncGenerator(stream) {
    let total = 0;
    for await (const item of stream) {
        total += item;
        await stream.checkpoint();
        yield total;
        total = +total;
    }
}

export { RoleName };
''',
    "grammar/operators.js": '''async function* operatorProbe(a, b, object, iterable) {
    const arithmetic = a + b - a * b / 2 % 3 ** 2;
    const shifts = (a << b) + (a >> b) + (a >>> b);
    const bits = (a & b) | (a ^ b);
    const logic = a < b && a <= b || a > b || a >= b;
    const equality = a == b || a != b || a === b || a !== b;
    const membership = "field" in object || object instanceof Object;
    const unary = +a + -b + ~a;
    const types = [!logic, typeof object, void a, delete object.removable];
    a += b; a -= b; a *= b; a /= b; a %= b; a **= b;
    a &= b; a |= b; a ^= b; a <<= b; a >>= b; a >>>= b;
    object.left &&= a; object.right ||= b; object.value ??= 0;
    a++; b--;
    const ternary = logic ? a : b;
    const nullish = object?.field?.method?.() ?? 0;
    const spread = [...iterable, a];
    const clone = { ...object, a };
    const arrow = (...rest) => rest.length;
    const awaited = await Promise.resolve(a);
    yield awaited;
    return arithmetic + shifts + bits + unary + ternary + nullish + arrow(...spread) + Number(types.length + membership + equality + clone.a);
}
''',
    "grammar/javascript.jsx": '''const Component = (props) => {
    const title = props.title ?? "untitled";
    const header = <h1 data-kind="title">{title}</h1>;
    const body = <section>{props.children}</section>;
    return <main>{header}{body}</main>;
};
''',
    "grammar/typescript.ts": '''interface TypeName {
    member: number;
    method(value: number): number;
}

type Alias<T> = { field: T };

abstract class Base<T> {
    abstract skipped(value: T): T;
}

class RoleName extends Base<number> implements TypeName {
    static readonly FIELD: number = 1;
    public member: number = 2;
    private labelText: string = "label";
    static {
        this.FIELD.valueOf();
        const first = 1;
        const second = first + 1;
        void second;
    }

    constructor(public value: number) {
        super();
        this.value += 1;
        this.member = value;
        this.labelText += String(value);
    }

    static method(this: typeof RoleName, RoleName: number, ...rest: string[]): number {
        let nilValue: null = null;
        let truth: boolean = true;
        let number: number = 3.5e2;
        let bigint: bigint = 42n;
        let string: string = "value";
        let regex: RegExp = /a+b?/giu;
        let template = `value ${RoleName} ${rest.length}`;
        const shaped: Alias<number> = { field: number };

        const blockArrow = (item: number): number => {
            const first = item + 1;
            const second = first * 2;
            const third = second / 3;
            return third;
        };
        const concise = (item: number): number => item + 1;

        if (truth && number > 0) {
            number += blockArrow(RoleName);
            string += template;
            truth = !truth;
            bigint <<= 1n;
        } else {
            number -= 1;
            string = "fallback";
            truth = false;
            bigint >>= 1n;
        }

        try {
            number = Number(string);
            bigint += 1n;
            truth = regex.test(string);
            string = string.trim();
        } catch (error: unknown) {
            number = -1;
            bigint = 0n;
            truth = false;
            string = (error as Error)?.message ?? "error";
        } finally {
            number += 1;
            bigint += 1n;
            truth = !truth;
            string = String(number);
        }

        switch (number) {
            case 1:
                number += 1;
                string = "one";
                truth = true;
                break;
            default:
                number = 0;
                string = "default";
                truth = false;
                break;
        }

        outer: for (let index = 0; index < number; index++) {
            number += index;
            string += String(index);
            truth ||= index > 1;
            if (index === 2) continue outer;
        }
        for (const item of rest) {
            number += item.length;
            string += item;
            truth = truth && item.length > 0;
            bigint += 1n;
        }
        while (number < 100) {
            number++;
            bigint++;
            truth = truth || number > 0;
            string += ".";
        }
        do {
            number += 1;
            bigint += 1n;
            truth = truth && number < 1000;
            string += "d";
        } while (number < 10);
        {
            number ^= RoleName;
            bigint **= 2n;
            truth = truth || false;
            string = shaped?.field?.toString() ?? string;
        }
        return shaped.field + number + Number(bigint) + string.length + Number(nilValue) + Number(concise);
    }

    method(value: number): number {
        const first = value + 1;
        const second = first * 2;
        const third = second / 3;
        return third;
    }
}

async function* asyncGenerator(stream: AsyncIterable<number>) {
    let total = 0;
    for await (const item of stream) {
        total += item;
        await Promise.resolve(total);
        yield total;
        total = +total;
    }
}
''',
    "grammar/operators.ts": '''type RequiredMap<T> = { readonly [K in keyof T]-?: T[K] };
type Predicate = (value: unknown) => value is string;

async function* operatorProbe<T extends object>(a: number, b: number, object: T, iterable: number[]) {
    const arithmetic = a + b - a * b / 2 % 3 ** 2;
    const shifts = (a << b) + (a >> b) + (a >>> b);
    const bits = (a & b) | (a ^ b);
    const logic = a < b && a <= b || a > b || a >= b;
    const equality = a == b || a != b || a === b || a !== b;
    const membership = "field" in object || object instanceof Object;
    const unary = +a + -b + ~a;
    const types = [!logic, typeof object, void a, delete (object as any).removable];
    a += b; a -= b; a *= b; a /= b; a %= b; a **= b;
    a &= b; a |= b; a ^= b; a <<= b; a >>= b; a >>>= b;
    (object as any).left &&= a; (object as any).right ||= b; (object as any).value ??= 0;
    a++; b--;
    const ternary = logic ? a : b;
    const nullish = (object as any)?.field?.method?.() ?? 0;
    const spread = [...iterable, a];
    const clone = { ...(object as any), a };
    const arrow = (...rest: number[]) => rest.length;
    const asserted = object satisfies object;
    const nonNull = object!;
    const awaited = await Promise.resolve(a);
    yield awaited;
    return arithmetic + shifts + bits + unary + ternary + nullish + arrow(...spread) + Number(types.length + membership + equality + clone.a + Number(asserted) + Number(nonNull));
}
''',
    "grammar/typescript.tsx": '''type Props = { title?: string; children?: unknown };

export function Component(props: Props): JSX.Element {
    const title = props.title ?? "untitled";
    const header = <h1 data-kind="title">{title}</h1>;
    const body = <section>{props.children}</section>;
    return <main>{header}{body}</main>;
}
''',
}


THRESHOLD_SOURCES = {
    "thresholds/thresholds.py": '''def below_statements(value):
    first = transform(
        value, value + 1, value + 2, value + 3,
    )
    second = combine(
        first, value * 2, value * 3, value * 4,
    )
    return finalize(
        first, second, value, value + 5,
    )


def below_tokens(value):
    first = (
        value + 1
    )
    second = (
        first + 1
    )
    third = (
        second + 1
    )
    return (
        third
    )


def below_nloc(value):
    first = transform(value, value + 1, value + 2, value + 3); second = combine(first, value * 2, value * 3, value * 4); third = finalize(first, second, value, value + 5); return publish(first, second, third, value)


def qualifying(value):
    first = transform(
        value, value + 1, value + 2, value + 3,
    )
    second = combine(
        first, value * 2, value * 3, value * 4,
    )
    third = finalize(
        first, second, value, value + 5,
    )
    return publish(
        first, second, third, value,
    )
''',
    "thresholds/Thresholds.java": '''class Thresholds {
    int belowStatements(int value) {
        int first = transform(
            value, value + 1,
            value + 2, value + 3);
        int second = combine(
            first, value * 2,
            value * 3, value * 4);
        return finalize(
            first, second,
            value, value + 5);
    }
    int belowTokens(int value) {
        int first =
            value + 1;
        int second =
            first + 1;
        int third =
            second + 1;
        return
            third;
    }
    int belowNloc(int value) { int first = transform(value, value + 1, value + 2, value + 3); int second = combine(first, value * 2, value * 3, value * 4); int third = finalize(first, second, value, value + 5); return publish(first, second, third, value); }
    int qualifying(int value) {
        int first = transform(
            value, value + 1, value + 2, value + 3);
        int second = combine(
            first, value * 2, value * 3, value * 4);
        int third = finalize(
            first, second, value, value + 5);
        return publish(
            first, second, third, value);
    }
}
''',
    "thresholds/thresholds.go": '''package thresholds

func belowStatements(value int) int {
    first := transform(
        value, value + 1, value + 2, value + 3,
    )
    second := combine(
        first, value * 2, value * 3, value * 4,
    )
    return finalize(
        first, second, value, value + 5,
    )
}
func belowTokens(value int) int {
    first :=
        value + 1
    second :=
        first + 1
    third :=
        second + 1
    return (
        third
    )
}
func belowNloc(value int) int { first := transform(value, value + 1, value + 2, value + 3); second := combine(first, value * 2, value * 3, value * 4); third := finalize(first, second, value, value + 5); return publish(first, second, third, value) }
func qualifying(value int) int {
    first := transform(
        value, value + 1, value + 2, value + 3,
    )
    second := combine(
        first, value * 2, value * 3, value * 4,
    )
    third := finalize(
        first, second, value, value + 5,
    )
    return publish(
        first, second, third, value,
    )
}
''',
    "thresholds/thresholds.js": '''function belowStatements(value) {
    const first = transform(
        value, value + 1, value + 2, value + 3,
    );
    const second = combine(
        first, value * 2, value * 3, value * 4,
    );
    return finalize(
        first, second, value, value + 5,
    );
}
function belowTokens(value) {
    const first =
        value + 1;
    const second =
        first + 1;
    const third =
        second + 1;
    return (
        third
    );
}
function belowNloc(value) { const first = transform(value, value + 1, value + 2, value + 3); const second = combine(first, value * 2, value * 3, value * 4); const third = finalize(first, second, value, value + 5); return publish(first, second, third, value); }
function qualifying(value) {
    const first = transform(
        value, value + 1, value + 2, value + 3,
    );
    const second = combine(
        first, value * 2, value * 3, value * 4,
    );
    const third = finalize(
        first, second, value, value + 5,
    );
    return publish(
        first, second, third, value,
    );
}
''',
    "thresholds/thresholds.ts": '''function belowStatements(value: number): number {
    const first = transform(
        value, value + 1, value + 2, value + 3,
    );
    const second = combine(
        first, value * 2, value * 3, value * 4,
    );
    return finalize(
        first, second, value, value + 5,
    );
}
function belowTokens(value: number): number {
    const first =
        value + 1;
    const second =
        first + 1;
    const third =
        second + 1;
    return (
        third
    );
}
function belowNloc(value: number): number { const first = transform(value, value + 1, value + 2, value + 3); const second = combine(first, value * 2, value * 3, value * 4); const third = finalize(first, second, value, value + 5); return publish(first, second, third, value); }
function qualifying(value: number): number {
    const first = transform(
        value, value + 1, value + 2, value + 3,
    );
    const second = combine(
        first, value * 2, value * 3, value * 4,
    );
    const third = finalize(
        first, second, value, value + 5,
    );
    return publish(
        first, second, third, value,
    );
}
''',
}


def python_variants() -> str:
    bodies = {
        "baseline": """    first = transform(\n        left, right, left + right, 1,\n    )\n    second = combine(\n        first, left * 2, right * 3, 2,\n    )\n    third = finalize(\n        first, second, left - right, 3,\n    )\n    return publish(\n        first, second, third, 4,\n    )""",
        "exact_copy": """    first = transform(\n        left, right, left + right, 1,\n    )\n    second = combine(\n        first, left * 2, right * 3, 2,\n    )\n    third = finalize(\n        first, second, left - right, 3,\n    )\n    return publish(\n        first, second, third, 4,\n    )""",
        "whitespace_comment_copy": """    first=transform( left, right, left + right, 1 )  # formatting/comment\n    second = combine(\n        first,left*2,right*3,2,\n+    )\n+    third=finalize(first, second, left-right, 3)\n+    return publish(first, second, third, 4)""".replace("\n+", "\n"),
        "renamed": """    alpha = transform(\n        x, y, x + y, 1,\n    )\n    beta = combine(\n        alpha, x * 2, y * 3, 2,\n    )\n    gamma = finalize(\n        alpha, beta, x - y, 3,\n    )\n    return publish(\n        alpha, beta, gamma, 4,\n    )""",
        "literal_changed": """    first = transform(\n        left, right, left + right, 11,\n    )\n    second = combine(\n        first, left * 2, right * 3, 22,\n    )\n    third = finalize(\n        first, second, left - right, 33,\n    )\n    return publish(\n        first, second, third, 44,\n    )""",
        "operator_changed": """    first = transform(\n        left, right, left - right, 1,\n    )\n    second = combine(\n        first, left * 2, right * 3, 2,\n    )\n    third = finalize(\n        first, second, left - right, 3,\n    )\n    return publish(\n        first, second, third, 4,\n    )""",
        "statement_added": """    first = transform(left, right, left + right, 1)\n    extra = transform(right, left, right + left, 0)\n    second = combine(first, left * 2, right * 3, 2)\n    third = finalize(first, second, left - right, 3)\n    return publish(first, second, third, 4)""",
        "statement_removed": """    first = transform(left, right, left + right, 1)\n    second = combine(first, left * 2, right * 3, 2)\n    return publish(first, second, left - right, 4)""",
        "statement_reordered": """    second = combine(left, left * 2, right * 3, 2)\n    first = transform(left, right, left + right, 1)\n    third = finalize(first, second, left - right, 3)\n    return publish(first, second, third, 4)""",
    }
    parts = [f"def {name}(left, right):\n{body}\n" for name, body in bodies.items()]
    parts.append('''def nested_outer_a(left, right):
    def inner(value):
        first = transform(value, left, right, 1)
        second = combine(first, value, left, 2)
        third = finalize(first, second, right, 3)
        return publish(first, second, third, 4)
    first = inner(left)
    second = inner(right)
    third = combine(first, second, left, right)
    return publish(first, second, third, 4)

def nested_outer_b(left, right):
    def inner(value):
        first = transform(value, left, right, 1)
        second = combine(first, value, left, 2)
        third = finalize(first, second, right, 3)
        return publish(first, second, third, 4)
    first = inner(left)
    second = inner(right)
    third = combine(first, second, left, right)
    return publish(first, second, third, 4)
''')
    return "\n".join(parts)


def brace_variants(language: str) -> str:
    if language == "java":
        prefix, suffix, signature, decl, end = "class StructuralVariants {\n", "}\n", "int {name}(int left, int right) {", "int ", "}"
    elif language == "go":
        prefix, suffix, signature, decl, end = "package structural\n\n", "", "func {name}(left int, right int) int {", ":= ", "}"
    elif language == "typescript":
        prefix, suffix, signature, decl, end = "", "", "function {name}(left: number, right: number): number {", "const ", "}"
    else:
        prefix, suffix, signature, decl, end = "", "", "function {name}(left, right) {", "const ", "}"

    def statement(name: str, expression: str, indent: str = "    ") -> str:
        if language == "go":
            return f"{indent}{name} {decl}{expression}"
        return f"{indent}{decl}{name} = {expression};"

    def body(kind: str) -> list[str]:
        names = ("first", "second", "third")
        vars_ = ("left", "right")
        literals = ("1", "2", "3", "4")
        op = "+" if kind != "operatorChanged" else "-"
        if kind == "renamed":
            names, vars_ = ("alpha", "beta", "gamma"), ("x", "y")
        if kind == "literalChanged":
            literals = ("11", "22", "33", "44")
        trailing = "" if language == "java" else ","
        lines = [
            statement(names[0], f"transform(\n        {vars_[0]}, {vars_[1]}, {vars_[0]} {op} {vars_[1]}, {literals[0]}{trailing}\n    )"),
            statement(names[1], f"combine(\n        {names[0]}, {vars_[0]} * 2, {vars_[1]} * 3, {literals[1]}{trailing}\n    )"),
            statement(names[2], f"finalize(\n        {names[0]}, {names[1]}, {vars_[0]} - {vars_[1]}, {literals[2]}{trailing}\n    )"),
            f"    return publish(\n        {names[0]}, {names[1]}, {names[2]}, {literals[3]}{trailing}\n    ){'' if language == 'go' else ';'}",
        ]
        if kind == "statementAdded":
            lines.insert(1, statement("extra", "transform(right, left, right + left, 0)"))
        elif kind == "statementRemoved":
            lines.pop(2)
        elif kind == "statementReordered":
            lines[0], lines[1] = lines[1], lines[0]
        return lines

    functions = []
    for name in (
        "baseline", "exactCopy", "whitespaceCommentCopy", "renamed",
        "literalChanged", "operatorChanged", "statementAdded",
        "statementRemoved", "statementReordered",
    ):
        params = ("x", "y") if name == "renamed" else ("left", "right")
        if language == "java":
            head = f"    int {name}(int {params[0]}, int {params[1]}) {{"
            tail = "    }"
        elif language == "go":
            head = f"func {name}({params[0]} int, {params[1]} int) int {{"
            tail = "}"
        elif language == "typescript":
            head = f"function {name}({params[0]}: number, {params[1]}: number): number {{"
            tail = "}"
        else:
            head = f"function {name}({params[0]}, {params[1]}) {{"
            tail = "}"
        lines = body(name)
        if name == "whitespaceCommentCopy":
            lines.insert(1, "    // formatting/comment only")
        functions.append("\n".join((head, *lines, tail)))
    return prefix + "\n\n".join(functions) + "\n" + suffix


GENERATED_TEXT = {
    **GRAMMAR_SOURCES,
    **THRESHOLD_SOURCES,
    "structural/python.py": python_variants(),
    "structural/StructuralVariants.java": brace_variants("java"),
    "structural/structural.go": brace_variants("go"),
    "structural/structural.js": brace_variants("javascript"),
    "structural/structural.ts": brace_variants("typescript"),
    "selection/generated/copy.generated.js": "function generated(){ return 1; }\n",
    "selection/vendor/copy.java": "class VendorCopy { void copied() {} }\n",
    "selection/tests/copy.py": "def excluded_test():\n    return 1\n",
    "selection/build/copy.go": "package build\nfunc copied() {}\n",
    "selection/declarations.d.ts": "declare function signatureOnly(value: number): number;\n",
    "selection/stubs.pyi": "def signature_only(value: int) -> int: ...\n",
    "selection/bundle.min.js": "function tiny(){return 1;}\n",
    "selection/unsupported.rb": "def unsupported; 1; end\n",
}


SPECIAL_BYTES = {
    "portability/café/日本語.py": "def 計算(値):\n    第一 = 値 + 1\n    第二 = 第一 * 2\n    第三 = 第二 - 3\n    return 第三\n".encode("utf-8"),
    "portability/crlf.java": GRAMMAR_SOURCES["grammar/GrammarProbe.java"].replace("\n", "\r\n").encode("utf-8"),
    "portability/lone_cr.go": GRAMMAR_SOURCES["grammar/grammar.go"].replace("\n", "\r").encode("utf-8"),
    "portability/bom.ts": b"\xef\xbb\xbf" + b"function bom(value: number) {\n  const a = value + 1;\n  const b = a + 1;\n  const c = b + 1;\n  return c;\n}\n",
    "portability/no_final_newline.js": b"function noFinal(value) {\n  const a = value + 1;\n  const b = a + 1;\n  const c = b + 1;\n  return c;\n}",
    "portability/malformed.py": b"def broken(:\n    return 1\n",
    "portability/malformed.java": b"class Broken { void method( { int x = 1; }\n",
    "portability/malformed.go": b"package broken\nfunc broken( { return 1 }\n",
    "portability/malformed.js": b"function broken( { return 1; }\n",
    "portability/malformed.ts": b"function broken(value: ) { return value; }\n",
    "portability/recovered_import_assertion.js": b'import data from "./data.json" assert { type: "json" };\nfunction recovered(){ const a=data; const b=a.value; const c=b+1; return c; }\n',
    "portability/recovered_keyword_parameter.ts": b'type KeywordCallback = (string) => number;\nconst recovered: KeywordCallback = (value) => value.length;\n',
}


def main() -> None:
    for relative, text in sorted(GENERATED_TEXT.items()):
        path = CORPUS / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    for relative, payload in sorted(SPECIAL_BYTES.items()):
        path = CORPUS / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    print(f"materialized {len(GENERATED_TEXT) + len(SPECIAL_BYTES)} files under {CORPUS}")


if __name__ == "__main__":
    main()
