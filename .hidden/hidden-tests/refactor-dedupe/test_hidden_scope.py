"""Hidden API/scope review for refactor-dedupe. Evaluator-owned."""
import inspect

import normalize as normalize_module


def test_normalize_signature_stable():
    parameters = list(inspect.signature(normalize_module.normalize).parameters.values())
    assert [parameter.name for parameter in parameters] == ["text"]


def test_slug_signature_stable():
    parameters = list(inspect.signature(normalize_module.slug).parameters.values())
    assert [parameter.name for parameter in parameters] == ["text"]


def test_public_names():
    assert {"normalize", "slug"}.issubset(set(dir(normalize_module)))
