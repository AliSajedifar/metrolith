# Probe: does the reference recognize the `async` forms as control flow?
# Rule table 2.1 S-LOOP covers `for`; an `async for` is a loop.
# Each function: loop (1) + inner if at nesting 1 (2) = 3.
def sync_for(items, flag):
    for item in items:
        if flag:
            return item
    return None


async def async_for(items, flag):
    async for item in items:
        if flag:
            return item
    return None


async def async_with(ctx, flag):
    async with ctx as handle:
        if flag:
            return handle
    return None


def sync_with(ctx, flag):
    with ctx as handle:
        if flag:
            return handle
    return None
