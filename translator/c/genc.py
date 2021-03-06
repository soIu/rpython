import contextlib
import py
import sys, os
from rpython.rlib import exports
from rpython.rlib.entrypoint import entrypoint
from rpython.rtyper.typesystem import getfunctionptr
from rpython.rtyper.lltypesystem import lltype, rffi
from rpython.tool import runsubprocess
from rpython.tool.nullpath import NullPyPathLocal
from rpython.tool.udir import udir
from rpython.translator.c import gc
from rpython.translator.c.database import LowLevelDatabase
from rpython.translator.c.extfunc import pre_include_code_lines
from rpython.translator.c.support import log
from rpython.translator.gensupp import uniquemodulename, NameManager
from rpython.translator.tool.cbuild import ExternalCompilationInfo

_CYGWIN = sys.platform == 'cygwin'

_CPYTHON_RE = py.std.re.compile('^Python 2.[567]')

def get_recent_cpython_executable():

    if sys.platform == 'win32':
        python = sys.executable.replace('\\', '/')
    else:
        python = sys.executable
    # Is there a command 'python' that runs python 2.5-2.7?
    # If there is, then we can use it instead of sys.executable
    returncode, stdout, stderr = runsubprocess.run_subprocess(
        "python", "-V")
    if _CPYTHON_RE.match(stdout) or _CPYTHON_RE.match(stderr):
        python = 'python'
    return python


class ProfOpt(object):
    #XXX assuming gcc style flags for now
    name = "profopt"

    def __init__(self, compiler):
        self.compiler = compiler

    def first(self):
        return self.build('-fprofile-generate')

    def probe(self, exe, args):
        # 'args' is a single string typically containing spaces
        # and quotes, which represents several arguments.
        self.compiler.platform.execute(exe, args)

    def after(self):
        return self.build('-fprofile-use')

    def build(self, option):
        eci = ExternalCompilationInfo(compile_extra=[option],
                                      link_extra=[option])
        return self.compiler._build(eci)

class CCompilerDriver(object):
    def __init__(self, platform, cfiles, eci, outputfilename=None,
                 profbased=False):
        # XXX config might contain additional link and compile options.
        #     We need to fish for it somehow.
        self.platform = platform
        self.cfiles = cfiles
        self.eci = eci
        self.outputfilename = outputfilename
        self.profbased = profbased

    def _build(self, eci=ExternalCompilationInfo(), shared=False):
        outputfilename = self.outputfilename
        if shared:
            if outputfilename:
                basename = outputfilename
            else:
                basename = self.cfiles[0].purebasename
            outputfilename = 'lib' + basename
        return self.platform.compile(self.cfiles, self.eci.merge(eci),
                                     outputfilename=outputfilename,
                                     standalone=not shared)

    def build(self, shared=False):
        if self.profbased:
            return self._do_profbased()
        return self._build(shared=shared)

    def _do_profbased(self):
        ProfDriver, args = self.profbased
        profdrv = ProfDriver(self)
        dolog = getattr(log, profdrv.name)
        dolog(args)
        exename = profdrv.first()
        dolog('Gathering profile data from: %s %s' % (
            str(exename), args))
        profdrv.probe(exename, args)
        return profdrv.after()

