"""Thin wrapper so tests can call the package's independent referee."""
from minicloudos.referee import RefereeError, verify_result as _verify


def verify_result(test, result, switch_cost=0):
    try:
        _verify(result, switch_cost)
    except RefereeError as e:
        test.fail(str(e))
