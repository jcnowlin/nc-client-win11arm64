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


# Minimal excerpt of upstream blueprints/dev-utils/kshimgen/kshimgen.py
# carrying the import block and the tail of the non-Android Package class
# that Patch 10 targets.
KSHIMGEN_SNIPPET = '''\
import os

import info
import utils
from CraftCore import CraftCore
from Package.BinaryPackageBase import BinaryPackageBase
from Package.CMakePackageBase import CMakePackageBase


class subinfo(info.infoclass):
    def registerOptions(self):
        pass


if CraftCore.compiler.isAndroid:

    class Package(BinaryPackageBase):
        pass

else:

    class Package(CMakePackageBase):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)

        def configure(self):
            cmakePath = CraftCore.standardDirs.craftRoot() / "dev-utils/cmake-base"
            path = f"{cmakePath}{os.pathsep}{os.environ['PATH']}"
            with utils.ScopedEnv({"PATH": path}):
                return super().configure()
'''


class PatchKshimgenHostBuildTests(unittest.TestCase):
    """Regression tests for Patch 10 of patch-craft-arm64.py.

    kshimgen is a host tool: Craft executes kshimgen.exe via
    utils.createShim (dev-utils/cmake's install() does this). When
    cross-compiling Windows ARM64 from x64, an ARM64 kshimgen.exe cannot
    run on the x64 runner (WinError 216), so it must be built for x64.
    """

    def test_patch_applies_and_rewires_configure(self):
        result = module.patch_kshimgen_host_build(KSHIMGEN_SNIPPET)
        self.assertIn("import platform\nimport re", result)
        self.assertIn("def _is_windows_arm64_cross():", result)
        self.assertIn("def _x64_host_tool_env(path):", result)
        self.assertIn("_is_windows_arm64_cross()", result)
        self.assertIn("building for x64 host", result)
        # Old native-only configure() must be gone.
        self.assertNotIn('with utils.ScopedEnv({"PATH": path}):', result)

    def test_upstream_shape_change_is_rejected(self):
        with self.assertRaises(ValueError):
            module.patch_kshimgen_host_build("import os\nclass Package:\n    pass\n")

    def _load_helpers(self, *, machine, is_windows, arch, path, lib, cl_exists):
        """Exec the exact injected helper code with mocked os/platform/CraftCore."""
        import os as real_os
        import re as real_re

        class FakeEnviron(dict):
            pass

        fake_os = type("os", (), {})()
        fake_os.environ = {"PATH": path, "LIB": lib}
        fake_os.pathsep = ";"  # windows semantics: the code runs on a Windows runner
        fake_os.path = type("path", (), {})()
        fake_os.path.join = lambda a, b: a + "\\" + b
        fake_os.path.isfile = lambda p: cl_exists

        fake_platform = type("platform", (), {})()
        fake_platform.machine = lambda: machine

        class FakeArch:
            arm64 = "arm64"
            x86_64 = "x86_64"

        class FakeCompiler:
            isWindows = is_windows
            architecture = arch
            Architecture = FakeArch

        fake_craftcore = type("CraftCore", (), {})()
        fake_craftcore.compiler = FakeCompiler

        namespace = {
            "os": fake_os,
            "re": real_re,
            "platform": fake_platform,
            "CraftCore": fake_craftcore,
        }
        exec(module._KSHIMGEN_HOST_HELPERS, namespace)
        return namespace

    def test_host_env_derived_from_cross_path(self):
        vs = r"C:\Program Files\Microsoft Visual Studio\18\Community\VC\Tools\MSVC\14.51.36231"
        arm_bin = vs + r"\bin\HostX64\arm64"
        path = arm_bin + r";C:\Windows\System32"
        lib = vs + r"\lib\arm64" + r";C:\WinKits\Lib\ucrt\arm64"
        ns = self._load_helpers(
            machine="AMD64", is_windows=True, arch="arm64",
            path=path, lib=lib, cl_exists=True,
        )
        self.assertTrue(ns["_is_windows_arm64_cross"]())
        env = ns["_x64_host_tool_env"](path)
        x64bin = vs + r"\bin\HostX64\x64"
        self.assertEqual(env["CC"], x64bin + r"\cl.exe")
        self.assertEqual(env["CXX"], x64bin + r"\cl.exe")
        self.assertTrue(env["PATH"].startswith(x64bin + ";"))
        self.assertIn(vs + r"\lib\x64", env["LIB"])
        self.assertIn(r"C:\WinKits\Lib\ucrt\x64", env["LIB"])
        self.assertNotIn("arm64", env["LIB"])

    def test_host_env_empty_when_toolchain_not_found(self):
        ns = self._load_helpers(
            machine="AMD64", is_windows=True, arch="arm64",
            path=r"C:\Windows\System32", lib="", cl_exists=True,
        )
        self.assertEqual(ns["_x64_host_tool_env"](r"C:\Windows\System32"), {})

    def test_host_env_empty_when_cl_missing(self):
        vs = r"C:\VS\VC\Tools\MSVC\14.51.36231"
        path = vs + r"\bin\HostX64\arm64" + r";C:\Windows"
        ns = self._load_helpers(
            machine="AMD64", is_windows=True, arch="arm64",
            path=path, lib="", cl_exists=False,
        )
        self.assertEqual(ns["_x64_host_tool_env"](path), {})

    def test_not_cross_when_target_is_native(self):
        ns = self._load_helpers(
            machine="AMD64", is_windows=True, arch="x86_64",
            path=r"C:\VS\bin\HostX64\x64", lib="", cl_exists=True,
        )
        self.assertFalse(ns["_is_windows_arm64_cross"]())


if __name__ == "__main__":
    unittest.main()