class CBuilder(object):
    c_source_filename = None
    _compiled = False
    modulename = None
    split = False

    def __init__(self, translator, entrypoint, config, gcpolicy=None,
            secondary_entrypoints=()):
        self.translator = translator
        self.entrypoint = entrypoint
        self.entrypoint_name = getattr(self.entrypoint, 'func_name', None)
        self.originalentrypoint = entrypoint
        self.config = config
        self.gcpolicy = gcpolicy    # for tests only, e.g. rpython/memory/
        self.eci = self.get_eci()
        self.secondary_entrypoints = secondary_entrypoints

    def get_eci(self):
        pypy_include_dir = py.path.local(__file__).join('..')
        include_dirs = [pypy_include_dir]
        return ExternalCompilationInfo(include_dirs=include_dirs)

    def build_database(self):
        translator = self.translator

        gcpolicyclass = self.get_gcpolicyclass()

        if self.config.translation.gcrootfinder == "asmgcc":
            if not self.standalone:
                raise NotImplementedError("--gcrootfinder=asmgcc requires standalone")

        db = LowLevelDatabase(translator, standalone=self.standalone,
                              gcpolicyclass=gcpolicyclass,
                              thread_enabled=self.config.translation.thread,
                              sandbox=self.config.translation.sandbox)
        self.db = db

        # give the gc a chance to register interest in the start-up functions it
        # need (we call this for its side-effects of db.get())
        list(db.gcpolicy.gc_startup_code())

        # build entrypoint and eventually other things to expose
        pf = self.getentrypointptr()
        if isinstance(pf, list):
            for one_pf in pf:
                db.get(one_pf)
            self.c_entrypoint_name = None
        else:
            pfname = db.get(pf)

            for func, _ in self.secondary_entrypoints:
                bk = translator.annotator.bookkeeper
                db.get(getfunctionptr(bk.getdesc(func).getuniquegraph()))

            self.c_entrypoint_name = pfname

        for obj in exports.EXPORTS_obj2name.keys():
            db.getcontainernode(obj)
        exports.clear()
        db.complete()

        self.collect_compilation_info(db)
        return db

    have___thread = None

    def merge_eci(self, *ecis):
        self.eci = self.eci.merge(*ecis)

    def collect_compilation_info(self, db):
        # we need a concrete gcpolicy to do this
        self.merge_eci(db.gcpolicy.compilation_info())

        all = []
        for node in self.db.globalcontainers():
            eci = node.compilation_info()
            if eci:
                all.append(eci)
        for node in self.db.getstructdeflist():
            try:
                all.append(node.STRUCT._hints['eci'])
            except (AttributeError, KeyError):
                pass
        self.merge_eci(*all)

    def get_gcpolicyclass(self):
        if self.gcpolicy is None:
            name = self.config.translation.gctransformer
            if name == "framework":
                name = "%s+%s" % (name, self.config.translation.gcrootfinder)
            return gc.name_to_gcpolicy[name]
        return self.gcpolicy

    # use generate_source(defines=DEBUG_DEFINES) to force the #definition
    # of the macros that enable debugging assertions
    DEBUG_DEFINES = {'RPY_ASSERT': 1,
                     'RPY_LL_ASSERT': 1}

    def generate_graphs_for_llinterp(self, db=None):
        # prepare the graphs as when the source is generated, but without
        # actually generating the source.
        if db is None:
            db = self.build_database()
        graphs = db.all_graphs()
        db.gctransformer.prepare_inline_helpers(graphs)
        for node in db.containerlist:
            if hasattr(node, 'funcgens'):
                for funcgen in node.funcgens:
                    funcgen.patch_graph(copy_graph=False)
        return db

    def generate_source(self, db=None, defines={}, exe_name=None):
        assert self.c_source_filename is None

        if db is None:
            db = self.build_database()
        pf = self.getentrypointptr()
        if self.modulename is None:
            self.modulename = uniquemodulename('testing')
        modulename = self.modulename
        targetdir = udir.ensure(modulename, dir=1)
        if self.config.translation.dont_write_c_files:
            targetdir = NullPyPathLocal(targetdir)

        self.targetdir = targetdir
        defines = defines.copy()
        if self.config.translation.countmallocs:
            defines['COUNT_OP_MALLOCS'] = 1
        if self.config.translation.sandbox:
            defines['RPY_SANDBOXED'] = 1
        if CBuilder.have___thread is None:
            CBuilder.have___thread = self.translator.platform.check___thread()
        if not self.standalone:
            assert not self.config.translation.instrument
        else:
            defines['PYPY_STANDALONE'] = db.get(pf)
            if self.config.translation.instrument:
                defines['PYPY_INSTRUMENT'] = 1
            if CBuilder.have___thread:
                if not self.config.translation.no__thread:
                    defines['USE___THREAD'] = 1
            if self.config.translation.shared:
                defines['PYPY_MAIN_FUNCTION'] = "pypy_main_startup"
        self.eci, cfile, extra, headers_to_precompile = \
                gen_source(db, modulename, targetdir,
                           self.eci, defines=defines, split=self.split)
        self.c_source_filename = py.path.local(cfile)
        self.extrafiles = self.eventually_copy(extra)
        self.gen_makefile(targetdir, exe_name=exe_name,
                          headers_to_precompile=headers_to_precompile)
        return cfile

    def eventually_copy(self, cfiles):
        extrafiles = []
        for fn in cfiles:
            fn = py.path.local(fn)
            if not fn.relto(udir):
                newname = self.targetdir.join(fn.basename)
                fn.copy(newname)
                fn = newname
            extrafiles.append(fn)
        return extrafiles


