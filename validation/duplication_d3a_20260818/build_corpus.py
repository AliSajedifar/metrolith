"""Materialize the D3-A structural relation corpus deterministically."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CORPUS = HERE / "corpus"
D2_CORPUS = ROOT / "validation" / "duplication_d2_20260818" / "corpus"
D0_CORPUS = ROOT / "validation" / "duplication_d0_20260818" / "corpus"


FEATURE_COPIES = {
    "features/python.py": D0_CORPUS / "grammar" / "python.py",
    "features/Features.java": D0_CORPUS / "grammar" / "GrammarProbe.java",
    "features/features.go": D0_CORPUS / "grammar" / "grammar.go",
    "features/features.js": D0_CORPUS / "grammar" / "javascript.js",
    "features/features.jsx": D0_CORPUS / "grammar" / "javascript.jsx",
    "features/features.ts": D0_CORPUS / "grammar" / "typescript.ts",
    "features/features.tsx": D0_CORPUS / "grammar" / "typescript.tsx",
}


ORDER_SOURCES = {
    "python/order_base.py": """def order_base(source):
    alpha = source + 1
    beta = source * 2
    gamma = source - 3
    delta = source / 4
    epsilon = source % 5
    zeta = source << 6
    eta = source & 7
    theta = source | 8
    iota = source ^ 9
""",
    "python/order_changed.py": """def order_changed(source):
    beta = source * 2
    alpha = source + 1
    gamma = source - 3
    delta = source / 4
    epsilon = source % 5
    zeta = source << 6
    eta = source & 7
    theta = source | 8
    iota = source ^ 9
""",
    "java/order_base.java": """class OrderBase {
  int run(int source) {
    int alpha = source + 1;
    int beta = source * 2;
    int gamma = source - 3;
    int delta = source / 4;
    int epsilon = source % 5;
    int zeta = source << 6;
    int eta = source & 7;
    int theta = source | 8;
    return source ^ 9;
  }
}
""",
    "java/order_changed.java": """class OrderChanged {
  int run(int source) {
    int beta = source * 2;
    int alpha = source + 1;
    int gamma = source - 3;
    int delta = source / 4;
    int epsilon = source % 5;
    int zeta = source << 6;
    int eta = source & 7;
    int theta = source | 8;
    return source ^ 9;
  }
}
""",
    "go/order_base.go": """package corpus
func orderBase(source int) int {
    alpha := source + 1
    beta := source * 2
    gamma := source - 3
    delta := source / 4
    epsilon := source % 5
    zeta := source << 6
    eta := source & 7
    theta := source | 8
    return source ^ 9
}
""",
    "go/order_changed.go": """package corpus
func orderChanged(source int) int {
    beta := source * 2
    alpha := source + 1
    gamma := source - 3
    delta := source / 4
    epsilon := source % 5
    zeta := source << 6
    eta := source & 7
    theta := source | 8
    return source ^ 9
}
""",
    "javascript/order_base.js": """function orderBase(source) {
  const alpha = source + 1;
  const beta = source * 2;
  const gamma = source - 3;
  const delta = source / 4;
  const epsilon = source % 5;
  const zeta = source << 6;
  const eta = source & 7;
  const theta = source | 8;
  return source ^ 9;
}
""",
    "javascript/order_changed.js": """function orderChanged(source) {
  const beta = source * 2;
  const alpha = source + 1;
  const gamma = source - 3;
  const delta = source / 4;
  const epsilon = source % 5;
  const zeta = source << 6;
  const eta = source & 7;
  const theta = source | 8;
  return source ^ 9;
}
""",
    "typescript/order_base.ts": """function orderBase(source: number): number {
  const alpha = source + 1;
  const beta = source * 2;
  const gamma = source - 3;
  const delta = source / 4;
  const epsilon = source % 5;
  const zeta = source << 6;
  const eta = source & 7;
  const theta = source | 8;
  return source ^ 9;
}
""",
    "typescript/order_changed.ts": """function orderChanged(source: number): number {
  const beta = source * 2;
  const alpha = source + 1;
  const gamma = source - 3;
  const delta = source / 4;
  const epsilon = source % 5;
  const zeta = source << 6;
  const eta = source & 7;
  const theta = source | 8;
  return source ^ 9;
}
""",
}


PYTHON_RELATIONS = {
    "relations/equality_distinct.py": """def equality_distinct(price, tax):
    total = calculate(price, tax, price + tax, 10)
    first = combine(total, price, tax, 20)
    second = combine(first, price, tax, 30)
    third = combine(second, total, price, 40)
    fourth = combine(third, total, tax, 50)
    fifth = combine(fourth, price, tax, 60)
    sixth = combine(fifth, total, tax, 70)
    return publish(total, first, second, third)
