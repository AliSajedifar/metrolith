async def fstring_changed(stream):
    one = await stream.read(101)
    two = await stream.read(202)
    three = one + two
    four = three * 303
    rendered = f"beta:{two!r}:{one:>999}:{four}"
    await stream.write(rendered)
    yield rendered
    return
