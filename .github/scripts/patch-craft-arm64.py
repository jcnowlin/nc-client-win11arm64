#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Dudiebug
# SPDX-License-Identifier: GPL-2.0-or-later

import sys, re


# Patch 1: add ARM64 entries to the architectures dicts in
# CraftSetupHelper.getMSVCEnv. These dicts select the vcvarsall.bat
# "<host>_<target>" argument, so the host half must match the machine
# Craft actually runs on:
#   - native dict (host == target): "arm64" -> the ARM64-hosted toolchain,
#     used for native builds on Windows ARM64 runners.
#   - non-native dict (host != target): "amd64_arm64" -> the x64-hosted
#     ARM64 cross compiler, used when cross-compiling from the x64
#     windows-latest runners. ("x86_arm64" would select the x86-hosted
#     toolchain instead; on current runner images its ARM64 cross tools
#     are absent, so CMake silently falls back to the x86 compiler and
#     the link then fails with LNK1104 against the ARM64 LIB dirs.)
def patch_msvc_architectures(source: str) -> str:
    s_new = re.sub(
        r'(CraftCore\.compiler\.Architecture\.x86_64: "amd64",\r?\n)(\s*\})',
        r'\1                CraftCore.compiler.Architecture.arm64: "arm64",\n\2',
        source, count=1,
    )
    if s_new == source:
        raise ValueError("could not patch native architectures dict")
    s_new2 = re.sub(
        r'(CraftCore\.compiler\.Architecture\.x86_64: "x86_amd64",\r?\n)(\s*\})',
        r'\1                CraftCore.compiler.Architecture.arm64: "amd64_arm64",\n\2',
        s_new, count=1,
    )
    if s_new2 == s_new:
        raise ValueError("could not patch non-native architectures dict")
    return s_new2


# Patch 10: kshimgen is a host tool. Craft executes kshimgen.exe during the
# build (utils.createShim runs it to generate shims), so when cross-compiling
# Windows ARM64 from x64 it must be built for the x64 host. An ARM64
# kshimgen.exe fails on the x64 runner with WinError 216 ("not compatible
# with the version of Windows you're running") when dev-utils/cmake's
# install() calls createShim.
# The helpers below are injected into blueprints/dev-utils/kshimgen/kshimgen.py.
# They are kept as string constants (single source of truth) so the test suite
# can exec the exact injected code with mocked os/platform/CraftCore.
_HOST_TOOL_HELPERS = r'''
def _is_windows_arm64_cross():
    """True when targeting Windows ARM64 from an x64 host."""
    return (
        CraftCore.compiler.isWindows
        and CraftCore.compiler.architecture == CraftCore.compiler.Architecture.arm64
        and platform.machine().lower() in ("amd64", "x86_64")
    )


def _host_tool_env():
    """Env overrides to build kshimgen for the x64 host.

    Returns {} when not cross-compiling, or when the host toolchain
    cannot be determined (caller fails loudly). Everything is derived
    via path substitution; no hardcoded VS paths or versions. INCLUDE
    is arch-independent and left alone.
    """
    if not _is_windows_arm64_cross():
        return {}
    path = os.environ.get("PATH", "")
    m = re.search(r"([^;]*[/\\]bin[/\\]hostx64[/\\])arm64", path, re.IGNORECASE)
    if not m:
        return {}
    x64bin = m.group(1) + "x64"
    x64cl = os.path.join(x64bin, "cl.exe")
    if not os.path.isfile(x64cl):
        return {}
    lib = os.environ.get("LIB", "")
    x64lib = re.sub(r"([/\\])arm64(?=;|$)", r"\1x64", lib, flags=re.IGNORECASE)
    return {
        "PATH": f"{x64bin}{os.pathsep}{path}",
        "LIB": x64lib,
        "CC": x64cl,
        "CXX": x64cl,
        # vcvarsall stamps the target arch; CMake's MSVC logic keys the
        # library directories off it, so it must say x64 for a host build.
        "VSCMD_ARG_TGT_ARCH": "x64",
        "VSCMD_ARG_HOST_ARCH": "x64",
    }
'''

_KSHIMGEN_CONFIGURE = r'''
        def configure(self):
            cmakePath = CraftCore.standardDirs.craftRoot() / "dev-utils/cmake-base"
            if CraftCore.compiler.isMacOS:
                cmakePath /= "CMake.app/Contents/bin"
            else:
                cmakePath /= "bin"
            path = f"{cmakePath}{os.pathsep}{os.environ['PATH']}"
            env = {"PATH": path}
            hostEnv = _host_tool_env()
            if _is_windows_arm64_cross():
                # kshimgen runs on the build host (utils.createShim executes
                # it); cross-compiling to ARM64 from x64, build it for x64.
                if not hostEnv:
                    CraftCore.log.error("host tool: cannot determine x64 host toolchain, aborting cross build")
                    return False
                env.update(hostEnv)
                CraftCore.log.info("host tool: building for x64 host (ARM64 target)")
            with utils.ScopedEnv(env):
                return super().configure()

        def make(self):
            # The host toolchain must also be active for the link step;
            # configure() alone is not enough (CMake hardcodes the compiler
            # but the linker still resolves libraries via LIB at build time).
            hostEnv = _host_tool_env()
            if _is_windows_arm64_cross():
                if not hostEnv:
                    CraftCore.log.error("host tool: cannot determine x64 host toolchain, aborting cross build")
                    return False
                with utils.ScopedEnv(hostEnv):
                    return super().make()
            return super().make()
'''


