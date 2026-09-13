async function* templateAlpha(source) {
  const first = await source.read(10);
  const second = await source.read(20);
  const third = first + second;
  const fourth = third * 30;
  const message = `alpha:${first}:${second}:${fourth}`;
  await source.write(message);
  yield message;
  return message;
}
