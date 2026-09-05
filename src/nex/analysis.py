"""Infer speculative assumptions from Python dataflow, without model self-report."""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from pathlib import Path

SEMANTIC_NAMES = {"semantic", "infer_pattern", "ask_model", "llm"}
EFFECT_NAMES = {
    "run_tests",
    "apply_pattern",
    "rewrite_call",
    "update_config",
    "rewrite_import",
    "normalize_record",
    "update_policy",
    "update_client",
    "apply_change",
    "publish_report",
    "write_file",
    "post",
    "push",
    "commit",
}
# Stable tool contracts mark the second positional argument as semantic. The
# compiler needs this reusable typing information, not per-task model hints.
SEMANTIC_EFFECT_ARG = {
    "apply_pattern": 1,
    "rewrite_call": 1,
    "update_config": 1,
    "rewrite_import": 1,
    "normalize_record": 1,
    "update_policy": 1,
    "update_client": 1,
    "apply_change": 1,
}
SEMANTIC_EFFECT_KEYWORD = {
    "apply_pattern": "pattern",
    "rewrite_call": "migration",
    "update_config": "migration",
    "rewrite_import": "migration",
    "normalize_record": "rule",
    "update_policy": "rule",
    "update_client": "rule",
    "apply_change": "decision",
}
LOOP_NODES = (ast.For, ast.AsyncFor, ast.While, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return "<dynamic>"


@dataclass(frozen=True)
class Assumption:
    value: str
    producer: str
    source_line: int
    producer_loop_depth: int
    static_consumers: int
    loop_consumers: int
    symbolic_fanout: str
    effect_consumers: tuple[str, ...]


class Analyzer(ast.NodeVisitor):
    def __init__(self) -> None:
        self.semantic_values: dict[str, tuple[str, int, tuple[ast.AST, ...]]] = {}
        self.uses: dict[str, list[tuple[ast.Name, tuple[ast.AST, ...]]]] = {}
        self.definitions: dict[str, list[int]] = {}
        self.stack: list[ast.AST] = []

    def visit(self, node: ast.AST) -> None:  # type: ignore[override]
        self.stack.append(node)
        super().visit(node)
        self.stack.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            for name in _target_names(target):
                self.definitions.setdefault(name, []).append(node.lineno)
        if isinstance(node.value, ast.Call) and _call_name(node.value) in SEMANTIC_NAMES:
            loops = tuple(x for x in self.stack if isinstance(x, LOOP_NODES))
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.semantic_values[target.id] = (_call_name(node.value), node.lineno, loops)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        for name in _target_names(node.target):
            self.definitions.setdefault(name, []).append(node.lineno)
        if isinstance(node.target, ast.Name) and isinstance(node.value, ast.Call):
            if _call_name(node.value) in SEMANTIC_NAMES:
                loops = tuple(x for x in self.stack if isinstance(x, LOOP_NODES))
                self.semantic_values[node.target.id] = (_call_name(node.value), node.lineno, loops)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.uses.setdefault(node.id, []).append((node, tuple(self.stack)))


def analyze_source(source: str) -> list[Assumption]:
    tree = ast.parse(source)
    analyzer = Analyzer()
    analyzer.visit(tree)
    results: list[Assumption] = []
    for value, (producer, line, producer_loops) in analyzer.semantic_values.items():
        uses = analyzer.uses.get(value, [])
        loop_uses = 0
        effect_consumers: set[str] = set()
        loop_symbols: list[str] = []
        for use, ancestors in uses:
            # A later assignment shadows the semantic result on this path.
            if any(line < definition <= use.lineno for definition in analyzer.definitions.get(value, [])):
                continue
            effect = _semantic_effect_for_use(use, ancestors)
            if effect is None:
                continue
            loops = [
                x
                for x in ancestors
                if isinstance(x, LOOP_NODES) and x not in producer_loops
            ]
            if loops:
                loop_uses += 1
                loop = loops[-1]
                if isinstance(loop, (ast.For, ast.AsyncFor)):
                    loop_symbols.append(f"len({ast.unparse(loop.iter)})")
                elif isinstance(loop, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                    loop_symbols.append(f"len({ast.unparse(loop.generators[0].iter)})")
                else:
                    loop_symbols.append("iterations(while)")
            effect_consumers.add(effect)
        symbolic = " + ".join(sorted(set(loop_symbols))) or str(len(uses))
        results.append(
            Assumption(
                value=value,
                producer=producer,
                source_line=line,
                producer_loop_depth=len(producer_loops),
                static_consumers=len(uses),
                loop_consumers=loop_uses,
                symbolic_fanout=symbolic,
                effect_consumers=tuple(sorted(effect_consumers)),
            )
        )
    results.extend(_infer_model_authored_effect_values(tree, analyzer.semantic_values))
    return results


def _semantic_effect_for_use(use: ast.Name, ancestors: tuple[ast.AST, ...]) -> str | None:
    """Return the contract-typed effect receiving this exact value use."""
    for ancestor in reversed(ancestors):
        if not isinstance(ancestor, ast.Call):
            continue
        effect = _call_name(ancestor)
        semantic_arg = _semantic_effect_argument(ancestor)
        if semantic_arg is None:
            continue
        if any(node is use for node in ast.walk(semantic_arg)):
            return effect
    return None


def _semantic_effect_argument(node: ast.Call) -> ast.AST | None:
    effect = _call_name(node)
    index = SEMANTIC_EFFECT_ARG.get(effect)
    if index is not None and len(node.args) > index:
        return node.args[index]
    keyword = SEMANTIC_EFFECT_KEYWORD.get(effect)
    if keyword is not None:
        for item in node.keywords:
            if item.arg == keyword:
                return item.value
    return None


def _target_names(node: ast.AST) -> set[str]:
    return {x.id for x in ast.walk(node) if isinstance(x, ast.Name)}


def _infer_model_authored_effect_values(
    tree: ast.AST,
    semantic_values: dict[str, tuple[str, int, tuple[ast.AST, ...]]],
) -> list[Assumption]:
    """Find loop-invariant semantic effect arguments authored by the model.

    Example: ``for site in sites: rewrite_call(site, "client.send(data=)")``.
    The literal is a semantic prediction even though there is no runtime LLM
    call. Its type comes from the reusable ``rewrite_call`` tool contract.
    """
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    inferred: list[Assumption] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        effect = _call_name(node)
        semantic_arg = _semantic_effect_argument(node)
        if semantic_arg is None:
            continue
        current: ast.AST | None = node
        loop: ast.AST | None = None
        while current in parents:
            current = parents[current]
            if isinstance(current, LOOP_NODES):
                loop = current
                break
        if loop is None:
            continue

        if isinstance(semantic_arg, ast.Name) and semantic_arg.id in semantic_values:
            continue
        variant_names = _loop_variant_names(loop)
        arg_names = {x.id for x in ast.walk(semantic_arg) if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)}
        # Expressions derived from a tracked semantic result are already
        # represented by that result's dataflow assumption. Counting the
        # derived expression again as model-authored would duplicate one
        # prediction (for example, result["migration"]).
        if arg_names & semantic_values.keys():
            continue
        if arg_names & variant_names:
            continue
        if isinstance(loop, (ast.For, ast.AsyncFor)):
            fanout = f"len({ast.unparse(loop.iter)})"
        elif isinstance(loop, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            fanout = f"len({ast.unparse(loop.generators[0].iter)})"
        else:
            fanout = "iterations(while)"
        inferred.append(
            Assumption(
                value=ast.unparse(semantic_arg),
                producer="model-authored",
                source_line=node.lineno,
                producer_loop_depth=0,
                static_consumers=1,
                loop_consumers=1,
                symbolic_fanout=fanout,
                effect_consumers=(effect,),
            )
        )
    inferred.extend(_infer_helper_effect_values(tree, parents, semantic_values))
    # Helper summaries and direct traversal can converge on the same effect.
    unique: dict[tuple[int, str, str], Assumption] = {}
    for item in inferred:
        unique[(item.source_line, item.value, item.effect_consumers[0])] = item
    return list(unique.values())


def _loop_variant_names(loop: ast.AST) -> set[str]:
    variants: set[str] = set()
    if isinstance(loop, (ast.For, ast.AsyncFor)):
        variants |= _target_names(loop.target)
    elif isinstance(loop, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        for generator in loop.generators:
            variants |= _target_names(generator.target)
    changed = True
    while changed:
        changed = False
        for inner in ast.walk(loop):
            targets: set[str] = set()
            value: ast.AST | None = None
            force_variant = False
            if isinstance(inner, ast.Assign):
                for target in inner.targets:
                    targets |= _target_names(target)
                value = inner.value
            elif isinstance(inner, ast.AnnAssign):
                targets = _target_names(inner.target)
                value = inner.value
            elif isinstance(inner, ast.NamedExpr):
                targets = _target_names(inner.target)
                value = inner.value
            elif isinstance(inner, ast.AugAssign):
                targets = _target_names(inner.target)
                value = inner.value
                force_variant = True
            if targets and (force_variant or _loaded_names(value) & variants):
                before = len(variants)
                variants |= targets
                changed |= len(variants) != before
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                receiver = inner.func.value
                if isinstance(receiver, ast.Name) and any(_loaded_names(arg) & variants for arg in inner.args):
                    before = len(variants)
                    variants.add(receiver.id)
                    changed |= len(variants) != before
    return variants


def _loaded_names(node: ast.AST | None) -> set[str]:
    if node is None:
        return set()
    return {
        item.id
        for item in ast.walk(node)
        if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)
    }


def _infer_helper_effect_values(
    tree: ast.AST,
    parents: dict[ast.AST, ast.AST],
    semantic_values: dict[str, tuple[str, int, tuple[ast.AST, ...]]],
) -> list[Assumption]:
    """Summarize simple helper calls that are dynamically repeated by a loop."""
    helpers = {node.name: node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    inferred: list[Assumption] = []
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        helper = helpers.get(_call_name(call))
        if helper is None:
            continue
        current: ast.AST | None = call
        loop: ast.AST | None = None
        while current in parents:
            current = parents[current]
            if isinstance(current, LOOP_NODES):
                loop = current
                break
        if loop is None:
            continue
        variants = _loop_variant_names(loop)
        params = [arg.arg for arg in helper.args.args]
        bindings = {name: call.args[index] for index, name in enumerate(params) if index < len(call.args)}
        local_constants: set[str] = set()
        for node in ast.walk(helper):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
                if value is not None and not _loaded_names(value):
                    for target in targets:
                        local_constants |= _target_names(target)
        for effect_call in (node for node in ast.walk(helper) if isinstance(node, ast.Call)):
            effect = _call_name(effect_call)
            semantic_arg = _semantic_effect_argument(effect_call)
            if semantic_arg is None:
                continue
            dependencies = _loaded_names(semantic_arg) - local_constants
            variant = False
            for dependency in dependencies:
                if dependency in bindings:
                    if _loaded_names(bindings[dependency]) & variants:
                        variant = True
                elif dependency in params:
                    variant = True
                elif dependency in variants:
                    variant = True
            if variant:
                continue
            if any(name in semantic_values for name in dependencies):
                producer = "semantic"
            else:
                producer = "model-authored"
            if isinstance(loop, (ast.For, ast.AsyncFor)):
                fanout = f"len({ast.unparse(loop.iter)})"
            elif isinstance(loop, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                fanout = f"len({ast.unparse(loop.generators[0].iter)})"
            else:
                fanout = "iterations(while)"
            inferred.append(
                Assumption(
                    value=ast.unparse(semantic_arg),
                    producer=producer,
                    source_line=effect_call.lineno,
                    producer_loop_depth=0,
                    static_consumers=1,
                    loop_consumers=1,
                    symbolic_fanout=fanout,
                    effect_consumers=(effect,),
                )
            )
    return inferred


def analyze_file(path: Path) -> list[dict[str, object]]:
    return [asdict(x) for x in analyze_source(path.read_text(encoding="utf-8"))]
