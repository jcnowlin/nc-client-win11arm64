# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: GPL-2.0-or-later
from pathlib import Path
import re
import unittest


WORKFLOW = Path(__file__).resolve().parents[2] / "workflows" / "arm64-release.yml"
CACHE_SHA = "55cc8345863c7cc4c66a329aec7e433d2d1c52a9"


class Arm64WorkflowTests(unittest.TestCase):
    def test_pip_legacy_certs_is_inherited_by_all_craft_subprocesses(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        job = re.search(r"(?ms)^  build-release:\n(?P<body>.*?)(?=^  \S|\Z)", text)
        self.assertIsNotNone(job)
        env = re.search(r"(?ms)^    env:\n(?P<body>(?:^      .*\n)+)", job.group("body"))
        self.assertIsNotNone(env)
        self.assertRegex(
            env.group("body"),
            r"(?m)^      PIP_USE_DEPRECATED:\s*legacy-certs\s*$",
        )
