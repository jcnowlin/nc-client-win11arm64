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

    def test_dependency_build_is_resumable_across_six_hour_runner_windows(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(f"actions/cache/restore@{CACHE_SHA}", text)
        self.assertGreaterEqual(text.count(f"actions/cache/save@{CACHE_SHA}"), 5)
        self.assertIn("arm64-deps-${{ env.ARM64_CACHE_FINGERPRINT }}-phase-", text)
        self.assertIn(".arm64-dependency-checkpoints", text)
        for package in (
            "dev-utils/cmake",
            "libs/qt6/qtbase",
            "libs/qt6/qtdeclarative",
            "libs/qt6/qtwebsockets",
            "libs/qt/qtsvg",
            "libs/qt6/qt5compat",
            "libs/libp11",
            "libs/kdsingleapplication",
            "qt-libs/qtkeychain",
            "kde/frameworks/tier1/karchive",
            "libs/openssl",
        ):
            self.assertIn(package, text)

    def test_blueprint_fixes_reach_restored_workspaces(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        reuse = text.index('Write-Host "Reusing restored ARM64 Craft dependency workspace."')
        patch = text.index("patch-kde-blueprints-arm64.py")
        self.assertLess(reuse, patch)
        self.assertIn('"$nextcloud/libs/libp11/libp11.py"', text)

if __name__ == "__main__":
    unittest.main()