class CStandaloneBuilder(CBuilder):
    standalone = True
    split = True
    executable_name = None
    shared_library_name = None

    def getprofbased(self):
        profbased = None
        if self.config.translation.instrumentctl is not None:
            profbased = self.config.translation.instrumentctl
        else:
            # xxx handling config.translation.profopt is a bit messy, because
            # it could be an empty string (not to be confused with None) and
            # because noprofopt can be used as an override.
            profopt = self.config.translation.profopt
            if profopt is not None and not self.config.translation.noprofopt:
                profbased = (ProfOpt, profopt)
        return profbased

    def has_profopt(self):
        profbased = self.getprofbased()
        retval = (profbased and isinstance(profbased, tuple)
                and profbased[0] is ProfOpt)
        if retval and self.translator.platform.name == 'msvc':
            raise ValueError('Cannot do profile based optimization on MSVC,'
                    'it is not supported in free compiler version')

    def getentrypointptr(self):
        # XXX check that the entrypoint has the correct
        # signature:  list-of-strings -> int
        bk = self.translator.annotator.bookkeeper
        return getfunctionptr(bk.getdesc(self.entrypoint).getuniquegraph())

    def cmdexec(self, args='', env=None, err=False, expect_crash=False, exe=None):
        assert self._compiled
        if sys.platform == 'win32':
            #Prevent opening a dialog box
            import ctypes
            winapi = ctypes.windll.kernel32
            SetErrorMode = winapi.SetErrorMode
            SetErrorMode.argtypes=[ctypes.c_int]

            SEM_FAILCRITICALERRORS = 1
            SEM_NOGPFAULTERRORBOX  = 2
            SEM_NOOPENFILEERRORBOX = 0x8000
            flags = SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX \
                    | SEM_NOOPENFILEERRORBOX
            #Since there is no GetErrorMode, do a double Set
            old_mode = SetErrorMode(flags)
            SetErrorMode(old_mode | flags)
        if env is None:
            envrepr = ''
        else:
            envrepr = ' [env=%r]' % (env,)
        if exe is None:
            exe = self.executable_name
        log.cmdexec('%s %s%s' % (exe, args, envrepr))
        res = self.translator.platform.execute(exe, args, env=env)
        if sys.platform == 'win32':
            SetErrorMode(old_mode)
        if res.returncode != 0:
            if expect_crash:
                return res.out, res.err
            print >> sys.stderr, res.err
            raise Exception("Returned %d" % (res.returncode,))
        if expect_crash:
            raise Exception("Program did not crash!")
        if err:
            return res.out, res.err
        return res.out

    def build_main_for_shared(self, shared_library_name, entrypoint, exe_name):
        import time
        time.sleep(1)
        self.shared_library_name = shared_library_name
        # build main program
        eci = self.get_eci()
        kw = {}
        if self.translator.platform.cc == 'gcc':
            kw['libraries'] = [self.shared_library_name.purebasename[3:]]
            kw['library_dirs'] = [self.targetdir]
        else:
            kw['libraries'] = [self.shared_library_name.new(ext='')]
        eci = eci.merge(ExternalCompilationInfo(
            separate_module_sources=['''
                int %s(int argc, char* argv[]);

                int main(int argc, char* argv[])
                { return %s(argc, argv); }
                ''' % (entrypoint, entrypoint)
                ],
            **kw
            ))
        eci = eci.convert_sources_to_files(
            cache_dir=self.targetdir)
        return self.translator.platform.compile(
            [], eci,
            outputfilename=exe_name)

    def compile(self, exe_name=None):
        assert self.c_source_filename
        assert not self._compiled

        shared = self.config.translation.shared

        extra_opts = []
        if self.config.translation.make_jobs != 1:
            extra_opts += ['-j', str(self.config.translation.make_jobs)]
        if self.config.translation.lldebug:
            extra_opts += ["lldebug"]
        elif self.config.translation.lldebug0:
            extra_opts += ["lldebug0"]
        self.translator.platform.execute_makefile(self.targetdir,
                                                  extra_opts)
        if shared:
            self.shared_library_name = self.executable_name.new(
                purebasename='lib' + self.executable_name.purebasename,
                ext=self.translator.platform.so_ext)
        self._compiled = True
        return self.executable_name

    def gen_makefile(self, targetdir, exe_name=None, headers_to_precompile=[]):
        module_files = self.eventually_copy(self.eci.separate_module_files)
        self.eci.separate_module_files = []
        cfiles = [self.c_source_filename] + self.extrafiles + list(module_files)
        if exe_name is not None:
            exe_name = targetdir.join(exe_name)
        mk = self.translator.platform.gen_makefile(
            cfiles, self.eci,
            path=targetdir, exe_name=exe_name,
            headers_to_precompile=headers_to_precompile,
            no_precompile_cfiles = module_files,
            shared=self.config.translation.shared,
            icon=self.config.translation.icon)

        if self.has_profopt():
            profopt = self.config.translation.profopt
            mk.definition('ABS_TARGET', str(targetdir.join('$(TARGET)')))
            mk.definition('DEFAULT_TARGET', 'profopt')
            mk.definition('PROFOPT', profopt)

        rules = [
            ('clean', '', 'rm -f $(OBJECTS) $(DEFAULT_TARGET) $(TARGET) $(GCMAPFILES) $(ASMFILES) *.gc?? ../module_cache/*.gc??'),
            ('clean_noprof', '', 'rm -f $(OBJECTS) $(DEFAULT_TARGET) $(TARGET) $(GCMAPFILES) $(ASMFILES)'),
            ('debug', '', '$(MAKE) CFLAGS="$(DEBUGFLAGS) -DRPY_ASSERT" debug_target'),
            ('debug_exc', '', '$(MAKE) CFLAGS="$(DEBUGFLAGS) -DRPY_ASSERT -DDO_LOG_EXC" debug_target'),
            ('debug_mem', '', '$(MAKE) CFLAGS="$(DEBUGFLAGS) -DRPY_ASSERT -DPYPY_USE_TRIVIAL_MALLOC" debug_target'),
            ('llsafer', '', '$(MAKE) CFLAGS="-O2 -DRPY_LL_ASSERT" $(DEFAULT_TARGET)'),
            ('lldebug', '', '$(MAKE) CFLAGS="$(DEBUGFLAGS) -DRPY_ASSERT -DRPY_LL_ASSERT" debug_target'),
            ('lldebug0','', '$(MAKE) CFLAGS="$(DEBUGFLAGS) -O0 -DMAX_STACK_SIZE=8192000 -DRPY_ASSERT -DRPY_LL_ASSERT" debug_target'),
            ('profile', '', '$(MAKE) CFLAGS="-g -O1 -pg $(CFLAGS) -fno-omit-frame-pointer" LDFLAGS="-pg $(LDFLAGS)" $(DEFAULT_TARGET)'),
            ]
        if self.has_profopt():
            rules.append(
                ('profopt', '', [
                '$(MAKENOPROF)',
                '$(MAKE) CFLAGS="-fprofile-generate $(CFLAGS)" LDFLAGS="-fprofile-generate $(LDFLAGS)" $(TARGET)',
                'cd $(RPYDIR)/translator/goal && $(ABS_TARGET) $(PROFOPT)',
                '$(MAKE) clean_noprof',
                '$(MAKE) CFLAGS="-fprofile-use $(CFLAGS)" LDFLAGS="-fprofile-use $(LDFLAGS)" $(TARGET)']))
        for rule in rules:
            mk.rule(*rule)

        #XXX: this conditional part is not tested at all
        if self.config.translation.gcrootfinder == 'asmgcc':
            if self.translator.platform.name == 'msvc':
                raise Exception("msvc no longer supports asmgcc")
            if self.config.translation.shared:
                mk.definition('DEBUGFLAGS', '-O2 -fomit-frame-pointer -g -fPIC')
            else:
                mk.definition('DEBUGFLAGS', '-O2 -fomit-frame-pointer -g')

            if self.config.translation.shared:
                mk.definition('PYPY_MAIN_FUNCTION', "pypy_main_startup")
            else:
                mk.definition('PYPY_MAIN_FUNCTION', "main")

            mk.definition('PYTHON', get_recent_cpython_executable())

            mk.definition('GCMAPFILES', '$(subst .asmgcc.s,.gcmap,$(subst .c,.gcmap,$(SOURCES)))')
            mk.definition('OBJECTS1', '$(subst .asmgcc.s,.o,$(subst .c,.o,$(SOURCES)))')
            mk.definition('OBJECTS', '$(OBJECTS1) gcmaptable.s')


            # the rule that transforms %.c into %.o, by compiling it to
            # %.s, then applying trackgcroot to get %.lbl.s and %.gcmap, and
            # finally by using the assembler ($(CC) again for now) to get %.o
            mk.rule('%.o %.gcmap', '%.c', [
                '$(CC) $(CFLAGS) $(CFLAGSEXTRA) -frandom-seed=$< '
                    '-o $*.s -S $< $(INCLUDEDIRS)',
                '$(PYTHON) $(RPYDIR)/translator/c/gcc/trackgcroot.py '
                    '-t $*.s > $*.gctmp',
                '$(CC) $(CFLAGS) -o $*.o -c $*.lbl.s',
                'mv $*.gctmp $*.gcmap',
                'rm $*.s $*.lbl.s'])

            # this is for manually written assembly files which needs to be parsed by asmgcc
            mk.rule('%.o %.gcmap', '%.asmgcc.s', [
                '$(PYTHON) $(RPYDIR)/translator/c/gcc/trackgcroot.py '
                    '-t $*.asmgcc.s > $*.gctmp',
                '$(CC) -o $*.o -c $*.asmgcc.lbl.s',
                'mv $*.gctmp $*.gcmap',
                'rm $*.asmgcc.lbl.s'])
            
            # the rule to compute gcmaptable.s
            mk.rule('gcmaptable.s', '$(GCMAPFILES)',
                    [
                         '$(PYTHON) $(RPYDIR)/translator/c/gcc/trackgcroot.py '
                         '$(GCMAPFILES) > $@.tmp',
                     'mv $@.tmp $@'])

        else:
            if self.translator.platform.name == 'msvc':
                mk.definition('DEBUGFLAGS', '-MD -Zi')
            else:
                if self.config.translation.shared:
                    mk.definition('DEBUGFLAGS', '-O1 -g -fPIC')
                else:
                    mk.definition('DEBUGFLAGS', '-O1 -g')
        if self.translator.platform.name == 'msvc':
            mk.rule('debug_target', 'debugmode_$(DEFAULT_TARGET)', 'rem')
        else:
            mk.rule('debug_target', '$(DEFAULT_TARGET)', '#')
        mk.write()
        #self.translator.platform,
        #                           ,
        #                           self.eci, profbased=self.getprofbased()
        self.executable_name = mk.exe_name

