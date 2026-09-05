"""Positive grammar for a small, finite model-authored migration workflow.

This is an execution-language restriction, NOT a Python security sandbox.
The wider analysis module is diagnostic and is not an authorization oracle.
"""

import ast
from dataclasses import dataclass
import hashlib
from types import CodeType

from .analysis import Assumption, analyze_source


class WorkflowCompileError(ValueError):
    pass


@dataclass(frozen=True)
class CompiledWorkflow:
    source: str
    code: CodeType
    sha256: str
    assumption: Assumption


class WorkflowCompiler:
    """Accept the documented five-statement grammar, with arbitrary local names."""

    def compile(self, source: str) -> CompiledWorkflow:
        if not isinstance(source, str) or len(source.encode()) > 16_384:
            raise WorkflowCompileError("workflow must be a string of at most 16384 UTF-8 bytes")
        try:
            tree = ast.parse(source)
            self._validate(tree)
        except (SyntaxError, RecursionError) as exc:
            raise WorkflowCompileError("invalid workflow syntax") from exc
        assumptions = [a for a in analyze_source(source) if "apply_change" in a.effect_consumers]
        if len(assumptions) != 1 or assumptions[0].loop_consumers != 1:
            raise WorkflowCompileError("expected one semantic value flowing into the migration loop")
        digest = hashlib.sha256(source.encode()).hexdigest()
        return CompiledWorkflow(source, compile(tree, f"<nex:{digest[:12]}>", "exec"), digest, assumptions[0])

    @staticmethod
    def _require(condition: bool, message: str) -> None:
        if not condition:
            raise WorkflowCompileError(message)

    def _call(self, node: ast.AST, name: str, args: int) -> ast.Call:
        self._require(isinstance(node, ast.Call), f"expected {name} call")
        self._require(isinstance(node.func, ast.Name) and node.func.id == name, f"expected {name} call")
        self._require(not node.keywords and len(node.args) == args, f"invalid arguments to {name}")
        return node

    @staticmethod
    def _name(node: ast.AST, name: str) -> bool:
        return isinstance(node, ast.Name) and node.id == name

    def _assignment(self, node: ast.AST) -> tuple[str, ast.AST]:
        self._require(isinstance(node, ast.Assign) and len(node.targets) == 1
                      and isinstance(node.targets[0], ast.Name), "expected a simple local assignment")
        return node.targets[0].id, node.value

    def _validate(self, tree: ast.Module) -> None:
        self._require(len(list(ast.walk(tree))) <= 120, "workflow is too complex")
        self._require(len(tree.body) == 1 and isinstance(tree.body[0], ast.FunctionDef),
                      "module must contain only def migrate(sites)")
        fn = tree.body[0]
        self._require(fn.name == "migrate" and not fn.decorator_list and fn.returns is None
                      and not fn.type_comment and not getattr(fn, "type_params", ()),
                      "decorators, annotations and generic parameters are not supported")
        args = fn.args
        self._require(len(args.args) == 1 and args.args[0].arg == "sites"
                      and args.args[0].annotation is None and not args.posonlyargs
                      and not args.kwonlyargs and args.vararg is None and args.kwarg is None
                      and not args.defaults and not args.kw_defaults,
                      "migrate must take exactly one unannotated argument named sites")
        self._require(len(fn.body) == 5, "expected observe, semantic, for, publish_report, return final_validate")
        obs_name, obs_value = self._assignment(fn.body[0])
        obs = self._call(obs_value, "observe", 1).args[0]
        if not self._name(obs, "sites"):
            self._require(isinstance(obs, ast.Subscript) and self._name(obs.value, "sites")
                          and isinstance(obs.slice, ast.Slice), "observe expects sites or sites[:N]")
            part = obs.slice
            self._require(part.lower is None and part.step is None and isinstance(part.upper, ast.Constant)
                          and type(part.upper.value) is int and 1 <= part.upper.value <= 32,
                          "observation prefix must be a literal integer from 1 to 32")
        decision_name, decision_value = self._assignment(fn.body[1])
        sem = self._call(decision_value, "semantic", 2)
        self._require(self._name(sem.args[0], obs_name) and isinstance(sem.args[1], ast.Constant)
                      and isinstance(sem.args[1].value, str) and len(sem.args[1].value) <= 4096,
                      "semantic expects observations and a literal question")
        loop = fn.body[2]
        self._require(isinstance(loop, ast.For) and isinstance(loop.target, ast.Name)
                      and self._name(loop.iter, "sites") and not loop.orelse
                      and len(loop.body) == 1 and isinstance(loop.body[0], ast.Expr),
                      "expected one finite for-site-in-sites loop with one tool call")
        local_names = {obs_name, decision_name, loop.target.id}
        reserved = {"sites", "migrate", "observe", "semantic", "apply_change", "publish_report", "final_validate"}
        self._require(len(local_names) == 3 and not local_names & reserved
                      and all(not name.startswith("__") for name in local_names),
                      "local names must be distinct and cannot shadow runtime capabilities")
        apply = self._call(loop.body[0].value, "apply_change", 2)
        self._require(self._name(apply.args[0], loop.target.id) and self._name(apply.args[1], decision_name),
                      "apply_change must consume the loop site and semantic decision")
        self._require(isinstance(fn.body[3], ast.Expr), "expected publish_report(sites)")
        publish = self._call(fn.body[3].value, "publish_report", 1)
        self._require(self._name(publish.args[0], "sites"), "publish_report must cover sites")
        self._require(isinstance(fn.body[4], ast.Return), "expected return final_validate()")
        self._call(fn.body[4].value, "final_validate", 0)