def patch_kshimgen_host_build(source: str) -> str:
    """Patch 10: build kshimgen for the x64 host when cross-compiling to ARM64."""
    s_new = re.sub(
        r"^import os$",
        "import os\nimport platform\nimport re",
        source, count=1, flags=re.MULTILINE,
    )
    if s_new == source:
        raise ValueError("could not patch kshimgen imports")
    s_new2 = re.sub(
        r"\n\n\nclass subinfo",
        lambda m: "\n\n" + _HOST_TOOL_HELPERS.strip("\n") + "\n\n\nclass subinfo",
        s_new, count=1,
    )
    if s_new2 == s_new:
        raise ValueError("could not inject kshimgen host helpers")
    # configure() is the last method in the file; replace it through EOF.
    # (Lambda replacement: the injected code contains backslash sequences.)
    s_new3 = re.sub(
        r"        def configure\(self\):.*\Z",
        lambda m: _KSHIMGEN_CONFIGURE.strip("\n"),
        s_new2, count=1, flags=re.DOTALL,
    )
    if s_new3 == s_new2:
        raise ValueError("could not patch kshimgen configure()")
    return s_new3


# Patch 11: pkgconf is a host tool (its postInstall creates a shim via
# utils.createShim, and the shim is used during builds). When cross-compiling
# Windows ARM64 from x64, build it for the x64 host; otherwise its configure
# script compiles an ARM64 test binary and fails with "cannot run C compiled
# programs" when it tries to execute it.
_PKGCONF_METHODS = r'''
    def configure(self):
        hostEnv = _host_tool_env()
        if _is_windows_arm64_cross():
            if not hostEnv:
                CraftCore.log.error("host tool: cannot determine x64 host toolchain, aborting cross build")
                return False
            CraftCore.log.info("host tool: building for x64 host (ARM64 target)")
            # Patch 13 sets --host=aarch64 for the ARM64 target; as a host
            # tool pkgconf needs the x64 triplets instead.
            from Utils.Arguments import Arguments
            self.platform = Arguments(
                [
                    "--host=x86_64-w64-mingw32",
                    "--build=x86_64-w64-mingw32",
                    "--target=x86_64-w64-mingw32",
                ]
            )
            with utils.ScopedEnv(hostEnv):
                return super().configure()
        return super().configure()

    def make(self, dummyBuildType=None):
        hostEnv = _host_tool_env()
        if _is_windows_arm64_cross():
            if not hostEnv:
                CraftCore.log.error("host tool: cannot determine x64 host toolchain, aborting cross build")
                return False
            with utils.ScopedEnv(hostEnv):
                return super().make(dummyBuildType)
        return super().make(dummyBuildType)
'''


def patch_pkgconf_host_build(source: str) -> str:
    """Patch 11: build pkgconf for the x64 host when cross-compiling to ARM64."""
    s_new = re.sub(
        r"^# -\*- coding: utf-8 -\*-$",
        "# -*- coding: utf-8 -*-\nimport os\nimport platform\nimport re",
        source, count=1, flags=re.MULTILINE,
    )
    if s_new == source:
        raise ValueError("could not patch pkgconf imports")
    s_new2 = re.sub(
        r"\n\nclass Package\(AutoToolsPackageBase\):",
        lambda m: "\n\n" + _HOST_TOOL_HELPERS.strip("\n") + "\n\n\nclass Package(AutoToolsPackageBase):",
        s_new, count=1,
    )
    if s_new2 == s_new:
        raise ValueError("could not inject pkgconf host helpers")
    s_new3 = re.sub(
        r"\n    def postInstall\(self\):",
        lambda m: "\n" + _PKGCONF_METHODS.strip("\n") + "\n\n    def postInstall(self):",
        s_new2, count=1,
    )
    if s_new3 == s_new2:
        raise ValueError("could not inject pkgconf configure()/make()")
    return s_new3


