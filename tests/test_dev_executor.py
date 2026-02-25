from __future__ import annotations

import tempfile
import unittest

from services.dev_executor import execute_dev_tasks
from shared.dev_schemas import DevTask


class DevExecutorTests(unittest.TestCase):
    def test_rejects_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tasks = [
                DevTask(
                    id="t1",
                    description="escape scope",
                    command="python -c \"print('x')\"",
                    cwd="../outside",
                )
            ]
            logs, touched, errors = execute_dev_tasks(tasks, scope_root=tmp)
            self.assertEqual(logs, [])
            self.assertEqual(touched, [])
            self.assertTrue(any("[SCOPE]" in e for e in errors), msg=str(errors))

    def test_blocks_git_push(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tasks = [
                DevTask(
                    id="t2",
                    description="push should be blocked",
                    command="git push origin HEAD",
                    cwd=".",
                )
            ]
            _, _, errors = execute_dev_tasks(tasks, scope_root=tmp)
            self.assertTrue(any("[BLOCKED]" in e for e in errors), msg=str(errors))


if __name__ == "__main__":
    unittest.main()