# ____________________________________________________________

SPLIT_CRITERIA = 65535 # support VC++ 7.2
#SPLIT_CRITERIA = 32767 # enable to support VC++ 6.0

MARKER = '/*/*/' # provide an easy way to split after generating

class SourceGenerator:
    one_source_file = True

    def __init__(self, database):
        self.database = database
        self.extrafiles = []
        self.headers_to_precompile = []
        self.path = None
        self.namespace = NameManager()

    def set_strategy(self, path, split=True):
        all_nodes = list(self.database.globalcontainers())
        # split off non-function nodes. We don't try to optimize these, yet.
        funcnodes = []
        othernodes = []
        for node in all_nodes:
            if node.nodekind == 'func':
                funcnodes.append(node)
            else:
                othernodes.append(node)
        if split:
            self.one_source_file = False
        self.funcnodes = funcnodes
        self.othernodes = othernodes
        self.path = path

    def uniquecname(self, name):
        assert name.endswith('.c')
        return self.namespace.uniquename(name[:-2]) + '.c'

    def makefile(self, name):
        log.writing(name)
        filepath = self.path.join(name)
        if name.endswith('.c'):
            self.extrafiles.append(filepath)
        if name.endswith('.h'):
            self.headers_to_precompile.append(filepath)
        return filepath.open('w')

    def getextrafiles(self):
        return self.extrafiles

    def getothernodes(self):
        return self.othernodes[:]

    def getbasecfilefornode(self, node, basecname):
        # For FuncNode instances, use the python source filename (relative to
        # the top directory):
        def invent_nice_name(g):
            # Lookup the filename from the function.
            # However, not all FunctionGraph objs actually have a "func":
            if hasattr(g, 'func'):
                if g.filename.endswith('.py'):
                    localpath = py.path.local(g.filename)
                    pypkgpath = localpath.pypkgpath()
                    if pypkgpath:
                        relpypath = localpath.relto(pypkgpath.dirname)
                        assert relpypath, ("%r should be relative to %r" %
                            (localpath, pypkgpath.dirname))
                        return relpypath.replace('.py', '.c')
            return None
        if hasattr(node.obj, 'graph'):
            # Regular RPython functions
            name = invent_nice_name(node.obj.graph)
            if name is not None:
                return name
        elif node._funccodegen_owner is not None:
            # Data nodes that belong to a known function
            graph = getattr(node._funccodegen_owner, 'graph', None)
            name = invent_nice_name(graph)
            if name is not None:
                return "data_" + name
        return basecname

    def splitnodesimpl(self, basecname, nodes, nextra, nbetween,
                       split_criteria=SPLIT_CRITERIA):
        # Gather nodes by some criteria:
        nodes_by_base_cfile = {}
        for node in nodes:
            c_filename = self.getbasecfilefornode(node, basecname)
            if c_filename in nodes_by_base_cfile:
                nodes_by_base_cfile[c_filename].append(node)
            else:
                nodes_by_base_cfile[c_filename] = [node]

        # produce a sequence of nodes, grouped into files
        # which have no more than SPLIT_CRITERIA lines
        for basecname in sorted(nodes_by_base_cfile):
            iternodes = iter(nodes_by_base_cfile[basecname])
            done = [False]
            def subiter():
                used = nextra
                for node in iternodes:
                    impl = '\n'.join(list(node.implementation())).split('\n')
                    if not impl:
                        continue
                    cost = len(impl) + nbetween
                    yield node, impl
                    del impl
                    if used + cost > split_criteria:
                        # split if criteria met, unless we would produce nothing.
                        raise StopIteration
                    used += cost
                done[0] = True
            while not done[0]:
                yield self.uniquecname(basecname), subiter()

    @contextlib.contextmanager
    def write_on_included_file(self, f, name):
        fi = self.makefile(name)
        print >> f, '#include "%s"' % name
        yield fi
        fi.close()

    @contextlib.contextmanager
    def write_on_maybe_separate_source(self, f, name):
        print >> f, '/* %s */' % name
        if self.one_source_file:
            yield f
        else:
            fi = self.makefile(name)
            yield fi
            fi.close()

    def gen_readable_parts_of_source(self, f):
        split_criteria_big = SPLIT_CRITERIA
        if py.std.sys.platform != "win32":
            if self.database.gcpolicy.need_no_typeptr():
                pass    # XXX gcc uses toooooons of memory???
            else:
                split_criteria_big = SPLIT_CRITERIA * 4

        #
        # All declarations
        #
        with self.write_on_included_file(f, 'structdef.h') as fi:
            gen_structdef(fi, self.database)
        with self.write_on_included_file(f, 'forwarddecl.h') as fi:
            gen_forwarddecl(fi, self.database)
        with self.write_on_included_file(f, 'preimpl.h') as fi:
            gen_preimpl(fi, self.database)

        #
        # Implementation of functions and global structures and arrays
        #
        print >> f
        print >> f, '/***********************************************************/'
        print >> f, '/***  Implementations                                    ***/'
        print >> f

        print >> f, '#define PYPY_FILE_NAME "%s"' % os.path.basename(f.name)
        print >> f, '#include "src/g_include.h"'
        print >> f

        nextralines = 11 + 1
        for name, nodeiter in self.splitnodesimpl('nonfuncnodes.c',
                                                   self.othernodes,
                                                   nextralines, 1):
            with self.write_on_maybe_separate_source(f, name) as fc:
                if fc is not f:
                    print >> fc, '/***********************************************************/'
                    print >> fc, '/***  Non-function Implementations                       ***/'
                    print >> fc
                    print >> fc, '#include "common_header.h"'
                    print >> fc, '#include "structdef.h"'
                    print >> fc, '#include "forwarddecl.h"'
                    print >> fc, '#include "preimpl.h"'
                    print >> fc
                    print >> fc, '#include "src/g_include.h"'
                    print >> fc
                print >> fc, MARKER
                for node, impl in nodeiter:
                    print >> fc, '\n'.join(impl)
                    print >> fc, MARKER
                print >> fc, '/***********************************************************/'

        nextralines = 12
        for name, nodeiter in self.splitnodesimpl('implement.c',
                                                   self.funcnodes,
                                                   nextralines, 1,
                                                   split_criteria_big):
            with self.write_on_maybe_separate_source(f, name) as fc:
                if fc is not f:
                    print >> fc, '/***********************************************************/'
                    print >> fc, '/***  Implementations                                    ***/'
                    print >> fc
                    print >> fc, '#include "common_header.h"'
                    print >> fc, '#include "structdef.h"'
                    print >> fc, '#include "forwarddecl.h"'
                    print >> fc, '#include "preimpl.h"'
                    print >> fc, '#define PYPY_FILE_NAME "%s"' % name
                    print >> fc, '#include "src/g_include.h"'
                    print >> fc
                print >> fc, MARKER
                for node, impl in nodeiter:
                    print >> fc, '\n'.join(impl)
                    print >> fc, MARKER
                print >> fc, '/***********************************************************/'
        print >> f


