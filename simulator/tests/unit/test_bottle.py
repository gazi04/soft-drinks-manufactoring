"""Bottle objects and their ids.  uv run pytest"""

from simulator.bottle import Bottle, BottleFactory


def test_new_bottle_is_empty():
    bottle = Bottle(id=1)
    assert bottle.level_ml == 0
    assert not bottle.rinsed
    assert not bottle.capped
    assert bottle.name == "Bottle 1"


def test_ids_increase():
    factory = BottleFactory()
    ids = [factory.new().id for _ in range(3)]
    assert ids == [1, 2, 3]


def test_each_factory_has_its_own_counter():
    first, second = BottleFactory(), BottleFactory()
    first.new()
    first.new()
    assert second.new().id == 1


def test_bottle_keeps_its_state():
    # what the bottle "remembers" travels with the object (e.g. after an E-stop)
    bottle = BottleFactory().new()
    bottle.level_ml = 200
    pockets = [bottle, None]
    pockets = [None, pockets[0]]  # the belt moves the bottle one pocket forward
    assert pockets[1].level_ml == 200
