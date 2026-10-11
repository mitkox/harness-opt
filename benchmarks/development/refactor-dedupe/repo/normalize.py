"""Public normalization API."""
from helpers import collapse_spaces


def normalize(text):
    # NOTE: duplicates helpers.collapse_spaces; refactor should reuse it.
    out = []
    previous_space = False
    for char in text.strip().lower():
        if char.isspace():
            if not previous_space:
                out.append(" ")
            previous_space = True
        else:
            out.append(char)
            previous_space = False
    return "".join(out)


def slug(text):
    return normalize(text).replace(" ", "-")