def gen_structdef(f, database):
    structdeflist = database.getstructdeflist()
    print >> f, '/***********************************************************/'
    print >> f, '/***  Structure definitions                              ***/'
    print >> f
    print >> f, "#ifndef _PYPY_STRUCTDEF_H"
    print >> f, "#define _PYPY_STRUCTDEF_H"
    for node in structdeflist:
        if hasattr(node, 'forward_decl'):
            if node.forward_decl:
                print >> f, node.forward_decl
        elif node.name is not None:
            print >> f, '%s %s;' % (node.typetag, node.name)
    print >> f
    for node in structdeflist:
        for line in node.definition():
            print >> f, line
    gen_threadlocal_structdef(f, database)
    print >> f, "#endif"

def gen_threadlocal_structdef(f, database):
    from rpython.translator.c.support import cdecl
    print >> f
    bk = database.translator.annotator.bookkeeper
    fields = list(bk.thread_local_fields)
    fields.sort(key=lambda field: field.fieldname)
    for field in fields:
        print >> f, ('#define RPY_TLOFS_%s  offsetof(' % field.fieldname +
                     'struct pypy_threadlocal_s, %s)' % field.fieldname)
    print >> f, 'struct pypy_threadlocal_s {'
    print >> f, '\tint ready;'
    print >> f, '\tchar *stack_end;'
    for field in fields:
        typename = database.gettype(field.FIELDTYPE)
        print >> f, '\t%s;' % cdecl(typename, field.fieldname)
    print >> f, '};'
    print >> f

