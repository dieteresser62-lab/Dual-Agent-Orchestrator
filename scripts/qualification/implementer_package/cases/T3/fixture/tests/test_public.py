from app import discount

def test_baseline():
    assert discount(100, 20) == 80
