function orderBase(source) {
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
