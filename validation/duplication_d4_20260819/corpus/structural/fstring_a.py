async def fstring_alpha(source):
    first = await source.read(10)
    second = await source.read(20)
    third = first + second
    fourth = third * 30
    message = f"alpha:{first!r}:{second:>10}:{fourth}"
    await source.write(message)
    yield message
    return
