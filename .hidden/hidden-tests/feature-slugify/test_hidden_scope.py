"""Hidden API/scope review for feature-slugify. Evaluator-owned."""
import inspect

import slugify as slugify_module
from slugify import slugify


def test_public_function_exists():
    assert callable(slugify)
    assert slugify.__module__ == "slugify"


def test_single_positional_parameter():
    parameters = list(inspect.signature(slugify).parameters.values())
    assert len(parameters) == 1
    assert parameters[0].name == "text"
    assert parameters[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD


def test_returns_str():
    assert isinstance(slugify("Hello"), str)
    assert slugify_module.__file__
