import py, os
import itertools

from rpython.translator.platform.posix import BasePosix, rpydir, GnuMakefile
from rpython.translator.platform import log, _run_subprocess


pypy_root_dir = str(py.path.local(rpydir).join('..'))


def find_executable(filename, environ=None):
    """Find an executable by searching the current $PATH."""
    if environ is None:
        environ = os.environ
    path = environ.get("PATH", "/usr/local/bin:/usr/bin:/bin").split(":")
    for dirpath in path:
        dirpath = os.path.abspath(dirpath.strip())
        filepath = os.path.normpath(os.path.join(dirpath, filename))
        if os.path.isfile(filepath):
            return filepath
    return None


def find_javascript_shell():
    """Find an executable javascript shell."""
    jsshell = find_executable("js")
    if jsshell is None:
        jsshell = find_executable("node")
        if jsshell is None:
            raise RuntimeError("Could not find javascript shell")
    return jsshell


class EmscriptenPlatform(BasePosix):
    """Platform for compiling to javascript via emscripten.

    Emscripten gives us the ability to compile from C to JavaScript while
    emulating a POSIX-like environment.  There are some subtlties, but it's
    mostly just a matter of using the right CC, CFLAGS, etc on top of a
    standard posixy build process.
    """

    name = "emscripten"

    exe_ext = 'js'
    so_ext = 'so'
    DEFAULT_CC = 'emcc'
    standalone_only = []
    shared_only = []

    llvm_opts = [] # ["-mergefunc"]  unfortunately this infinite-loops clang

    emcc_flags = [
      # Misc helpful/sensible flags.
      #"-v",
      #"-s", "DISABLE_EXCEPTION_CATCHING=1",
      # General llvm optimizations.  These seem to give a good tradeoff
      # between size and speed of the generated code.
      #"-Os",
      #"--llvm-opts", repr(["-Os"] + llvm_opts),
      # Handy warnings if we happen to do unaligned stuff.
      #"-s", "WARN_UNALIGNED=1",
      # These are things that we've found to work OK with the generated code.
      # and give a good performance/code-size tradeoff.
      #"-s", "FUNCTION_POINTER_ALIGNMENT=1",
      #"-s", "ASSERTIONS=0",
      # This prevents llvm optimization from throwing stuff away.
      # It's a useful default for intermediate compilations, but we replace
      # it with an explicit list of exports for the final executable.
      #"-s", "EXPORT_ALL=1",
      # Sadly, asmjs requires a fixed pre-allocated array for memory.
      # We default to a modest 64MB; this can be changed in the JS at runtime.
      # XXX TODO: automatically limit the GC to this much memory.
      # XXX TODO: ensure that pypy GC can detect when this runs out.
      #"-s", "TOTAL_MEMORY=67108864",
      # Some dummy includes to convince things to compile properly.
      # XXX TODO: only include these when needed.
      "-I", os.path.dirname(__file__),
      # Extra sanity-checking.
      # Enable these if things go wrong.
      #"-s", "ASSERTIONS=1",
      #"-s", "SAFE_HEAP=1",
      #"-s", "ALLOW_MEMORY_GROWTH=1",
      "-s", "INITIAL_MEMORY=1gb",
    ]

    cflags = list(emcc_flags) + \
             [f.strip() for f in os.environ.get("CFLAGS", "").split()] + \
             [f.strip() for f in os.environ.get("EMCFLAGS", "").split()]

    link_flags = list(emcc_flags) + [
      # For compiling with the JIT.
      # Emscripten is smart enough to pull in only the bits of code that it
      # needs here, so it's OK to list them all even if some are not used.
      #"--js-library", os.path.join(pypy_root_dir, "rpython/jit/backend/asmjs/library_jit_allinone.js"),
      #"--js-library", os.path.join(pypy_root_dir, "rpython/jit/backend/asmjs/library_jit.js"),
      #"--js-library", os.path.join(pypy_root_dir, "rpython/translator/platform/emscripten_platform/library_ffi.js"),
      #"--js-library", os.path.join(pypy_root_dir, "rpython/translator/platform/emscripten_platform/library_emjs.js"),
      # Disable the use of a separate memory-initializer file.
      # Such file makes it harder to run the compiled code during the build.
      #"--memory-init-file", "0",
    ] + [f.strip() for f in os.environ.get("LDFLAGS", "").split()] + \
        [f.strip() for f in os.environ.get("EMLDFLAGS", "").split()]

    extra_environ = {
        # Needed when running closure compiler.
        #"JAVA_HEAP_SIZE": "4096m",
    }
 
    def __init__(self, *args, **kwds):
        os.environ.update(self.extra_environ)
        super(EmscriptenPlatform, self).__init__(*args, **kwds)

    def execute(self, executable, args=None, env=None, *a, **k):
        if env is None:
            os.environ.update(self.extra_environ)
        else:
            env.update(self.extra_environ)
        # The generated file is just javascript, so it's not executable.
        # Instead we arrange for it to be run with a javascript shell.
        if args is None:
            args = []
        if isinstance(args, basestring):
            args = str(executable) + " " + args
        else:
            args = [str(executable)] + args
        jsshell = find_javascript_shell()
        super_cls = super(EmscriptenPlatform, self)
        return super_cls.execute(jsshell, args, env, *a, **k)

    def include_dirs_for_libffi(self):
        return []

    def library_dirs_for_libffi(self):
        return []

    def gen_makefile(self, cfiles, eci, exe_name=None, path=None,
                     shared=False, headers_to_precompile=[],
                     no_precompile_cfiles=[], icon=None):
        m = super(EmscriptenPlatform, self).gen_makefile(
            cfiles, eci, exe_name, path, shared,
            headers_to_precompile, no_precompile_cfiles,
        )
        # We want to mutate the flags, so convert from tuples to lists.
        ldflags = list(m.lines[m.defs["LDFLAGS"]].value)
        cflags = list(m.lines[m.defs["CFLAGS"]].value)
        m.lines[m.defs["LDFLAGS"]].value = ldflags
        m.lines[m.defs["CFLAGS"]].value = cflags
        # Find what debug level we're linking at, based on environ flags.
        debug_level = 0
        for flag in ldflags:
            if flag.startswith("-g"):
                if flag == "-g":
                    debug_level = 3
                else:
                    debug_level = int(flag[2:])
        # At debug level zero, insert a stub header file that avoids
        # capturing rpython-level traceback information.  Such tracebacks
        # should only be for interpreter debugging, and add many megabytes
        # of strings to the final build size.
        if debug_level == 0:
            incl = os.path.join(os.path.dirname(__file__), "no-debug")
            cflags.insert(0, "-I")
            cflags.insert(1, incl)
        # Export all the entry-point functions, plus a few generally-useful
        # helper functions.  Unfortunately there's no nice API for getting
        # their names, so we search for RPY_EXPORTED symbols in known files.
        # We may have to grep forwarddecl.h for PY_EXPORTED
        """exports = ["main", "free", "jitInvoke", "emjs_make_handle",
                   "emjs_free", "pypy_g_entry_point"]
        with m.makefile_dir.join("forwarddecl.h").open("r") as decls:
            for ln in decls:
                if ln.startswith("RPY_EXPORTED "):
                    name = ln.strip().split(None, 2)[-1].split("(", 1)[0]
                    exports.append(name)
        exports = repr(["_" + nm for nm in exports])"""
        #idx = ldflags.index("EXPORT_ALL=1")
        #del ldflags[idx - 1 : idx + 1]
        #ldflags.extend([
        #    "-s", repr("EXPORTED_FUNCTIONS=%s" % (exports,)),
        #])
        # Do more aggressive (hence more expensive) optimization for final
        # linkage.  Note that this only applies to emscripten itself; llvm
        # still sees "-Os" due to separate use of --llvm-opts.
        # The most important thing this enables is the registerizeHarder pass.
        #ldflags.remove("-Os")
        #ldflags.extend([
        #    "-O3",
        #])
        # Use -Oz for final LLVM LTO.  This avoids adding extra size
        # to the final output file, while still allowing some cross-file
        # optimizations to be performed.
        #idx = ldflags.index("--llvm-opts")
        #ldflags[idx + 1] = repr(["-Oz"] + self.llvm_opts)
        # Ensure that --llvm-opts appears properly quoted in the makefile.
        #idx = ldflags.index("--llvm-opts")
        #ldflags[idx + 1] = repr(ldflags[idx + 1])
        #idx = cflags.index("--llvm-opts")
        #cflags[idx + 1] = repr(cflags[idx + 1])
        # Use closure on the non-asm javascript.
        # This will eliminate any names not exported above.
        # XXX TODO: need to include FS-using code before doing this.
        # Maybe we can do it in a separate pass?
        #ldflags.extend([
        #    "--closure", "1",
        #])
        m.lines[m.defs["LDFLAGS_LINK"]].value = list(ldflags)
        return m 

    def execute_makefile(self, path_to_makefile, extra_opts=[]):
        if isinstance(path_to_makefile, GnuMakefile):
            path = path_to_makefile.makefile_dir
        else:
            path = path_to_makefile
        log.execute('make %s in %s' % (" ".join(extra_opts), path))
        env = os.environ.copy()
        returncode, stdout, stderr = _run_subprocess(
            self.make_cmd, ['-C', str(path)] + extra_opts, env=env)
        self._handle_error(returncode, stdout, stderr, path.join('make'))

    def _args_for_shared(self, args):
        return args
