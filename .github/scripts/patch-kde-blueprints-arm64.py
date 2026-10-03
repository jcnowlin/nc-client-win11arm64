#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Dudiebug
# SPDX-License-Identifier: GPL-2.0-or-later

"""Apply Windows ARM64 fixes to stable Nextcloud/KDE Craft blueprints."""

import sys


def _read_normalized(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        source = f.read()
    line_ending = "\r\n" if "\r\n" in source else "\n"
    return source.replace("\r\n", "\n"), line_ending


def _write_preserving_line_endings(path, source, line_ending):
    if line_ending == "\r\n":
        source = source.replace("\n", "\r\n")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(source)


def patch_libjpeg(path):
    s, line_ending = _read_normalized(path)

    if "-DWITH_SIMD=OFF" in s:
        print(f"SKIP {path}: ARM64 SIMD patch already present")
        return

    old_imports = "import info\nfrom Package.CMakePackageBase import CMakePackageBase\nfrom Utils import CraftHash\n"
    new_imports = "import info\nfrom CraftCompiler import CraftCompiler\nfrom CraftCore import CraftCore\nfrom Package.CMakePackageBase import CMakePackageBase\nfrom Utils import CraftHash\n"
    if old_imports not in s:
        raise RuntimeError("libjpeg-turbo import block changed upstream")
    s = s.replace(old_imports, new_imports)

    needle = (
        '        else:\n'
        '            self.subinfo.options.configure.args += ["-DENABLE_SHARED=ON", "-DENABLE_STATIC=OFF"]\n'
    )
    replacement = needle + (
        '        if CraftCore.compiler.isWindows and CraftCore.compiler.architecture == CraftCompiler.Architecture.arm64:\n'
        '            self.subinfo.options.configure.args += ["-DWITH_SIMD=OFF"]\n'
    )
    if needle not in s:
        raise RuntimeError("libjpeg-turbo Package block changed upstream")
    s = s.replace(needle, replacement, 1)

    _write_preserving_line_endings(path, s, line_ending)
    print(f"Patched {path}: disabled x86 SIMD for Windows ARM64")


def patch_pixman(path):
    s, line_ending = _read_normalized(path)

    if "-Dmmx=disabled" in s and "-Dsse2=disabled" in s and "-Dssse3=disabled" in s:
        print(f"SKIP {path}: Windows ARM64 SIMD patch already present")
        return

    needle = (
        'class Package(MesonPackageBase):\n'
        '    def __init__(self, **kwargs):\n'
        '        super().__init__(**kwargs)\n'
    )
    replacement = needle + (
        '        from CraftCompiler import CraftCompiler\n'
        '        if CraftCore.compiler.isWindows and CraftCore.compiler.architecture == CraftCompiler.Architecture.arm64:\n'
        '            self.subinfo.options.configure.args += [\n'
        '                "-Da64-neon=disabled",\n'
        '                "-Dmmx=disabled",\n'
        '                "-Dsse2=disabled",\n'
        '                "-Dssse3=disabled",\n'
        '            ]\n'
    )
    if needle not in s:
        raise RuntimeError("pixman Package block changed upstream")
    s = s.replace(needle, replacement, 1)

    _write_preserving_line_endings(path, s, line_ending)
    print(f"Patched {path}: disabled unsupported SIMD paths for Windows ARM64")


def patch_libp11(path):
    s, line_ending = _read_normalized(path)

    if "/MACHINE:ARM64" in s:
        print(f"SKIP {path}: Windows ARM64 linker machine patch already present")
        return

    # libp11's make.rules.mak links with /MACHINE:X86 unless BUILD_FOR=WIN64.
    # An nmake command-line macro overrides the makefile's MACHINE definition.
    needle = (
        '        if CraftCore.compiler.architecture == CraftCompiler.Architecture.x86_64:\n'
        '             self.subinfo.options.make.args += f" BUILD_FOR=WIN64"\n'
    )
    replacement = needle + (
        '        elif CraftCore.compiler.architecture == CraftCompiler.Architecture.arm64:\n'
        '             self.subinfo.options.make.args += " MACHINE=/MACHINE:ARM64"\n'
    )
    if needle not in s:
        raise RuntimeError("libp11 nmake architecture block changed upstream")
    s = s.replace(needle, replacement, 1)

    _write_preserving_line_endings(path, s, line_ending)
    print(f"Patched {path}: link libp11 for Windows ARM64")




def bootstrap_zlib():
    """Pre-build zlib.lib so Craft's Python build can link it.

    Craft resolves libs/python before libs/zlib, but CPython's Windows build
    links <craft-root>/lib/zlib.lib (see KDE/craft blueprints/libs/python).
    Building zlib 1.3.1 here with CMake breaks the cycle; Craft rebuilds and
    reinstalls it properly afterwards via libs/zlib.
    """
    import os
    import shutil
    import subprocess
    import tarfile
    import urllib.request

    github_workspace = os.environ.get("GITHUB_WORKSPACE")
    craft_target = os.environ.get("CRAFT_TARGET")
    if not github_workspace or not craft_target:
        print("SKIP zlib bootstrap: GITHUB_WORKSPACE or CRAFT_TARGET not set")
        return

    craft_root = os.path.join(github_workspace, craft_target)
    zlib_lib = os.path.join(craft_root, "lib", "zlib.lib")
    if os.path.exists(zlib_lib):
        print(f"SKIP zlib bootstrap: {zlib_lib} already exists")
        return

    # Need CMake from the runner (Craft's own cmake is built later).
    if shutil.which("cmake") is None:
        raise RuntimeError("zlib bootstrap requires cmake on PATH")

    zlib_ver = "1.3.1"
    runner_temp = os.environ.get("RUNNER_TEMP", "/tmp")
    tgz_path = os.path.join(runner_temp, f"zlib-{zlib_ver}.tar.gz")
    src_dir = os.path.join(runner_temp, "zlib-bootstrap-src")
    build_dir = os.path.join(runner_temp, "zlib-bootstrap-build")

    print(f"Bootstrapping zlib {zlib_ver} (breaks python->zlib build cycle)...")
    url = f"https://github.com/madler/zlib/releases/download/v{zlib_ver}/zlib-{zlib_ver}.tar.gz"
    urllib.request.urlretrieve(url, tgz_path)

    shutil.rmtree(src_dir, ignore_errors=True)
    os.makedirs(src_dir, exist_ok=True)
    with tarfile.open(tgz_path, "r:gz") as tf:
        tf.extractall(src_dir)
    extracted = os.path.join(src_dir, f"zlib-{zlib_ver}")
    if not os.path.isdir(extracted):
        raise RuntimeError(f"zlib source not found at {extracted}")

    shutil.rmtree(build_dir, ignore_errors=True)
    os.makedirs(build_dir, exist_ok=True)
    subprocess.run(
        [
            "cmake", "-S", extracted, "-B", build_dir,
            "-G", "Visual Studio 18 2026", "-A", "ARM64",
            f"-DCMAKE_INSTALL_PREFIX={craft_root}",
            "-DBUILD_SHARED_LIBS=ON",
        ],
        check=True,
    )
    subprocess.run(["cmake", "--build", build_dir, "--config", "Release"], check=True)
    subprocess.run(["cmake", "--install", build_dir, "--config", "Release"], check=True)

    if not os.path.exists(zlib_lib):
        raise RuntimeError(f"zlib bootstrap failed: {zlib_lib} not created")
    print("zlib bootstrap complete.")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: patch-kde-blueprints-arm64.py <libjpeg-turbo.py> <pixman.py> <libp11.py>")
        sys.exit(2)
    bootstrap_zlib()
    patch_libjpeg(sys.argv[1])
    patch_pixman(sys.argv[2])
    patch_libp11(sys.argv[3])
