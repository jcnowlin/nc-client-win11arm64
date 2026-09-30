# SPDX-FileCopyrightText: 2026 Dudiebug
# SPDX-License-Identifier: GPL-2.0-or-later

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "patch-kde-blueprints-arm64.py"
spec = spec_from_file_location("patch_kde_blueprints_arm64", SCRIPT)
module = module_from_spec(spec)
spec.loader.exec_module(module)

LIBJPEG = (
    "import info\n"
    "from Package.CMakePackageBase import CMakePackageBase\n"
    "from Utils import CraftHash\n\n"
    "class Package(CMakePackageBase):\n"
    "    def __init__(self, **kwargs):\n"
    "        super().__init__(**kwargs)\n"
    "        if self.subinfo.options.buildStatic:\n"
    "            self.subinfo.options.configure.args += [\"-DENABLE_SHARED=OFF\", \"-DENABLE_STATIC=ON\"]\n"
    "        else:\n"
    "            self.subinfo.options.configure.args += [\"-DENABLE_SHARED=ON\", \"-DENABLE_STATIC=OFF\"]\n"
)

PIXMAN = (
    "import info\n"
    "from CraftCore import CraftCore\n"
    "from Package.MesonPackageBase import MesonPackageBase\n\n"
    "class Package(MesonPackageBase):\n"
    "    def __init__(self, **kwargs):\n"
    "        super().__init__(**kwargs)\n"
    "        if CraftCore.compiler.isMacOS:\n"
    "            self.subinfo.options.configure.args += [\"-Da64-neon=disabled\"]\n"
)

LIBP11 = (
    "class PackageMake(MakeFilePackageBase):\n"
    "    def __init__(self, **kwargs):\n"
    "        super().__init__(**kwargs)\n"
    "        self.subinfo.options.make.args += f\"/f Makefile.mak OPENSSL_DIR=\" + str(CraftCore.standardDirs.craftRoot())\n"
    "        if CraftCore.compiler.architecture == CraftCompiler.Architecture.x86_64:\n"
    "             self.subinfo.options.make.args += f\" BUILD_FOR=WIN64\"\n"
    "\n"
    "    def install(self):\n"
    "        pass\n"
)


class KdeBlueprintPatchTests(unittest.TestCase):
    @staticmethod
    def _write(path, source, newline):
        path.write_bytes(source.replace("\n", newline).encode("utf-8"))

    def test_libjpeg_crlf_checkout_patches_and_preserves_crlf(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "libjpeg-turbo.py"
            self._write(path, LIBJPEG, "\r\n")
            module.patch_libjpeg(path)
            module.patch_libjpeg(path)
            data = path.read_bytes()
            self.assertIn(b"-DWITH_SIMD=OFF", data)
            self.assertIn(b"from CraftCompiler import CraftCompiler\r\n", data)
            self.assertNotIn(b"\n", data.replace(b"\r\n", b""))

    def test_pixman_crlf_checkout_patches_and_preserves_crlf(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pixman.py"
            self._write(path, PIXMAN, "\r\n")
            module.patch_pixman(path)
            module.patch_pixman(path)
            data = path.read_bytes()
            self.assertIn(b"-Dmmx=disabled", data)
            self.assertIn(b"-Dsse2=disabled", data)
            self.assertIn(b"-Dssse3=disabled", data)
            self.assertNotIn(b"\n", data.replace(b"\r\n", b""))

    def test_lf_checkout_remains_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            libjpeg = Path(directory) / "libjpeg-turbo.py"
            pixman = Path(directory) / "pixman.py"
            self._write(libjpeg, LIBJPEG, "\n")
            self._write(pixman, PIXMAN, "\n")
            module.patch_libjpeg(libjpeg)
            module.patch_pixman(pixman)
            self.assertNotIn(b"\r\n", libjpeg.read_bytes())
            self.assertNotIn(b"\r\n", pixman.read_bytes())

    def test_libp11_links_arm64_instead_of_x86(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "libp11.py"
            self._write(path, LIBP11, "\r\n")
            module.patch_libp11(path)
            module.patch_libp11(path)
            text = path.read_bytes().decode("utf-8")
            self.assertEqual(text.count("MACHINE=/MACHINE:ARM64"), 1)
            self.assertIn("elif CraftCore.compiler.architecture == CraftCompiler.Architecture.arm64:", text)
            self.assertIn("BUILD_FOR=WIN64", text)
            self.assertNotIn("\n", text.replace("\r\n", ""))
            compile(text.replace("\r\n", "\n"), str(path), "exec")

    def test_libp11_unexpected_upstream_layout_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "libp11.py"
            self._write(path, "class PackageMake:\n    pass\n", "\n")
            with self.assertRaises(RuntimeError):
                module.patch_libp11(path)


if __name__ == "__main__":
    unittest.main()
