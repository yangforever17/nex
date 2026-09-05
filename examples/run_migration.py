"""Run with `python examples/run_migration.py` after installing the package."""

from pathlib import Path
import tempfile

from nex import PublicationLedger, Runtime, WorkflowCompiler
from nex.demo import DemoProvider, JsonMigrationAdapter, WORKFLOW


def main():
    with tempfile.TemporaryDirectory(prefix="nex-example-") as directory:
        root = Path(directory)
        adapter = JsonMigrationAdapter(root / "workspace")
        session = Runtime(WorkflowCompiler().compile(WORKFLOW), adapter, DemoProvider(),
                          PublicationLedger(root / "publication.sqlite"))
        result = session.execute()
        assert result.success, result.error
        for event in result.events:
            if event["event"] in {"exception", "resume", "publication_committed"}:
                print(event)
        print(result.metrics)


if __name__ == "__main__":
    main()
