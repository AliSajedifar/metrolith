async function* asyncGenerator(source) {
  const first = await source.read(1);
  const second = await source.read(2);
  const third = await source.read(3);
  const fourth = `${first}:${second}:${third}`;
  yield first;
  yield second;
  yield third;
  return source?.finish?.(fourth);
}