def gen_forwarddecl(f, database):
    print >> f, '/***********************************************************/'
    print >> f, '/***  Forward declarations                               ***/'
    print >> f
    print >> f, "#ifndef _PYPY_FORWARDDECL_H"
    print >> f, "#define _PYPY_FORWARDDECL_H"
    for node in database.globalcontainers():
        for line in node.forward_declaration():
            print >> f, line
    print >> f, "#endif"

def gen_preimpl(f, database):
    f.write('#ifndef _PY_PREIMPL_H\n#define _PY_PREIMPL_H\n')
    if database.translator is None or database.translator.rtyper is None:
        return
    preimplementationlines = pre_include_code_lines(
        database, database.translator.rtyper)
    for line in preimplementationlines:
        print >> f, line
    f.write('#endif /* _PY_PREIMPL_H */\n')    

def gen_startupcode(f, database):
    # generate the start-up code and put it into a function
    print >> f, 'char *RPython_StartupCode(void) {'
    print >> f, '\tchar *error = NULL;'

    bk = database.translator.annotator.bookkeeper
    if bk.thread_local_fields:
        print >> f, '\tRPython_ThreadLocals_ProgramInit();'

    for line in database.gcpolicy.gc_startup_code():
        print >> f,"\t" + line

    # put float infinities in global constants, we should not have so many of them for now to make
    # a table+loop preferable
    for dest, value in database.late_initializations:
        print >> f, "\t%s = %s;" % (dest, value)

    firsttime = True
    for node in database.containerlist:
        lines = list(node.startupcode())
        if lines:
            if firsttime:
                firsttime = False
            else:
                print >> f, '\tif (error) return error;'
            for line in lines:
                print >> f, '\t'+line

    print >> f, '\treturn error;'
    print >> f, '}'