# Patch 12: perl is a host tool (libs/openssl runs `perl Configure` during
# its build). When cross-compiling Windows ARM64 from x64, build it for the
# x64 host; otherwise the ARM64 perl.exe cannot run on the x64 runner.
# The win32 Makefile also needs CRAFT_WIN64 set for x64 (not "undef").
_PERL_METHODS = r'''
    def make(self):
        # NOTE: This overrides PackageMSVC.make() entirely (Python's
        # last-definition-wins). We replicate its logic here because
        # super().make() would skip the os.chdir(self.sourceDir() / "win32")
        # that the original does.
        if _is_windows_arm64_cross():
            hostEnv = _host_tool_env()
            if not hostEnv:
                CraftCore.log.error("host tool: cannot determine x64 host toolchain, aborting cross build")
                return False
            CraftCore.log.info("host tool: building for x64 host (ARM64 target)")
            # CRAFT_WIN64="" selects the 64-bit build; the __init__ set it to
            # "undef" for the ARM64 target. Arguments is not iterable, so
            # access the underlying list via the private attribute.
            orig_args = self.subinfo.options.make.args
            from Utils.Arguments import Arguments
            orig_list = orig_args._Arguments__args if hasattr(orig_args, "_Arguments__args") else []
            fixed_list = [a for a in orig_list if not str(a).startswith("CRAFT_WIN64=")]
            fixed_list.append("CRAFT_WIN64=")
            saved_env = dict(os.environ)
            os.environ.update(hostEnv)
            self.subinfo.options.make.args = Arguments(fixed_list)
            try:
                with utils.ScopedEnv(self._globEnv()):
                    os.chdir(self.sourceDir() / "win32")
                    return utils.system(Arguments([self.makeProgram, self.makeOptions(self.subinfo.options.make.args)]))
            finally:
                os.environ.clear()
                os.environ.update(saved_env)
                self.subinfo.options.make.args = orig_args
        # Non-cross: replicate original PackageMSVC.make().
        with utils.ScopedEnv(self._globEnv()):
            os.chdir(self.sourceDir() / "win32")
            from Utils.Arguments import Arguments
            return utils.system(Arguments([self.makeProgram, self.makeOptions(self.subinfo.options.make.args)]))

    def install(self):
        # NOTE: Same as make() above — replicates PackageMSVC.install()
        # instead of calling super(), which would skip the original logic.
        if _is_windows_arm64_cross():
            hostEnv = _host_tool_env()
            if not hostEnv:
                CraftCore.log.error("host tool: cannot determine x64 host toolchain, aborting cross build")
                return False
            saved_env = dict(os.environ)
            os.environ.update(hostEnv)
            orig_args = self.subinfo.options.install.args
            from Utils.Arguments import Arguments
            orig_list = orig_args._Arguments__args if hasattr(orig_args, "_Arguments__args") else []
            fixed_list = [a for a in orig_list if not str(a).startswith("CRAFT_WIN64=")]
            fixed_list.append("CRAFT_WIN64=")
            self.subinfo.options.install.args = Arguments(fixed_list)
            try:
                with utils.ScopedEnv(self._globEnv()):
                    if not BuildSystemBase.install(self):
                        return False
                    os.chdir(self.sourceDir() / "win32")
                    if not utils.system(Arguments([self.makeProgram, self.makeOptions(self.subinfo.options.install.args), f"DESTDIR={self.installDir()}"])):
                        return False
                def makeWriatable(root):
                    with os.scandir(root) as scan:
                        for f in scan:
                            utils.makeWritable(f.path)
                            if f.is_dir():
                                makeWriatable(f.path)
                makeWriatable(self.installDir())
                return True
            finally:
                os.environ.clear()
                os.environ.update(saved_env)
                self.subinfo.options.install.args = orig_args
        # Non-cross: replicate original PackageMSVC.install().
        with utils.ScopedEnv(self._globEnv()):
            if not BuildSystemBase.install(self):
                return False
            os.chdir(self.sourceDir() / "win32")
            from Utils.Arguments import Arguments
            if not utils.system(Arguments([self.makeProgram, self.makeOptions(self.subinfo.options.install.args), f"DESTDIR={self.installDir()}"])):
                return False
        def makeWriatable(root):
            with os.scandir(root) as scan:
                for f in scan:
                    utils.makeWritable(f.path)
                    if f.is_dir():
                        makeWriatable(f.path)
        makeWriatable(self.installDir())
        return True
'''


def patch_perl_host_build(source: str) -> str:
    """Patch 12: build perl for the x64 host when cross-compiling to ARM64."""
    s_new = re.sub(
        r"^import os$",
        "import os\nimport platform\nimport re",
        source, count=1, flags=re.MULTILINE,
    )
    if s_new == source:
        raise ValueError("could not patch perl imports")
    s_new2 = re.sub(
        r"\nclass PackageMSVC\(MakeFilePackageBase\):",
        lambda m: "\n" + _HOST_TOOL_HELPERS.strip("\n") + "\n\n\nclass PackageMSVC(MakeFilePackageBase):",
        s_new, count=1,
    )
    if s_new2 == s_new:
        raise ValueError("could not inject perl host helpers")
    # Insert make()/install() overrides before postInstall().
    s_new3 = re.sub(
        r"\n    def postInstall\(self\):",
        lambda m: "\n" + _PERL_METHODS.strip("\n") + "\n\n    def postInstall(self):",
        s_new2, count=1,
    )
    if s_new3 == s_new2:
        raise ValueError("could not inject perl make()/install()")
    return s_new3


def patch_autotools_arm64_host(source: str) -> str:
    """Patch 13: teach AutoToolsBuildSystem about Windows ARM64.

    The upstream __init__ only handles x86_32 vs "everything else" for
    Windows, so an ARM64 target falls through to --host=x86_64-w64-mingw32.
    Configure then believes the build is native, tries to execute the
    ARM64 test binary on the x64 host, and dies with "cannot run C compiled
    programs". For an ARM64 target, --host/--target must be aarch64; --build
    is the machine doing the building (x64 when cross-compiling).
    Host tools (e.g. pkgconf via Patch 11) override self.platform back to
    x86_64 for their x64 host build.
    """
    s_new = re.sub(
        r"(\s+)else:\n(\s+self\.platform = Arguments\(\n\s+\[\n\s+\"--host=x86_64-w64-mingw32\",)",
        r"""\1elif CraftCore.compiler.architecture == CraftCompiler.Architecture.arm64:
\1    # Windows ARM64 target: --host/--target are ARM64; --build is the
\1    # machine doing the building (x64 when cross-compiling from x64).
\1    if CraftCore.compiler.isNative():
\1        _build_triplet = "aarch64-w64-mingw32"
\1    else:
\1        _build_triplet = "x86_64-w64-mingw32"
\1    self.platform = Arguments(
\1        [
\1            "--host=aarch64-w64-mingw32",
\1            f"--build={_build_triplet}",
\1            "--target=aarch64-w64-mingw32",
\1        ]
\1    )
\1else:
\2""",
        source, count=1,
    )
    if s_new == source:
        raise ValueError("could not patch AutoToolsBuildSystem for Windows ARM64")
    return s_new


# Patch 14: ninja is a host tool (CMake runs `ninja --version` during
# configure, and the build invokes ninja to execute commands). When
# cross-compiling Windows ARM64 from x64, build it for the x64 host;
# otherwise the ARM64 ninja.exe cannot run on the x64 runner.
_NINJA_METHODS = r'''
    def configure(self):
        hostEnv = _host_tool_env()
        if _is_windows_arm64_cross():
            if not hostEnv:
                CraftCore.log.error("host tool: cannot determine x64 host toolchain, aborting cross build")
                return False
            CraftCore.log.info("host tool: building for x64 host (ARM64 target)")
            with utils.ScopedEnv(hostEnv):
                return super().configure()
        return super().configure()

    def make(self):
        hostEnv = _host_tool_env()
        if _is_windows_arm64_cross():
            if not hostEnv:
                CraftCore.log.error("host tool: cannot determine x64 host toolchain, aborting cross build")
                return False
            with utils.ScopedEnv(hostEnv):
                return super().make()
        return super().make()
'''


