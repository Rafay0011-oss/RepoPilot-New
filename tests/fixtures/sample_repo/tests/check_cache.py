from service.cache import get_product


def test_get_product():
    assert get_product(1)["id"] == 1
