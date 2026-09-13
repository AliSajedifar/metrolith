#!/usr/bin/env python
"""Module docstring counts as CODE, not a comment."""
# A pure comment line.

import os


class Service:
    """Class docstring."""

    def handle(self, request):  # trailing comment stays code
        marker = "# not a comment"
        return os.path.join(marker, request)


def helper():
    return 1
