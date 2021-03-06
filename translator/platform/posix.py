"""Base support for POSIX-like platforms."""

import py, os, sys

from rpython.translator.platform import Platform, log, _run_subprocess

import rpython
rpydir = str(py.path.local(rpython.__file__).join('..'))

class BasePosix(Platform):
    exe_ext = ''
    make_cmd = 'make'

    relevant_environ = ('CPATH', 'LIBRARY_PATH', 'C_INCLUDE_PATH')

    DEFAULT_CC = 'gcc'
    rpath_flags = ['-Wl,-rpath=\'$$ORIGIN/\'']

    def __init__(self, cc=None):
        self.cc = cc or os.environ.get('CC', self.DEFAULT_CC)

    def _libs(self, libraries):
        return ['-l%s' % lib for lib in libraries]

    def _libdirs(self, library_dirs):
        assert '' not in library_dirs
        return ['-L%s' % ldir for ldir in library_dirs]

    def _includedirs(self, include_dirs):
        assert '' not in include_dirs
        return ['-I%s' % idir for idir in include_dirs]

    def _linkfiles(self, link_files):
        return list(link_files)

    def _compile_c_file(self, cc, cfile, compile_args):
        oname = self._make_o_file(cfile, ext='o')
        args = ['-c'] + compile_args + [str(cfile), '-o', str(oname)]
        self._execute_c_compiler(cc, args, oname,
                                 cwd=str(cfile.dirpath()))
        return oname

    def _link_args_from_eci(self, eci, standalone):
        return Platform._link_args_from_eci(self, eci, standalone)

    def _exportsymbols_link_flags(self):
        if (self.cc == 'mingw32' or (self.cc== 'gcc' and os.name=='nt')
                or sys.platform == 'cygwin'):
            return ["-Wl,--export-all-symbols"]
        return ["-Wl,--export-dynamic"]

    def _link(self, cc, ofiles, link_args, standalone, exe_name):
        args = [str(ofile) for ofile in ofiles] + link_args
        args += ['-o', str(exe_name)]
        if not standalone:
            args = self._args_for_shared(args)
        self._execute_c_compiler(cc, args, exe_name,
                                 cwd=str(exe_name.dirpath()))
        return exe_name

    def _pkg_config(self, lib, opt, default, check_result_dir=False):
        try:
            ret, out, err = _run_subprocess("pkg-config", [lib, opt])
        except OSError, e:
            err = str(e)
            ret = 1
        if ret:
            result = default
        else:
            # strip compiler flags
            result = [entry[2:] for entry in out.split()]
        #
        if not result:
            pass # if pkg-config explicitly returned nothing, then
                 # we assume it means no options are needed
        elif check_result_dir:
            # check that at least one of the results is a valid dir
            for check in result:
                if os.path.isdir(check):
                    break
            else:
                if ret:
                    msg = ("running 'pkg-config %s %s' failed:\n%s\n"
                           "and the default %r is not a valid directory" % (
                        lib, opt, err.rstrip(), default))
                else:
                    msg = ("'pkg-config %s %s' returned no valid directory:\n"
                           "%s\n%s" % (lib, opt, out.rstrip(), err.rstrip()))
                raise ValueError(msg)
        return result

    def get_rpath_flags(self, rel_libdirs):
        # needed for cross-compilation i.e. ARM
        return self.rpath_flags + ['-Wl,-rpath-link=\'%s\'' % ldir
                                    for ldir in rel_libdirs]

    def get_shared_only_compile_flags(self):
        return tuple(self.shared_only) + ('-fvisibility=hidden',)

    def gen_makefile(self, cfiles, eci, exe_name=None, path=None,
                     shared=False, headers_to_precompile=[],
                     no_precompile_cfiles = [], icon=None):
        cfiles = self._all_cfiles(cfiles, eci)

        if path is None:
            path = cfiles[0].dirpath()

        rpypath = py.path.local(rpydir)

        if exe_name is None:
            exe_name = cfiles[0].new(ext=self.exe_ext)
        else:
            exe_name = exe_name.new(ext=self.exe_ext)

        linkflags = list(self.link_flags)
        if shared:
            linkflags = self._args_for_shared(linkflags)

        linkflags += self._exportsymbols_link_flags()

        if shared:
            libname = exe_name.new(ext='').basename
            target_name = 'lib' + exe_name.new(ext=self.so_ext).basename
        else:
            target_name = exe_name.basename

        if shared:
            cflags = tuple(self.cflags) + self.get_shared_only_compile_flags()
        else:
            cflags = tuple(self.cflags) + tuple(self.standalone_only)

        m = GnuMakefile(path)
        m.exe_name = path.join(exe_name.basename)
        m.eci = eci

        def rpyrel(fpath):
            lpath = py.path.local(fpath)
            rel = lpath.relto(rpypath)
            if rel:
                return os.path.join('$(RPYDIR)', rel)
            m_dir = m.makefile_dir
            if m_dir == lpath:
                return '.'
            if m_dir.dirpath() == lpath:
                return '..'
            return fpath

        rel_cfiles = [m.pathrel(cfile) for cfile in cfiles]
        rel_ofiles = [rel_cfile[:rel_cfile.rfind('.')]+'.o' for rel_cfile in rel_cfiles]
        m.cfiles = rel_cfiles

        rel_includedirs = [rpyrel(incldir) for incldir in
                           self.preprocess_include_dirs(eci.include_dirs)]
        rel_libdirs = [rpyrel(libdir) for libdir in
                       self.preprocess_library_dirs(eci.library_dirs)]

        m.comment('automatically generated makefile')
        definitions = [
            ('RPYDIR', '"%s"' % rpydir),
            ('TARGET', target_name),
            ('DEFAULT_TARGET', exe_name.basename),
            ('SOURCES', rel_cfiles),
            ('OBJECTS', rel_ofiles),
            ('LIBS', self._libs(eci.libraries) + list(self.extra_libs)),
            ('LIBDIRS', self._libdirs(rel_libdirs)),
            ('INCLUDEDIRS', self._includedirs(rel_includedirs)),
            ('CFLAGS', cflags),
            ('CFLAGSEXTRA', list(eci.compile_extra)),
            ('LDFLAGS', linkflags),
            ('LDFLAGS_LINK', list(self.link_flags)),
            ('LDFLAGSEXTRA', list(eci.link_extra)),
            ('CC', self.cc),
            ('CC_LINK', eci.use_cpp_linker and 'g++' or '$(CC)'),
            ('LINKFILES', eci.link_files),
            ('RPATH_FLAGS', self.get_rpath_flags(rel_libdirs)),
            ]
        for args in definitions:
            m.definition(*args)

        rules = [
            ('all', '$(DEFAULT_TARGET)', []),
            ('$(TARGET)', '$(OBJECTS)', '$(CC_LINK) $(LDFLAGSEXTRA) -o $@ $(OBJECTS) $(LIBDIRS) $(LIBS) $(LINKFILES) $(LDFLAGS)'),
            ('%.o', '%.c', '$(CC) $(CFLAGS) $(CFLAGSEXTRA) -o $@ -c $< $(INCLUDEDIRS)'),
            ('%.o', '%.cxx', '$(CXX) $(CFLAGS) $(CFLAGSEXTRA) -o $@ -c $< $(INCLUDEDIRS)'),
            ]

        for rule in rules:
            m.rule(*rule)

        if shared:
            m.definition('SHARED_IMPORT_LIB', libname),
            m.definition('PYPY_MAIN_FUNCTION', "pypy_main_startup")
            m.rule('main.c', '',
                   'echo "'
                   'int $(PYPY_MAIN_FUNCTION)(int, char*[]); '
                   'int main(int argc, char* argv[]) '
                   '{ return $(PYPY_MAIN_FUNCTION)(argc, argv); }" > $@')
            m.rule('$(DEFAULT_TARGET)', ['$(TARGET)', 'main.o'],
                   '$(CC_LINK) $(LDFLAGS_LINK) main.o -L. -l$(SHARED_IMPORT_LIB) -o $@ $(RPATH_FLAGS)')

        return m

    def execute_makefile(self, path_to_makefile, extra_opts=[]):
        if isinstance(path_to_makefile, GnuMakefile):
            path = path_to_makefile.makefile_dir
        else:
            path = path_to_makefile
        log.execute('make %s in %s' % (" ".join(extra_opts), path))
        returncode, stdout, stderr = _run_subprocess(
            self.make_cmd, ['-C', str(path)] + extra_opts)
        self._handle_error(returncode, stdout, stderr, path.join('make'))

