async function* templateBeta(stream) {
  const one = await stream.read(101);
  const two = await stream.read(202);
  const three = one + two;
  const four = three * 303;
  const rendered = `beta:${one}:${two}:${four}`;
  await stream.write(rendered);
  yield rendered;
  return rendered;
}
