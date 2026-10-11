"""Fixture for assertion-block detection (test-quality smells)."""


def test_many_bare_asserts():
    x = 1
    assert x == 1
    assert x == 1
    assert x == 1
    assert x == 1
    assert x == 1
    assert x == 1
    assert x == 1
    assert x == 1
    assert x == 1
    assert x == 1
    assert x == 1
    assert x == 1
    assert x == 1
    assert x == 1
    assert x == 1
    assert x == 1


class _Case:
    def test_unittest_calls(self):
        self.assertEqual(1, 1)
        self.assertEqual(2, 2)
        self.assertTrue(True)

    def test_few_asserts(self):
        assert 1 == 1
        x = 2
        assert x == 2


def test_split_runs():
    assert 1 == 1
    assert 2 == 2
    do_work()
    assert 3 == 3
    assert 4 == 4


def do_work():
    return None
