from openparkcad.geometry import point_in_polygon, polygons_intersect


def test_point_in_polygon_for_square():
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]

    assert point_in_polygon((5, 5), square)
    assert point_in_polygon((0, 5), square)
    assert not point_in_polygon((11, 5), square)


def test_polygons_intersect():
    a = [(0, 0), (4, 0), (4, 4), (0, 4)]
    b = [(2, 2), (6, 2), (6, 6), (2, 6)]
    c = [(5, 5), (7, 5), (7, 7), (5, 7)]

    assert polygons_intersect(a, b)
    assert not polygons_intersect(a, c)
