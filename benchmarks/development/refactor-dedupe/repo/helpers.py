"""Shared helpers. `collapse_spaces` is the single source of truth."""


def collapse_spaces(value):
    """Collapse every run of whitespace to one space (keeps other chars)."""
    out = []
    previous_space = False
    for char in value:
        if char.isspace():
            if not previous_space:
                out.append(" ")
            previous_space = True
        else:
            out.append(char)
            previous_space = False
    return "".join(out)