def commondefs(defines):
    from rpython.rlib.rarithmetic import LONG_BIT, LONGLONG_BIT
    defines['PYPY_LONG_BIT'] = LONG_BIT
    defines['PYPY_LONGLONG_BIT'] = LONGLONG_BIT

def add_extra_files(eci):
    srcdir = py.path.local(__file__).join('..', 'src')
    files = [
        srcdir / 'entrypoint.c',       # ifdef PYPY_STANDALONE
        srcdir / 'mem.c',
        srcdir / 'exception.c',
        srcdir / 'rtyper.c',           # ifdef HAVE_RTYPER
        srcdir / 'support.c',
        srcdir / 'profiling.c',
        srcdir / 'debug_print.c',
        srcdir / 'debug_traceback.c',  # ifdef HAVE_RTYPER
        srcdir / 'asm.c',
        srcdir / 'instrument.c',
        srcdir / 'int.c',
        srcdir / 'stack.c',
        srcdir / 'threadlocal.c',
    ]
    if _CYGWIN:
        files.append(srcdir / 'cygwin_wait.c')
    return eci.merge(ExternalCompilationInfo(separate_module_files=files))


def gen_source(database, modulename, targetdir,
               eci, defines={}, split=False):
    if isinstance(targetdir, str):
        targetdir = py.path.local(targetdir)

    filename = targetdir.join(modulename + '.c')
    f = filename.open('w')
    incfilename = targetdir.join('common_header.h')
    fi = incfilename.open('w')
    fi.write('#ifndef _PY_COMMON_HEADER_H\n#define _PY_COMMON_HEADER_H\n')

    #
    # Header
    #
    print >> f, '#include "common_header.h"'
    print >> f
    commondefs(defines)
    for key, value in defines.items():
        print >> fi, '#define %s %s' % (key, value)

    eci.write_c_header(fi)
    print >> fi, '#include "src/g_prerequisite.h"'
    fi.write('#endif /* _PY_COMMON_HEADER_H*/\n')

    fi.close()

    #
    # 1) All declarations
    # 2) Implementation of functions and global structures and arrays
    #
    sg = SourceGenerator(database)
    sg.set_strategy(targetdir, split)
    database.prepare_inline_helpers()
    sg.gen_readable_parts_of_source(f)
    headers_to_precompile = sg.headers_to_precompile[:]
    headers_to_precompile.insert(0, incfilename)

    gen_startupcode(f, database)
    f.close()

    if 'PYPY_INSTRUMENT' in defines:
        fi = incfilename.open('a')
        n = database.instrument_ncounter
        print >>fi, "#define PYPY_INSTRUMENT_NCOUNTER %d" % n
        fi.close()

    eci = add_extra_files(eci)
    eci = eci.convert_sources_to_files()
    return eci, filename, sg.getextrafiles(), headers_to_precompile
