from nex.analysis import analyze_source


def test_infers_loop_fanout_from_dataflow() -> None:
    source = """
def migrate(failures):
    pattern = infer_pattern(failures[:3])
    for failure in failures:
        apply_pattern(failure, pattern)
    run_tests()
"""
    assumptions = analyze_source(source)
    assert len(assumptions) == 1
    assert assumptions[0].value == "pattern"
    assert assumptions[0].symbolic_fanout == "len(failures)"
    assert assumptions[0].effect_consumers == ("apply_pattern",)


def test_semantic_value_created_inside_loop_is_not_global_fanout() -> None:
    source = """
for failure in failures:
    pattern = infer_pattern([failure])
    apply_pattern(failure, pattern)
"""
    assumption = analyze_source(source)[0]
    assert assumption.producer_loop_depth == 1
    assert assumption.loop_consumers == 0
    assert assumption.symbolic_fanout == "1"


def test_infers_model_authored_semantic_literal_from_tool_contract() -> None:
    source = """
for site in failures:
    rewrite_call(site, "client.send(data=)")
run_tests()
"""
    assumption = analyze_source(source)[0]
    assert assumption.producer == "model-authored"
    assert assumption.symbolic_fanout == "len(failures)"
    assert assumption.effect_consumers == ("rewrite_call",)


def test_does_not_quantify_item_dependent_effect_argument() -> None:
    source = """
for path in policies:
    rule = inspect_policy(path)
    update_policy(path, rule)
"""
    assert analyze_source(source) == []


def test_semantic_derived_expression_is_not_double_counted() -> None:
    source = """
def solve(items):
    result = semantic(items[:3], "infer one migration")
    for item in items:
        update_config(item, result["migration"])
"""
    assumptions = analyze_source(source)
    assert len(assumptions) == 1
    assert assumptions[0].producer == "semantic"
    assert assumptions[0].loop_consumers > 0
