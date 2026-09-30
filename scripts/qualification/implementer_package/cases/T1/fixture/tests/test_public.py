from app import clamp

def test_baseline():
    assert clamp(3, 0, 8) == 3
