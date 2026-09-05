import pytest

from nex import WorkflowCompileError, WorkflowCompiler
from nex.demo import WORKFLOW


def test_compiler_recovers_fanout():
    compiled = WorkflowCompiler().compile(WORKFLOW)
    assert compiled.assumption.symbolic_fanout == "len(sites)"
    assert compiled.assumption.effect_consumers == ("apply_change",)
    assert len(compiled.sha256) == 64


@pytest.mark.parametrize("source", [
    "import os\n" + WORKFLOW,
    "@semantic([], 'decorate')\n" + WORKFLOW,
    WORKFLOW.replace("migrate(sites)", "migrate(sites=semantic([], 'default'))"),
    WORKFLOW.replace("migrate(sites)", "migrate(sites: semantic([], 'annotation'))"),
    WORKFLOW.replace("migrate(sites)", "migrate(sites, *args)"),
    WORKFLOW.replace("for site in sites:", "while True:"),
    WORKFLOW.replace("for site in sites:", "for site in sites[:2]:"),
    WORKFLOW.replace("apply_change(site, rule)", "open('publication', 'w')"),
    WORKFLOW.replace("apply_change(site, rule)", "site.__class__()"),
    WORKFLOW.replace("apply_change(site, rule)", "apply_change(sites[0], rule)"),
    WORKFLOW.replace("apply_change(site, rule)", "apply_change(site, 'unchecked')"),
    WORKFLOW.replace("rule", "publish_report"),
    WORKFLOW.replace("observations", "sites"),
    WORKFLOW.replace("sites[:2]", "sites[::2]"),
    WORKFLOW.replace("sites[:2]", "sites[:999999]"),
    WORKFLOW.replace("return final_validate()", "return True"),
    WORKFLOW.replace("    publish_report(sites)\n", ""),
    WORKFLOW.replace("    return", "    if False: return"),
    WORKFLOW.replace("apply_change(site, rule)", "apply_change(site, rule); publish_report(sites)"),
    "x = 1",
    "def migrate(:",
    " " * 17000,
])
def test_positive_grammar_rejects_unsafe_or_unsupported_forms(source):
    with pytest.raises(WorkflowCompileError):
        WorkflowCompiler().compile(source)


def test_local_names_are_not_hardcoded():
    program = WORKFLOW.replace("observations", "samples").replace("rule", "decision").replace("site,", "item,")
    program = program.replace("for site in sites", "for item in sites")
    assert WorkflowCompiler().compile(program).assumption.value == "decision"
