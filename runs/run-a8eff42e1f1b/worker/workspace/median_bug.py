"""Return the median of a non-empty list of numbers."""


def median(values):
    if not values:
        raise ValueError("median of empty list")
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    # BUG: uses the wrong pair for even-length input.
    return (ordered[mid] + ordered[mid + 1]) / 2
