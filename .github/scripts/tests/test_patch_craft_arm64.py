# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Regression tests for Patch 1 of patch-craft-arm64.py.

Patch 1 adds ARM64 entries to the vcvarsall architecture dicts in
CraftSetupHelper.getMSVCEnv. The non-native entry selects the
"<host>_<target>" cross compiler used on x64 CI runners; it must be
"amd64_arm64" (x64-hosted). "x86_arm64" selects the x86-hosted toolchain,
whose ARM64 cross tools are absent from current runner images, so CMake
silently fell back to the x86 compiler and the link failed with LNK1104.
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "patch-craft-arm64.py"
spec = spec_from_file_location("patch_craft_arm64", SCRIPT)
module = module_from_spec(spec)
spec.loader.exec_module(module)

# Minimal excerpt of upstream CraftSetupHelper.getMSVCEnv carrying the two
# architecture dicts Patch 1 targets.
UPSTREAM_SNIPPET = '''\
    def getMSVCEnv(version=0, architecture=None, toolset=None, native=True):
        if native:
            architectures = {
                CraftCore.compiler.Architecture.x86_32: "amd64_x86",
                CraftCore.compiler.Architecture.x86_64: "amd64",
            }
        else:
            architectures = {
                CraftCore.compiler.Architecture.x86_32: "x86",
                CraftCore.compiler.Architecture.x86_64: "x86_amd64",
            }
        args = architectures[architecture]
'''


class PatchMsvcArchitecturesTests(unittest.TestCase):
    def test_native_arm64_uses_arm64_hosted_toolchain(self):
        result = module.patch_msvc_architectures(UPSTREAM_SNIPPET)
        self.assertIn('CraftCore.compiler.Architecture.arm64: "arm64",', result)

    def test_cross_compile_arm64_uses_x64_hosted_toolchain(self):
        # Regression test: the cross compiler for x64 CI runners must be
        # amd64_arm64, never x86_arm64.
        result = module.patch_msvc_architectures(UPSTREAM_SNIPPET)
        self.assertIn('CraftCore.compiler.Architecture.arm64: "amd64_arm64",', result)
        self.assertNotIn('"x86_arm64"', result)

    def test_existing_entries_are_preserved(self):
        result = module.patch_msvc_architectures(UPSTREAM_SNIPPET)
        for entry in ('"amd64_x86"', '"amd64"', '"x86"', '"x86_amd64"'):
            self.assertIn(entry, result)

    def test_upstream_shape_change_is_rejected(self):
        with self.assertRaises(ValueError):
            module.patch_msvc_architectures("def getMSVCEnv():\n    pass\n")


if __name__ == "__main__":
    unittest.main()
