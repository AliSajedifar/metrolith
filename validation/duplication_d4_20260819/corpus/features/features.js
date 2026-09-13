import value from "module";

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
        let string = "value\n";
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
