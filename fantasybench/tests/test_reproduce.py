import pytest

from fantasybench.report import run_checks


@pytest.fixture(scope="module")
def checks():
    return {name: (ok, detail) for name, ok, detail in run_checks()[0]}


@pytest.mark.parametrize("n", range(1, 6))
def test_check(checks, n):
    name, (ok, detail) = next((k, v) for k, v in checks.items() if k.startswith(f"{n}."))
    assert ok, f"{name}: {detail}"
