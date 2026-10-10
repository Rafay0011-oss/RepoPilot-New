"""Small caching helper used by the fixture repo."""
from functools import lru_cache


@lru_cache(maxsize=128)
def get_product(product_id: int) -> dict:
    return {"id": product_id}


def ping() -> bool:
    return True
