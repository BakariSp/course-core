from ex1 import bottleneck


def test_picks_slowest_link_in_a_three_link_path():
    links = [
        ("api-server -> cloud-edge", 200),
        ("cloud-edge -> llm-upstream", 100),
        ("llm-upstream -> model-host", 1000),
    ]
    assert bottleneck(links) == ("cloud-edge -> llm-upstream", 100)


def test_order_of_the_list_does_not_matter():
    links = [("c", 50), ("a", 500), ("b", 20)]
    assert bottleneck(links) == ("b", 20)


def test_single_link_is_its_own_bottleneck():
    assert bottleneck([("only-link", 42)]) == ("only-link", 42)


def test_two_links_with_the_same_lowest_rate_returns_the_first_one():
    links = [("first-slow", 10), ("fast", 1000), ("second-slow", 10)]
    assert bottleneck(links) == ("first-slow", 10)


def test_slowest_link_sits_at_the_end():
    links = [("a", 1000), ("b", 300), ("c", 5)]
    assert bottleneck(links) == ("c", 5)