class Definition(object):
    def __init__(self, name, value):
        self.name = name
        self.value = value

    def write(self, f):
        def write_list(prefix, lst):
            lst = lst or ['']
            for i, fn in enumerate(lst):
                fn = fn.replace('\\', '\\\\')
                print >> f, prefix, fn,
                if i < len(lst)-1:
                    print >> f, '\\'
                else:
                    print >> f
                prefix = ' ' * len(prefix)
        name, value = self.name, self.value
        if isinstance(value, str):
            f.write('%s = %s\n' % (name, value.replace('\\', '\\\\')))
        else:
            write_list('%s =' % (name,), value)
        f.write('\n')

class Rule(object):
    def __init__(self, target, deps, body):
        self.target = target
        self.deps   = deps
        self.body   = body

    def write(self, f):
        target, deps, body = self.target, self.deps, self.body
        if isinstance(deps, str):
            dep_s = deps
        else:
            dep_s = ' '.join(deps)
        f.write('%s: %s\n' % (target, dep_s))
        if isinstance(body, str):
            f.write('\t%s\n' % body)
        elif body:
            f.write('\t%s\n' % '\n\t'.join(body))
        f.write('\n')

class Comment(object):
    def __init__(self, body):
        self.body = body

    def write(self, f):
        f.write('# %s\n' % (self.body,))

class GnuMakefile(object):
    def __init__(self, path=None):
        self.defs = {}
        self.lines = []
        self.makefile_dir = py.path.local(path)

    def pathrel(self, fpath):
        if fpath.dirpath() == self.makefile_dir:
            return fpath.basename
        elif fpath.dirpath().dirpath() == self.makefile_dir.dirpath():
            assert fpath.relto(self.makefile_dir.dirpath()), (
                "%r should be relative to %r" % (
                    fpath, self.makefile_dir.dirpath()))
            path = '../' + fpath.relto(self.makefile_dir.dirpath())
            return path.replace('\\', '/')
        else:
            return str(fpath)

    def definition(self, name, value):
        defs = self.defs
        defn = Definition(name, value)
        if name in defs:
            self.lines[defs[name]] = defn
        else:
            defs[name] = len(self.lines)
            self.lines.append(defn)

    def rule(self, target, deps, body):
        self.lines.append(Rule(target, deps, body))

    def comment(self, body):
        self.lines.append(Comment(body))

    def write(self, out=None):
        if out is None:
            f = self.makefile_dir.join('Makefile').open('w')
        else:
            f = out
        for line in self.lines:
            line.write(f)
        f.flush()
        if out is None:
            f.close()
