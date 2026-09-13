interface TypeName {
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
