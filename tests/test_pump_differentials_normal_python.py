"""Optimized Python must not remove Pump differential transaction checks."""

import ast
from pathlib import Path
import os
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = tuple(
    ROOT / "scripts" / name
    for name in (
        "pump_claim_token_incentives_differential.py",
        "pump_create_control_differential.py",
        "pump_create_differential.py",
        "pump_create_quote_differential.py",
        "pump_create_v2_differential.py",
        "pump_creator_collect_v2_differential.py",
        "pump_extend_differential.py",
        "pump_fee_sweep_differential.py",
        "pump_idl_authority_differential.py",
        "pump_incentives_admin_differential.py",
        "pump_set_creator_differential.py",
        "pump_set_params_differential.py",
        "pump_virtual_reserve_differential.py",
        "pump_volume_sync_differential.py",
    )
)


def parse(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


class PumpDifferentialNormalPythonTests(unittest.TestCase):
    def test_guards_precede_imports(self):
        for script in SCRIPTS:
            with self.subTest(script=script.name):
                tree = parse(script)
                guard = next(
                    node
                    for node in tree.body
                    if isinstance(node, ast.If)
                    and isinstance(node.test, ast.UnaryOp)
                    and isinstance(node.test.op, ast.Not)
                    and isinstance(node.test.operand, ast.Name)
                    and node.test.operand.id == "__debug__"
                )
                first_import = next(
                    node.lineno
                    for node in tree.body
                    if isinstance(node, (ast.Import, ast.ImportFrom))
                )
                self.assertLess(guard.lineno, first_import)

    def test_optimized_modes_exit_before_program_imports(self):
        environment = dict(os.environ)
        environment.pop("PYTHONOPTIMIZE", None)
        optimized_environment = dict(environment)
        optimized_environment["PYTHONOPTIMIZE"] = "1"
        invocations = (
            (("-O",), environment),
            (("-OO",), environment),
            ((), optimized_environment),
        )
        for script in SCRIPTS:
            for flags, env in invocations:
                with self.subTest(
                    script=script.name,
                    flags=flags,
                    optimize=env.get("PYTHONOPTIMIZE"),
                ):
                    result = subprocess.run(
                        [sys.executable, *flags, str(script), "--help"],
                        cwd=ROOT,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("Normal Python is required", result.stderr)
                    self.assertNotIn("ModuleNotFoundError", result.stderr)


if __name__ == "__main__":
    unittest.main()
