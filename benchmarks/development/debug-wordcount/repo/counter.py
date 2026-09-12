"""Count words in a string."""


def count_words(text):
    if not text or not text.strip():
        return 0
    # BUG: splitting on a single space mishandles tabs and runs of whitespace.
    return len(text.strip().split(" "))
