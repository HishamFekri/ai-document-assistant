"""Offline static and migration render checks; never import migrations/env.py."""

import ast
from io import StringIO
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def main():
    paths = [*ROOT.joinpath("app").rglob("*.py"), *ROOT.joinpath("migrations").rglob("*.py"),
             *ROOT.joinpath("tests").glob("*.py"), *ROOT.joinpath("scripts").glob("*.py"),
             ROOT / "main.py", ROOT / "runtime.py"]
    for path in paths:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path), feature_version=(3, 11))
        compile(tree, str(path), "exec")
        if ROOT / "app" in path.parents:
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Name) and node.func.id == "print":
                    raise AssertionError(f"Use allowlisted logging instead of print: {path.name}:{node.lineno}")
                if (isinstance(node.func, ast.Attribute) and node.func.attr == "exception"
                        and isinstance(node.func.value, ast.Name) and node.func.value.id in {"logger", "logging"}):
                    raise AssertionError(f"Raw exception logging is forbidden: {path.name}:{node.lineno}")
    print(f"Python 3.11 syntax/static logging checks: {len(paths)} files")

    import sqlalchemy
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory

    # Forbid connections even if a future revision accidentally tries to open one.
    with patch.object(sqlalchemy, "create_engine", side_effect=AssertionError("Offline render only")), \
            patch("socket.socket.connect", side_effect=AssertionError("Offline render only")), \
            patch("socket.create_connection", side_effect=AssertionError("Offline render only")):
        scripts = ScriptDirectory(str(ROOT / "migrations"))
        assert len(scripts.get_heads()) == 1, "Expected one migration head"
        revisions = list(scripts.walk_revisions())
        assert len(revisions) == len(list((ROOT / "migrations/versions").glob("*.py"))), "Unreachable migration"
        for direction, ordered in (("upgrade", list(reversed(revisions))), ("downgrade", revisions)):
            output = StringIO()
            context = MigrationContext.configure(dialect_name="postgresql", opts={
                "as_sql": True, "output_buffer": output, "transaction_per_migration": True,
            })
            with Operations.context(context):
                for revision in ordered:
                    with context.begin_transaction(_per_migration=True):
                        getattr(revision.module, direction)()
            assert output.getvalue().strip(), "Empty SQL render"
        print(f"Migration graph: one head, {len(revisions)} revisions; upgrade/downgrade SQL rendered in memory only")


if __name__ == "__main__":
    main()
