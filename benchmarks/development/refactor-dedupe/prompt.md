Remove the duplicated whitespace-collapsing logic in `normalize.py` by reusing
`helpers.collapse_spaces`. Behavior must not change: lowercase, strip, collapse
whitespace runs to one space. `normalize` and `slug` are public API.