def patch_ninja_host_build(source: str) -> str:
    """Patch 14: build ninja for the x64 host when cross-compiling to ARM64."""
    s_new = re.sub(
        r"^import info$",
        "import info\nimport os\nimport platform\nimport re",
        source, count=1, flags=re.MULTILINE,
    )
    if s_new == source:
        raise ValueError("could not patch ninja imports")
    s_new2 = re.sub(
        r"\nclass Package\(CMakePackageBase\):",
        lambda m: "\n" + _HOST_TOOL_HELPERS.strip("\n") + "\n\n\nclass Package(CMakePackageBase):",
        s_new, count=1,
    )
    if s_new2 == s_new:
        raise ValueError("could not inject ninja host helpers")
    # Insert configure()/make() overrides before postInstall().
    s_new3 = re.sub(
        r"\n    def postInstall\(self\):",
        lambda m: "\n" + _NINJA_METHODS.strip("\n") + "\n\n    def postInstall(self):",
        s_new2, count=1,
    )
    if s_new3 == s_new2:
        raise ValueError("could not inject ninja configure()/make()")
    return s_new3


def main(argv):
    helper_path = argv[1]
    git_path    = argv[2]
    perl_path   = argv[3]
    openssl_path = argv[4]
    liblzma_path = argv[5]
    libunistring_path = argv[6]
    libffi_path = argv[7]
    python_path = argv[8]
    kshimgen_path = argv[9]
    pkgconf_path = argv[10]
    perl_host_path = argv[11]
    autotools_system_path = argv[12]
    ninja_path = argv[13]

    with open(helper_path, 'r', encoding='utf-8') as f:
        helper_source = f.read()
    try:
        helper_patched = patch_msvc_architectures(helper_source)
    except ValueError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)
    with open(helper_path, 'w', encoding='utf-8') as f:
        f.write(helper_patched)
    print(f'Patched {helper_path}: added ARM64 architecture entries')

    # Patch 2: dev-utils/_windows/git/git.py locateGit() — handle the
    # Git for Windows ARM64 (clangarm64) layout where git.exe lives at
    # Git\clangarm64\bin\git.exe but the shared MSYS userland stays at
    # Git\usr\bin. Without this fix, dev-utils/patch and dev-utils/sed
    # fail post-install because they look up patch.exe / sed.exe at
    # locateGit().parent / "usr/bin/X.exe", which under the new layout
    # resolves to Git\clangarm64\usr\bin\X.exe (does not exist).
    with open(git_path, 'r', encoding='utf-8') as f:
        s = f.read()
    old_block = (
        '        # check whether git is installed by the system or us\n'
        '        if (CraftCore.standardDirs.craftRoot() / "dev-utils") in gitPath.parents:\n'
        '            return CraftCore.standardDirs.craftRoot() / "dev-utils/git/bin"\n'
        '        return gitPath.parent\n'
    )
    new_block = (
        '        # check whether git is installed by the system or us\n'
        '        if (CraftCore.standardDirs.craftRoot() / "dev-utils") in gitPath.parents:\n'
        '            return CraftCore.standardDirs.craftRoot() / "dev-utils/git/bin"\n'
        '        # Downstream consumers (dev-utils/patch, dev-utils/sed) expect\n'
        '        # locateGit().parent to be the MSYS root containing usr/bin/X.exe.\n'
        '        # Legacy x64 Git for Windows lays out as Git\\bin\\git.exe so\n'
        '        # gitPath.parent = Git\\bin and (Git\\bin).parent = Git, contract\n'
        '        # holds. The Git for Windows ARM64 (clangarm64) build instead\n'
        '        # uses Git\\clangarm64\\bin\\git.exe but keeps the shared MSYS\n'
        '        # userland at Git\\usr\\bin, so .parent.parent = Git\\clangarm64\n'
        '        # and the contract breaks. Detect by looking for usr\\bin and\n'
        '        # return a path one level shifted so the contract still holds.\n'
        '        binDir = gitPath.parent\n'
        '        if (binDir.parent / "usr" / "bin").exists():\n'
        '            return binDir\n'
        '        if (binDir.parent.parent / "usr" / "bin").exists():\n'
        '            return binDir.parent.parent / "bin"\n'
        '        return binDir\n'
    )
    if old_block not in s:
        print('ERROR: could not find locateGit() block in git.py', file=sys.stderr)
        sys.exit(1)
    with open(git_path, 'w', encoding='utf-8') as f:
        f.write(s.replace(old_block, new_block))
    print(f'Patched {git_path}: locateGit() handles ARM64 Git for Windows layout')

    # Patch 3: dev-utils/perl/perl.py CRAFT_WIN64 — Perl 5.40's win32/Makefile
    # already handles ARM64 (sets WIN64=define when PROCESSOR_ARCHITECTURE is
    # AMD64, IA64, or ARM64), but only when WIN64 is empty so its auto-detect
    # block runs. The Craft blueprint forces WIN64=undef for any non-x86_64
    # target, which under arm64 produces a 64-bit ARM64 miniperl.exe compiled
    # with 32-bit type assumptions; the resulting binary crashes with
    # 0xc0000005 the moment perl tries to use it. Extend the 64-bit branch
    # to include arm64 so the Makefile can self-configure.
    with open(perl_path, 'r', encoding='utf-8') as f:
        s = f.read()
    old_perl = '"CRAFT_WIN64": "" if CraftCore.compiler.architecture == CraftCompiler.Architecture.x86_64 else "undef",'
    new_perl = '"CRAFT_WIN64": "" if CraftCore.compiler.architecture in (CraftCompiler.Architecture.x86_64, CraftCompiler.Architecture.arm64) else "undef",'
    if old_perl not in s:
        print('ERROR: could not find CRAFT_WIN64 ternary in perl.py', file=sys.stderr)
        sys.exit(1)
    s = s.replace(old_perl, new_perl)
    with open(perl_path, 'w', encoding='utf-8') as f:
        f.write(s)
    print(f'Patched {perl_path}: CRAFT_WIN64 empty for arm64 (lets Makefile auto-detect)')

    # Patch 4: dev-utils/perl/perl.py _globEnv() — Perl 5.40's win32/Makefile
    # recipe for ..\git_version.h is "cd .. && miniperl -Ilib make_patchnum.pl
    # && cd win32". It uses bare "miniperl" rather than $(MINIPERL), and
    # assumes cmd.exe will find miniperl.exe in the current directory after
    # the cd. Modern Windows cmd.exe does not search the cwd for executables,
    # so the recipe fails with "'miniperl' is not recognized" even though
    # miniperl.exe is right there. Put the perl source root on PATH so the
    # bare name resolves via PATH lookup.
    old_globenv = (
        '    def _globEnv(self):\n'
        '        env = {}\n'
        '        if CraftCore.compiler.isMSVC():\n'
        '            env = {"PATH": f"{self.blueprintDir()};{os.environ[\'PATH\']}"}\n'
        '        return env\n'
    )
    new_globenv = (
        '    def _globEnv(self):\n'
        '        env = {}\n'
        '        if CraftCore.compiler.isMSVC():\n'
        '            # Perl 5.40 win32/Makefile rule for ..\\git_version.h runs\n'
        '            # "cd .. && miniperl -Ilib make_patchnum.pl" with a bare\n'
        '            # "miniperl" name. Modern cmd.exe does not search the cwd\n'
        '            # for executables, so even though miniperl.exe is right\n'
        '            # there it is not found. Put the perl source root on PATH\n'
        '            # so the bare name resolves via PATH lookup.\n'
        '            env = {"PATH": f"{self.sourceDir()};{self.blueprintDir()};{os.environ[\'PATH\']}"}\n'
        '        return env\n'
    )
    if old_globenv not in s:
        print('ERROR: could not find _globEnv() block in perl.py', file=sys.stderr)
        sys.exit(1)
    with open(perl_path, 'w', encoding='utf-8') as f:
        f.write(s.replace(old_globenv, new_globenv))
    print(f'Patched {perl_path}: _globEnv adds sourceDir to PATH for bare-miniperl recipe')

    # Patch 5: libs/openssl/openssl.py — extend the Configure target
    # ternary to recognise arm64. The upstream blueprint only knows
    # about x86_64 (VC-WIN64A) and falls back to VC-WIN32 for any
    # other architecture, so on a Windows ARM64 host OpenSSL ends up
    # configured for 32-bit x86: the C sources are built with
    # -DAES_ASM/-DOPENSSL_IA32_SSE2/-DSHA1_ASM/etc. and Configure
    # emits nasm rules for x86 .asm files. The resulting object
    # files cannot be linked into the otherwise-arm64 build. OpenSSL
    # ships a proper VC-WIN64-ARM target that uses the ARM64 .S
    # perlasm modules, so select it explicitly for arm64.
    with open(openssl_path, 'r', encoding='utf-8') as f:
        s = f.read()
    old_openssl = '                "VC-WIN64A" if CraftCore.compiler.architecture == CraftCompiler.Architecture.x86_64 else "VC-WIN32",\n'
    new_openssl = (
        '                # Pick the OpenSSL Configure target that matches the host arch.\n'
        '                # The upstream blueprint only knows about x86_64 (VC-WIN64A) and\n'
        '                # falls back to VC-WIN32 for everything else, which on a Windows\n'
        '                # ARM64 host produces a build configured for 32-bit x86 (the C\n'
        '                # sources get compiled with -DAES_ASM/-DOPENSSL_IA32_SSE2/etc.\n'
        '                # and Configure emits nasm rules for x86 .asm files). OpenSSL\n'
        '                # ships a proper VC-WIN64-ARM target that uses the ARM64 .S\n'
        '                # perlasm modules, so select it for arm64.\n'
        '                ("VC-WIN64A" if CraftCore.compiler.architecture == CraftCompiler.Architecture.x86_64\n'
        '                 else "VC-WIN64-ARM" if CraftCore.compiler.architecture == CraftCompiler.Architecture.arm64\n'
        '                 else "VC-WIN32"),\n'
    )
    if old_openssl not in s:
        print('ERROR: could not find VC-WIN64A/VC-WIN32 ternary in openssl.py', file=sys.stderr)
        sys.exit(1)
    with open(openssl_path, 'w', encoding='utf-8') as f:
        f.write(s.replace(old_openssl, new_openssl))
    print(f'Patched {openssl_path}: openssl Configure target VC-WIN64-ARM for arm64')

    # Patch 6: libs/liblzma/liblzma.py — xz 5.2.3's windows/ project files
    # only declare Win32, x64, and ReleaseMT|x64 platform configurations.
    # On a Windows ARM64 host MSBuild rejects /p:Platform=arm64 with
    # MSB4126 "The specified solution configuration 'Release|arm64' is
    # invalid". xz itself is pure C with no x86-specific assembly, so
    # cloning the x64 sections of xz_win.sln, liblzma.vcxproj, and
    # liblzma_dll.vcxproj as arm64 siblings is sufficient. Inject a
    # configure() override into PackageMSBuild that does the cloning
    # right before MSBuild runs.
    with open(liblzma_path, 'r', encoding='utf-8') as f:
        s = f.read()
    old_liblzma = (
        'class PackageMSBuild(MSBuildPackageBase):\n'
        '    def __init__(self, **kwargs):\n'
        '        super().__init__(**kwargs)\n'
        '        self.subinfo.options.configure.projectFile = self.sourceDir() / "windows/xz_win.sln"\n'
        '        self.msbuildTargets = ["liblzma_dll"]\n'
        '\n'
        '    def install(self):\n'
    )
    new_liblzma = (
        'class PackageMSBuild(MSBuildPackageBase):\n'
        '    def __init__(self, **kwargs):\n'
        '        super().__init__(**kwargs)\n'
        '        self.subinfo.options.configure.projectFile = self.sourceDir() / "windows/xz_win.sln"\n'
        '        self.msbuildTargets = ["liblzma_dll"]\n'
        '\n'
        '    def configure(self):\n'
        '        # The xz 5.2.3 windows/ project files only declare Win32, x64, and\n'
        '        # ReleaseMT|x64 platform configurations. On a Windows ARM64 host\n'
        '        # MSBuild rejects /p:Platform=arm64 (the platform Craft passes for\n'
        '        # an arm64 target) with MSB4126 "The specified solution\n'
        '        # configuration \'Release|arm64\' is invalid". xz itself is pure C\n'
        '        # with no x86-specific inline assembly, so cloning the x64 sections\n'
        '        # of xz_win.sln, liblzma.vcxproj, and liblzma_dll.vcxproj as arm64\n'
        '        # siblings is sufficient: the MSVC toolset handles arm64\n'
        '        # codegen automatically once an arm64 platform is declared.\n'
        '        from CraftCompiler import CraftCompiler\n'
        '        if CraftCore.compiler.architecture == CraftCompiler.Architecture.arm64:\n'
        '            self._addArm64PlatformConfigs()\n'
        '        return super().configure()\n'
        '\n'
        '    def _addArm64PlatformConfigs(self):\n'
        '        import re\n'
        '        windowsDir = self.sourceDir() / "windows"\n'
        '        slnPath = windowsDir / "xz_win.sln"\n'
        '        with open(slnPath, "r", encoding="utf-8", newline="") as f:\n'
        '            slnLines = f.readlines()\n'
        '        if not any("|arm64" in ln for ln in slnLines):\n'
        '            outLines = []\n'
        '            for ln in slnLines:\n'
        '                outLines.append(ln)\n'
        '                if "|x64" in ln:\n'
        '                    outLines.append(ln.replace("|x64", "|arm64"))\n'
        '            with open(slnPath, "w", encoding="utf-8", newline="") as f:\n'
        '                f.writelines(outLines)\n'
        '        # Non-greedy block regex: capture each top-level XML element whose\n'
        '        # opening tag mentions "|x64" (in either an Include= or Condition=\n'
        '        # attribute) and clone it as an arm64 sibling. The .vcxproj files\n'
        '        # have no nested elements of the same name at the relevant depths,\n'
        '        # so a non-greedy match is unambiguous.\n'
        '        blockPattern = re.compile(r"(  <(\\w+)[^>]*\\|x64[^>]*>[\\s\\S]*?</\\2>)")\n'
        '        for vcxproj in ("liblzma.vcxproj", "liblzma_dll.vcxproj"):\n'
        '            path = windowsDir / vcxproj\n'
        '            with open(path, "r", encoding="utf-8", newline="") as f:\n'
        '                content = f.read()\n'
        '            if "|arm64" in content:\n'
        '                continue\n'
        '            def cloneBlock(m):\n'
        '                orig = m.group(1)\n'
        '                cloned = (\n'
        '                    orig.replace("|x64", "|arm64")\n'
        '                        .replace("<Platform>x64</Platform>", "<Platform>arm64</Platform>")\n'
        '                )\n'
        '                return orig + "\\n" + cloned\n'
        '            with open(path, "w", encoding="utf-8", newline="") as f:\n'
        '                f.write(blockPattern.sub(cloneBlock, content))\n'
        '\n'
        '    def install(self):\n'
    )
    if old_liblzma not in s:
        print('ERROR: could not find PackageMSBuild __init__/install block in liblzma.py', file=sys.stderr)
        sys.exit(1)
    with open(liblzma_path, 'w', encoding='utf-8') as f:
        f.write(s.replace(old_liblzma, new_liblzma))
    print(f'Patched {liblzma_path}: PackageMSBuild.configure() clones x64 platform configs as arm64')

    # Patch 7: libs/libunistring/libunistring.py
    import stat, textwrap
    with open(libunistring_path, 'r', encoding='utf-8') as f:
        s = f.read()
    old_libunistring = (
        'class Package(AutoToolsPackageBase):\n'
        '    def __init__(self, **kwargs):\n'
        '        super().__init__(**kwargs)\n'
        '        self.shell.useMSVCCompatEnv = True\n'
    )
    new_libunistring = (
        'import stat\n'
        'import textwrap\n'
        'from CraftCore import CraftCore\n'
        '\n\n'
        'class Package(AutoToolsPackageBase):\n'
        '    def __init__(self, **kwargs):\n'
        '        super().__init__(**kwargs)\n'
        '        self.shell.useMSVCCompatEnv = True\n'
        '\n'
        '    def configure(self):\n'
        '        from CraftCompiler import CraftCompiler\n'
        '        if CraftCore.compiler.isWindows and CraftCore.compiler.architecture == CraftCompiler.Architecture.arm64:\n'
        '            self._installWindresArm64Wrapper()\n'
        '        return super().configure()\n'
        '\n'
        '    def _installWindresArm64Wrapper(self):\n'
        '        localBin = CraftCore.standardDirs.msysDir() / "usr" / "local" / "bin"\n'
        '        localBin.mkdir(parents=True, exist_ok=True)\n'
        '        wrapperPath = localBin / "windres"\n'
        '        if wrapperPath.exists():\n'
        '            return\n'
        '        realWindres = str(CraftCore.standardDirs.msysDir() / "usr" / "bin" / "windres.exe")\n'
        '        wrapperContent = textwrap.dedent(f"""\\\n'
        '            #!/usr/bin/env python\n'
        '            import subprocess, sys, os\n'
        '            REAL_WINDRES = r\'{realWindres}\'\n'
        '            output = None\n'
        '            i = 1\n'
        '            while i < len(sys.argv):\n'
        '                a = sys.argv[i]\n'
        '                if a in (\'-o\', \'--output\') and i + 1 < len(sys.argv):\n'
        '                    output = sys.argv[i + 1]\n'
        '                elif a.startswith(\'-o\') and len(a) > 2:\n'
        '                    output = a[2:]\n'
        '                i += 1\n'
        '            result = subprocess.run([REAL_WINDRES] + sys.argv[1:])\n'
        '            if result.returncode != 0:\n'
        '                sys.exit(result.returncode)\n'
        '            if output and os.path.exists(output):\n'
        '                with open(output, \'r+b\') as f:\n'
        '                    if f.read(2) == b\'\\\\x64\\\\x86\':\n'
        '                        f.seek(0); f.write(b\'\\\\x64\\\\xaa\')\n'
        '            sys.exit(0)\n'
        '        """)\n'
        '        wrapperPath.write_text(wrapperContent, encoding=\'utf-8\')\n'
        '        wrapperPath.chmod(wrapperPath.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)\n'
    )
    if old_libunistring not in s:
        print('ERROR: could not find Package class in libunistring.py', file=sys.stderr)
        sys.exit(1)
    with open(libunistring_path, 'w', encoding='utf-8') as f:
        f.write(s.replace(old_libunistring, new_libunistring))
    print(f'Patched {libunistring_path}: Package.configure() installs windres ARM64 wrapper')

    # Patch 8: libs/libffi/libffi.py
    with open(libffi_path, 'r', encoding='utf-8') as f:
        s = f.read()
    old_libffi_import = 'from Package.AutoToolsPackageBase import AutoToolsPackageBase\nfrom Utils import CraftHash\n'
    new_libffi_import = 'from Package.AutoToolsPackageBase import AutoToolsPackageBase\nfrom Utils import CraftHash\nfrom Utils.Arguments import Arguments\n'
    if old_libffi_import not in s:
        print('ERROR: could not find AutoToolsPackageBase import in libffi.py', file=sys.stderr)
        sys.exit(1)
    s = s.replace(old_libffi_import, new_libffi_import)
    old_libffi_arch = (
        '            if CraftCore.compiler.architecture == CraftCompiler.Architecture.x86_64:\n'
        '                arch = " -m64"\n'
        '            self.subinfo.options.configure.args += [f"CCAS={wrapper}{arch}"]\n'
    )
    new_libffi_arch = (
        '            if CraftCore.compiler.architecture == CraftCompiler.Architecture.x86_64:\n'
        '                arch = " -m64"\n'
        '            elif CraftCore.compiler.architecture == CraftCompiler.Architecture.arm64:\n'
        '                arch = " -marm64"\n'
        '            self.subinfo.options.configure.args += [f"CCAS={wrapper}{arch}"]\n'
    )
    if old_libffi_arch not in s:
        print('ERROR: could not find CCAS arch block in libffi.py', file=sys.stderr)
        sys.exit(1)
    s = s.replace(old_libffi_arch, new_libffi_arch)
    old_libffi_tail = (
        '            self.subinfo.options.configure.cflags += " -DFFI_BUILDING_DLL"\n'
        '        self.subinfo.options.configure.args += [\n'
    )
    new_libffi_tail = (
        '            self.subinfo.options.configure.cflags += " -DFFI_BUILDING_DLL"\n'
        '\n'
        '        if CraftCore.compiler.isWindows and CraftCore.compiler.architecture == CraftCompiler.Architecture.arm64:\n'
        '            self.platform = Arguments([\n'
        '                "--host=aarch64-w64-mingw32",\n'
        '                "--build=aarch64-w64-mingw32",\n'
        '                "--target=aarch64-w64-mingw32",\n'
        '            ])\n'
        '\n'
        '        self.subinfo.options.configure.args += [\n'
    )
    if old_libffi_tail not in s:
        print('ERROR: could not find FFI_BUILDING_DLL tail block in libffi.py', file=sys.stderr)
        sys.exit(1)
    with open(libffi_path, 'w', encoding='utf-8') as f:
        f.write(s.replace(old_libffi_tail, new_libffi_tail))
    print(f'Patched {libffi_path}: ARM64 platform triple override and -marm64 CCAS flag')

    # Patch 9: libs/python/python.py
    with open(python_path, 'r', encoding='utf-8') as f:
        s = f.read()
    old_python_install = (
        '        def install(self):\n'
        '            self.cleanImage()\n'
        '            verMinor = self.subinfo.buildTarget.split(".")[1]\n'
        '            debugSuffix = "_d" if self.buildType() == "Debug" else ""\n'
        '            for p in ["python", "pythonw", "venvlauncher", "venvwlauncher"]:\n'
        '                if not utils.copyFile(self.sourceDir() / f"PCbuild/amd64/{p}{debugSuffix}.exe", self.imageDir() / f"bin/{p}{debugSuffix}.exe"):\n'
    )
    new_python_install = (
        '        def install(self):\n'
        '            self.cleanImage()\n'
        '            verMinor = self.subinfo.buildTarget.split(".")[1]\n'
        '            debugSuffix = "_d" if self.buildType() == "Debug" else ""\n'
        '            from CraftCompiler import CraftCompiler\n'
        '            pcbuildArch = "arm64" if CraftCore.compiler.architecture == CraftCompiler.Architecture.arm64 else "amd64"\n'
        '            for p in ["python", "pythonw", "venvlauncher", "venvwlauncher"]:\n'
        '                if not utils.copyFile(self.sourceDir() / f"PCbuild/{pcbuildArch}/{p}{debugSuffix}.exe", self.imageDir() / f"bin/{p}{debugSuffix}.exe"):\n'
    )
    if old_python_install not in s:
        print('ERROR: could not find install() PCbuild/amd64 block in python.py', file=sys.stderr)
        sys.exit(1)
    s = s.replace(old_python_install, new_python_install)
    for old_ref, new_ref in [
        ('self.sourceDir() / "PCbuild/amd64/", self.imageDir() / "bin", ["*.dll"]',
         'self.sourceDir() / f"PCbuild/{pcbuildArch}/", self.imageDir() / "bin", ["*.dll"]'),
        ('self.sourceDir() / f"PCbuild/amd64/{p}{debugSuffix}.lib"',
         'self.sourceDir() / f"PCbuild/{pcbuildArch}/{p}{debugSuffix}.lib"'),
        ('self.sourceDir() / "PCbuild/amd64/", self.imageDir() / "bin/DLLs", ["*.pyd"]',
         'self.sourceDir() / f"PCbuild/{pcbuildArch}/", self.imageDir() / "bin/DLLs", ["*.pyd"]'),
    ]:
        if old_ref not in s:
            print(f'ERROR: PCbuild/amd64 reference not found: {old_ref!r}', file=sys.stderr)
            sys.exit(1)
        s = s.replace(old_ref, new_ref)
    with open(python_path, 'w', encoding='utf-8') as f:
        f.write(s)
    print(f'Patched {python_path}: PCbuild/amd64 -> PCbuild/{{pcbuildArch}} in install()')

    # Patch 10: blueprints/dev-utils/kshimgen/kshimgen.py — kshimgen is a
    # host tool (Craft runs kshimgen.exe via utils.createShim). When
    # cross-compiling Windows ARM64 from x64, build it for the x64 host;
    # an ARM64 kshimgen.exe dies with WinError 216 on the x64 runner.
    with open(kshimgen_path, 'r', encoding='utf-8') as f:
        kshimgen_source = f.read()
    try:
        kshimgen_patched = patch_kshimgen_host_build(kshimgen_source)
    except ValueError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)
    with open(kshimgen_path, 'w', encoding='utf-8') as f:
        f.write(kshimgen_patched)
    print(f'Patched {kshimgen_path}: kshimgen builds for x64 host when cross-compiling to ARM64')

    # Patch 11: blueprints/dev-utils/pkgconf/pkgconf.py — pkgconf is a host
    # tool (its postInstall creates a shim via utils.createShim, used during
    # builds). When cross-compiling Windows ARM64 from x64, build it for the
    # x64 host; otherwise configure fails with "cannot run C compiled
    # programs" when it tries to execute an ARM64 test binary.
    with open(pkgconf_path, 'r', encoding='utf-8') as f:
        pkgconf_source = f.read()
    try:
        pkgconf_patched = patch_pkgconf_host_build(pkgconf_source)
    except ValueError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)
    with open(pkgconf_path, 'w', encoding='utf-8') as f:
        f.write(pkgconf_patched)
    print(f'Patched {pkgconf_path}: pkgconf builds for x64 host when cross-compiling to ARM64')

    # Patch 12: blueprints/dev-utils/perl/perl.py — perl is a host tool
    # (libs/openssl runs `perl Configure` during its build). When
    # cross-compiling Windows ARM64 from x64, build it for the x64 host.
    with open(perl_host_path, 'r', encoding='utf-8') as f:
        perl_host_source = f.read()
    try:
        perl_host_patched = patch_perl_host_build(perl_host_source)
    except ValueError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)
    with open(perl_host_path, 'w', encoding='utf-8') as f:
        f.write(perl_host_patched)
    print(f'Patched {perl_host_path}: perl builds for x64 host when cross-compiling to ARM64')

    # Patch 13: bin/BuildSystem/AutoToolsBuildSystem.py — teach it about
    # Windows ARM64. Upstream only handles x86_32 vs "everything else", so an
    # ARM64 target gets --host=x86_64-w64-mingw32; configure then thinks the
    # build is native and dies with "cannot run C compiled programs" when it
    # tries to execute the ARM64 test binary.
    with open(autotools_system_path, 'r', encoding='utf-8') as f:
        autotools_system_source = f.read()
    try:
        autotools_system_patched = patch_autotools_arm64_host(autotools_system_source)
    except ValueError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)
    with open(autotools_system_path, 'w', encoding='utf-8') as f:
        f.write(autotools_system_patched)
    print(f'Patched {autotools_system_path}: AutoToolsBuildSystem handles Windows ARM64')

    # Patch 14: blueprints/dev-utils/ninja/ninja.py — ninja is a host tool
    # (CMake runs `ninja --version` during configure). When cross-compiling
    # Windows ARM64 from x64, build it for the x64 host.
    with open(ninja_path, 'r', encoding='utf-8') as f:
        ninja_source = f.read()
    try:
        ninja_patched = patch_ninja_host_build(ninja_source)
    except ValueError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)
    with open(ninja_path, 'w', encoding='utf-8') as f:
        f.write(ninja_patched)
    print(f'Patched {ninja_path}: ninja builds for x64 host when cross-compiling to ARM64')


if __name__ == "__main__":
    main(sys.argv)