""",
    "relations/equality_repeated.py": """def equality_repeated(cost, fee):
    amount = calculate(cost, cost, cost + cost, 10)
    first = combine(amount, cost, cost, 20)
    second = combine(first, cost, cost, 30)
    third = combine(second, amount, cost, 40)
    fourth = combine(third, amount, cost, 50)
    fifth = combine(fourth, cost, cost, 60)
    sixth = combine(fifth, amount, cost, 70)
    return publish(amount, first, second, third)
""",
    "relations/literal_integer.py": """def literal_integer(source):
    alpha = convert(source, 10, source + 10)
    beta = convert(source, 20, source + 20)
    gamma = convert(source, 30, source + 30)
    delta = convert(source, 40, source + 40)
    epsilon = convert(source, 50, source + 50)
    zeta = convert(source, 60, source + 60)
    eta = convert(source, 70, source + 70)
    return publish(alpha, beta, gamma, delta)
""",
    "relations/literal_string.py": """def literal_string(source):
    alpha = convert(source, "10", source + "10")
    beta = convert(source, "20", source + "20")
    gamma = convert(source, "30", source + "30")
    delta = convert(source, "40", source + "40")
    epsilon = convert(source, "50", source + "50")
    zeta = convert(source, "60", source + "60")
    eta = convert(source, "70", source + "70")
    return publish(alpha, beta, gamma, delta)
""",
    "relations/child_left_right.py": """def child_left_right(left, right):
    seed = pair(left, right, 10, 20)
    alpha = combine(seed, left + right, 30, 40)
    beta = combine(alpha, left * right, 50, 60)
    gamma = combine(beta, left - right, 70, 80)
    delta = combine(gamma, left / right, 90, 100)
    epsilon = combine(delta, left % right, 110, 120)
    zeta = combine(epsilon, left & right, 130, 140)
    return publish(alpha, beta, gamma, delta)
""",
    "relations/child_right_left.py": """def child_right_left(left, right):
    seed = pair(left, right, 10, 20)
    alpha = combine(seed, right + left, 30, 40)
    beta = combine(alpha, left * right, 50, 60)
    gamma = combine(beta, left - right, 70, 80)
    delta = combine(gamma, left / right, 90, 100)
    epsilon = combine(delta, left % right, 110, 120)
    zeta = combine(epsilon, left & right, 130, 140)
    return publish(alpha, beta, gamma, delta)
""",
    "relations/control_plain.py": """def control_plain(source):
    alpha = transform(source, 10, 20, 30)
    beta = transform(alpha, 40, 50, 60)
    gamma = transform(beta, 70, 80, 90)
    delta = transform(gamma, 100, 110, 120)
    epsilon = transform(delta, 130, 140, 150)
    zeta = transform(epsilon, 160, 170, 180)
    eta = transform(zeta, 190, 200, 210)
    return publish(alpha, beta, gamma, delta)
""",
    "relations/control_changed.py": """def control_changed(source):
    alpha = transform(source, 10, 20, 30)
    beta = transform(alpha, 40, 50, 60)
    gamma = transform(beta, 70, 80, 90)
    delta = transform(gamma, 100, 110, 120)
    if source:
        epsilon = transform(delta, 130, 140, 150)
    zeta = transform(delta, 160, 170, 180)
    eta = transform(zeta, 190, 200, 210)
    return publish(alpha, beta, gamma, delta)
""",
}


ASYNC_SOURCES = {
    "javascript/async_generator.js": """async function* asyncGenerator(source) {
  const first = await source.read(1);
  const second = await source.read(2);
  const third = await source.read(3);
  const fourth = `${first}:${second}:${third}`;
  yield first;
  yield second;
  yield third;
  return source?.finish?.(fourth);
}
""",
    "typescript/async_generator.ts": """async function* asyncGenerator(source: Stream): AsyncGenerator<number> {
  const first = await source.read(1);
  const second = await source.read(2);
  const third = await source.read(3);
  const fourth = `${first}:${second}:${third}`;
  yield first;
  yield second;
  yield third;
  return source?.finish?.(fourth);
}
""",
}


def main() -> None:
    CORPUS.mkdir(parents=True, exist_ok=True)
    shutil.copytree(D2_CORPUS, CORPUS, dirs_exist_ok=True)
    for relative, source in FEATURE_COPIES.items():
        target = CORPUS / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    for relative, text in {**ORDER_SOURCES, **PYTHON_RELATIONS, **ASYNC_SOURCES}.items():
        target = CORPUS / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")

    hashes = {}
    for path in sorted(CORPUS.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            hashes[path.relative_to(CORPUS).as_posix()] = {
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
    (HERE / "corpus_hashes.json").write_text(
        json.dumps(hashes, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()
