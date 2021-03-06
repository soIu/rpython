import py
from rpython.annotator import model as annmodel
from rpython.rtyper.llannotation import SomePtr
from rpython.rtyper.lltypesystem import lltype
from rpython.rtyper.lltypesystem import ll2ctypes
from rpython.rtyper.lltypesystem.llmemory import cast_ptr_to_adr
from rpython.rtyper.lltypesystem.llmemory import itemoffsetof
from rpython.rtyper.llannotation import lltype_to_annotation
from rpython.tool.sourcetools import func_with_new_name
from rpython.rlib.objectmodel import Symbolic
from rpython.rlib.objectmodel import keepalive_until_here, enforceargs
from rpython.rlib import rarithmetic, rgc
from rpython.rtyper.extregistry import ExtRegistryEntry
from rpython.rlib.unroll import unrolling_iterable
from rpython.rtyper.tool.rfficache import platform, sizeof_c_type
from rpython.translator.tool.cbuild import ExternalCompilationInfo
from rpython.rtyper.annlowlevel import llhelper
from rpython.rlib.objectmodel import we_are_translated
from rpython.rlib.rstring import StringBuilder, UnicodeBuilder, assert_str0
from rpython.rlib import jit
from rpython.rtyper.lltypesystem import llmemory
from rpython.rlib.rarithmetic import maxint, LONG_BIT
from rpython.translator.platform import CompilationError
import os, sys

class CConstant(Symbolic):
    """ A C-level constant, maybe #define, rendered directly.
    """
    def __init__(self, c_name, TP):
        self.c_name = c_name
        self.TP = TP

    def __repr__(self):
        return '%s(%r, %s)' % (self.__class__.__name__,
                               self.c_name, self.TP)

    def annotation(self):
        return lltype_to_annotation(self.TP)

    def lltype(self):
        return self.TP

def _isfunctype(TP):
    """ Evil hack to get rid of flow objspace inability
    to accept .TO when TP is not a pointer
    """
    return isinstance(TP, lltype.Ptr) and isinstance(TP.TO, lltype.FuncType)
_isfunctype._annspecialcase_ = 'specialize:memo'

def _isllptr(p):
    """ Second evil hack to detect if 'p' is a low-level pointer or not """
    return isinstance(p, lltype._ptr)
class _IsLLPtrEntry(ExtRegistryEntry):
    _about_ = _isllptr
    def compute_result_annotation(self, s_p):
        result = isinstance(s_p, SomePtr)
        return self.bookkeeper.immutablevalue(result)
    def specialize_call(self, hop):
        hop.exception_cannot_occur()
        return hop.inputconst(lltype.Bool, hop.s_result.const)

RFFI_SAVE_ERRNO          = 1     # save the real errno after the call
RFFI_READSAVED_ERRNO     = 2     # copy saved errno into real errno before call
RFFI_ZERO_ERRNO_BEFORE   = 4     # copy the value 0 into real errno before call
RFFI_FULL_ERRNO          = RFFI_SAVE_ERRNO | RFFI_READSAVED_ERRNO
RFFI_FULL_ERRNO_ZERO     = RFFI_SAVE_ERRNO | RFFI_ZERO_ERRNO_BEFORE
RFFI_SAVE_LASTERROR      = 8     # win32: save GetLastError() after the call
RFFI_READSAVED_LASTERROR = 16    # win32: call SetLastError() before the call
RFFI_SAVE_WSALASTERROR   = 32    # win32: save WSAGetLastError() after the call
RFFI_FULL_LASTERROR      = RFFI_SAVE_LASTERROR | RFFI_READSAVED_LASTERROR
RFFI_ERR_NONE            = 0
RFFI_ERR_ALL             = RFFI_FULL_ERRNO | RFFI_FULL_LASTERROR
RFFI_ALT_ERRNO           = 64    # read, save using alt tl destination
def llexternal(name, args, result, _callable=None,
               compilation_info=ExternalCompilationInfo(),
               sandboxsafe=False, releasegil='auto',
               _nowrapper=False, calling_conv='c',
               elidable_function=False, macro=None,
               random_effects_on_gcobjs='auto',
               save_err=RFFI_ERR_NONE):
    """Build an external function that will invoke the C function 'name'
    with the given 'args' types and 'result' type.

    You get by default a wrapper that casts between number types as needed
    to match the arguments.  You can also pass an RPython string when a
    CCHARP argument is expected, and the C function receives a 'const char*'
    pointing to a read-only null-terminated character of arrays, as usual
    for C.

    The C function can have callbacks, but they must be specified explicitly
    as constant RPython functions.  We don't support yet C functions that
    invoke callbacks passed otherwise (e.g. set by a previous C call).

    releasegil: whether it's ok to release the GIL around the call.
                Default is yes, unless sandboxsafe is set, in which case
                we consider that the function is really short-running and
                don't bother releasing the GIL.  An explicit True or False
                overrides this logic.
    """
    if _callable is not None:
        assert callable(_callable)
    ext_type = lltype.FuncType(args, result)
    if _callable is None:
        if macro is not None:
            if macro is True:
                macro = name
            _callable = generate_macro_wrapper(
                name, macro, ext_type, compilation_info)
        else:
            _callable = ll2ctypes.LL2CtypesCallable(ext_type, calling_conv)
    else:
        assert macro is None, "'macro' is useless if you specify '_callable'"
    if elidable_function:
        _callable._elidable_function_ = True
    kwds = {}

    has_callback = False
    for ARG in args:
        if _isfunctype(ARG):
            has_callback = True
    if has_callback:
        kwds['_callbacks'] = callbackholder = CallbackHolder()
    else:
        callbackholder = None

    if releasegil in (False, True):
        # invoke the around-handlers, which release the GIL, if and only if
        # the C function is thread-safe.
        invoke_around_handlers = releasegil
    else:
        # default case:
        # invoke the around-handlers only for "not too small" external calls;
        # sandboxsafe is a hint for "too-small-ness" (e.g. math functions).
        # Also, _nowrapper functions cannot release the GIL, by default.
        invoke_around_handlers = not sandboxsafe and not _nowrapper

    if random_effects_on_gcobjs not in (False, True):
        random_effects_on_gcobjs = (
            invoke_around_handlers or   # because it can release the GIL
            has_callback)               # because the callback can do it
    assert not (elidable_function and random_effects_on_gcobjs)

    funcptr = lltype.functionptr(ext_type, name, external='C',
                                 compilation_info=compilation_info,
                                 _callable=_callable,
                                 _safe_not_sandboxed=sandboxsafe,
                                 _debugexc=True, # on top of llinterp
                                 canraise=False,
                                 random_effects_on_gcobjs=
                                     random_effects_on_gcobjs,
                                 calling_conv=calling_conv,
                                 **kwds)
    if isinstance(_callable, ll2ctypes.LL2CtypesCallable):
        _callable.funcptr = funcptr

    if _nowrapper:
        assert save_err == RFFI_ERR_NONE
        return funcptr

    if invoke_around_handlers:
        # The around-handlers are releasing the GIL in a threaded pypy.
        # We need tons of care to ensure that no GC operation and no
        # exception checking occurs while the GIL is released.

        # The actual call is done by this small piece of non-inlinable
        # generated code in order to avoid seeing any GC pointer:
        # neither '*args' nor the GC objects originally passed in as
        # argument to wrapper(), if any (e.g. RPython strings).

        argnames = ', '.join(['a%d' % i for i in range(len(args))])
        source = py.code.Source("""
            def call_external_function(%(argnames)s):
                before = aroundstate.before
                if before: before()
                # NB. it is essential that no exception checking occurs here!
                if %(save_err)d:
                    from rpython.rlib import rposix
                    rposix._errno_before(%(save_err)d)
                res = funcptr(%(argnames)s)
                if %(save_err)d:
                    from rpython.rlib import rposix
                    rposix._errno_after(%(save_err)d)
                after = aroundstate.after
                if after: after()
                return res
        """ % locals())
        miniglobals = {'aroundstate': aroundstate,
                       'funcptr':     funcptr,
                       '__name__':    __name__, # for module name propagation
                       }
        exec source.compile() in miniglobals
        call_external_function = miniglobals['call_external_function']
        call_external_function._dont_inline_ = True
        call_external_function._annspecialcase_ = 'specialize:ll'
        call_external_function._gctransformer_hint_close_stack_ = True
        #
        # '_call_aroundstate_target_' is used by the JIT to generate a
        # CALL_RELEASE_GIL directly to 'funcptr'.  This doesn't work if
        # 'funcptr' might be a C macro, though.
        if macro is None:
            call_external_function._call_aroundstate_target_ = funcptr, save_err
        #
        call_external_function = func_with_new_name(call_external_function,
                                                    'ccall_' + name)
        # don't inline, as a hack to guarantee that no GC pointer is alive
        # anywhere in call_external_function
    else:
        # if we don't have to invoke the aroundstate, we can just call
        # the low-level function pointer carelessly
        if macro is None and save_err == RFFI_ERR_NONE:
            call_external_function = funcptr
        else:
            # ...well, unless it's a macro, in which case we still have
            # to hide it from the JIT...
            argnames = ', '.join(['a%d' % i for i in range(len(args))])
            source = py.code.Source("""
                def call_external_function(%(argnames)s):
                    if %(save_err)d:
                        from rpython.rlib import rposix
                        rposix._errno_before(%(save_err)d)
                    res = funcptr(%(argnames)s)
                    if %(save_err)d:
                        from rpython.rlib import rposix
                        rposix._errno_after(%(save_err)d)
                    return res
            """ % locals())
            miniglobals = {'funcptr':     funcptr,
                           '__name__':    __name__,
                           }
            exec source.compile() in miniglobals
            call_external_function = miniglobals['call_external_function']
            call_external_function = func_with_new_name(call_external_function,
                                                        'ccall_' + name)
            call_external_function = jit.dont_look_inside(
                call_external_function)

    unrolling_arg_tps = unrolling_iterable(enumerate(args))
    def wrapper(*args):
        real_args = ()
        to_free = ()
        for i, TARGET in unrolling_arg_tps:
            arg = args[i]
            freeme = None
            if TARGET == CCHARP:
                if arg is None:
                    arg = lltype.nullptr(CCHARP.TO)   # None => (char*)NULL
                    freeme = arg
                elif isinstance(arg, str):
                    arg = str2charp(arg)
                    # XXX leaks if a str2charp() fails with MemoryError
                    # and was not the first in this function
                    freeme = arg
            elif TARGET == CWCHARP:
                if arg is None:
                    arg = lltype.nullptr(CWCHARP.TO)   # None => (wchar_t*)NULL
                    freeme = arg
                elif isinstance(arg, unicode):
                    arg = unicode2wcharp(arg)
                    # XXX leaks if a unicode2wcharp() fails with MemoryError
                    # and was not the first in this function
                    freeme = arg
            elif TARGET is VOIDP:
                if arg is None:
                    arg = lltype.nullptr(VOIDP.TO)
                elif isinstance(arg, str):
                    arg = str2charp(arg)
                    freeme = arg
                elif isinstance(arg, unicode):
                    arg = unicode2wcharp(arg)
                    freeme = arg
            elif _isfunctype(TARGET) and not _isllptr(arg):
                # XXX pass additional arguments
                if invoke_around_handlers:
                    arg = llhelper(TARGET, _make_wrapper_for(TARGET, arg,
                                                             callbackholder,
                                                             aroundstate))
                else:
                    arg = llhelper(TARGET, _make_wrapper_for(TARGET, arg,
                                                             callbackholder))
            else:
                SOURCE = lltype.typeOf(arg)
                if SOURCE != TARGET:
                    if TARGET is lltype.Float:
                        arg = float(arg)
                    elif ((isinstance(SOURCE, lltype.Number)
                           or SOURCE is lltype.Bool)
                      and (isinstance(TARGET, lltype.Number)
                           or TARGET is lltype.Bool)):
                        arg = cast(TARGET, arg)
            real_args = real_args + (arg,)
            to_free = to_free + (freeme,)
        res = call_external_function(*real_args)
        for i, TARGET in unrolling_arg_tps:
            if to_free[i]:
                lltype.free(to_free[i], flavor='raw')
        if rarithmetic.r_int is not r_int:
            if result is INT:
                return cast(lltype.Signed, res)
            elif result is UINT:
                return cast(lltype.Unsigned, res)
        return res
    wrapper._annspecialcase_ = 'specialize:ll'
    wrapper._always_inline_ = 'try'
    # for debugging, stick ll func ptr to that
    wrapper._ptr = funcptr
    wrapper = func_with_new_name(wrapper, name)

    if calling_conv != "c":
        wrapper = jit.dont_look_inside(wrapper)

    return wrapper


class CallbackHolder:
    def __init__(self):
        self.callbacks = {}

def _make_wrapper_for(TP, callable, callbackholder=None, aroundstate=None):
    """ Function creating wrappers for callbacks. Note that this is
    cheating as we assume constant callbacks and we just memoize wrappers
    """
    from rpython.rtyper.lltypesystem import lltype
    from rpython.rtyper.lltypesystem.lloperation import llop
    if hasattr(callable, '_errorcode_'):
        errorcode = callable._errorcode_
    else:
        errorcode = TP.TO.RESULT._defl()
    callable_name = getattr(callable, '__name__', '?')
    if callbackholder is not None:
        callbackholder.callbacks[callable] = True
    args = ', '.join(['a%d' % i for i in range(len(TP.TO.ARGS))])
    source = py.code.Source(r"""
        def wrapper(%(args)s):    # no *args - no GIL for mallocing the tuple
            if aroundstate is not None:
                after = aroundstate.after
                if after:
                    after()
            # from now on we hold the GIL
            stackcounter.stacks_counter += 1
            llop.gc_stack_bottom(lltype.Void)   # marker for trackgcroot.py
            try:
                result = callable(%(args)s)
            except Exception, e:
                os.write(2,
                    "Warning: uncaught exception in callback: %%s %%s\n" %%
                    (callable_name, str(e)))
                if not we_are_translated():
                    import traceback
                    traceback.print_exc()
                result = errorcode
            stackcounter.stacks_counter -= 1
            if aroundstate is not None:
                before = aroundstate.before
                if before:
                    before()
            # here we don't hold the GIL any more. As in the wrapper() produced
            # by llexternal, it is essential that no exception checking occurs
            # after the call to before().
            return result
    """ % locals())
    miniglobals = locals().copy()
    miniglobals['Exception'] = Exception
    miniglobals['os'] = os
    miniglobals['we_are_translated'] = we_are_translated
    miniglobals['stackcounter'] = stackcounter
    exec source.compile() in miniglobals
    return miniglobals['wrapper']
_make_wrapper_for._annspecialcase_ = 'specialize:memo'

AroundFnPtr = lltype.Ptr(lltype.FuncType([], lltype.Void))

class AroundState:
    def _cleanup_(self):
        self.before = None        # or a regular RPython function
        self.after = None         # or a regular RPython function
aroundstate = AroundState()
aroundstate._cleanup_()

class StackCounter:
    def _cleanup_(self):
        self.stacks_counter = 0     # number of "stack pieces": callbacks
                                    # and threads increase it by one

stackcounter = StackCounter()
stackcounter._cleanup_()

def llexternal_use_eci(compilation_info):
    """Return a dummy function that, if called in a RPython program,
    adds the given ExternalCompilationInfo to it."""
    eci = ExternalCompilationInfo(post_include_bits=['#define PYPY_NO_OP()'])
    eci = eci.merge(compilation_info)
    return llexternal('PYPY_NO_OP', [], lltype.Void,
                      compilation_info=eci, sandboxsafe=True, _nowrapper=True,
                      _callable=lambda: None)

def generate_macro_wrapper(name, macro, functype, eci):
    """Wraps a function-like macro inside a real function, and expose
    it with llexternal."""

    # Generate the function call
    from rpython.translator.c.database import LowLevelDatabase
    from rpython.translator.c.support import cdecl
    wrapper_name = 'pypy_macro_wrapper_%s' % (name,)
    argnames = ['arg%d' % (i,) for i in range(len(functype.ARGS))]
    db = LowLevelDatabase()
    implementationtypename = db.gettype(functype, argnames=argnames)
    if functype.RESULT is lltype.Void:
        pattern = '%s%s { %s(%s); }'
    else:
        pattern = '%s%s { return %s(%s); }'
    source = pattern % (
        'RPY_EXTERN ',
        cdecl(implementationtypename, wrapper_name),
        macro, ', '.join(argnames))

    # Now stuff this source into a "companion" eci that will be used
    # by ll2ctypes.  We replace eci._with_ctypes, so that only one
    # shared library is actually compiled (when ll2ctypes calls the
    # first function)
    ctypes_eci = eci.merge(ExternalCompilationInfo(
            separate_module_sources=[source],
            ))
    if hasattr(eci, '_with_ctypes'):
        ctypes_eci = eci._with_ctypes.merge(ctypes_eci)
    eci._with_ctypes = ctypes_eci
    func = llexternal(wrapper_name, functype.ARGS, functype.RESULT,
                      compilation_info=eci, _nowrapper=True)
    # _nowrapper=True returns a pointer which is not hashable
    return lambda *args: func(*args)

# ____________________________________________________________
# Few helpers for keeping callback arguments alive
# this makes passing opaque objects possible (they don't even pass
# through C, only integer specifying number passes)

_KEEPER_CACHE = {}

def _keeper_for_type(TP):
    try:
        return _KEEPER_CACHE[TP]
    except KeyError:
        tp_str = str(TP) # make annotator happy
        class KeepaliveKeeper(object):
            def __init__(self):
                self.stuff_to_keepalive = []
                self.free_positions = []
        keeper = KeepaliveKeeper()
        _KEEPER_CACHE[TP] = keeper
        return keeper
_keeper_for_type._annspecialcase_ = 'specialize:memo'

def register_keepalive(obj):
    """ Register object obj to be kept alive,
    returns a position for that object
    """
    keeper = _keeper_for_type(lltype.typeOf(obj))
    if len(keeper.free_positions):
        pos = keeper.free_positions.pop()
        keeper.stuff_to_keepalive[pos] = obj
        return pos
    # we don't have any free positions
    pos = len(keeper.stuff_to_keepalive)
    keeper.stuff_to_keepalive.append(obj)
    return pos
register_keepalive._annspecialcase_ = 'specialize:argtype(0)'

def get_keepalive_object(pos, TP):
    keeper = _keeper_for_type(TP)
    return keeper.stuff_to_keepalive[pos]
get_keepalive_object._annspecialcase_ = 'specialize:arg(1)'

def unregister_keepalive(pos, TP):
    """ Unregister an object of type TP, stored at position
    pos (position previously returned by register_keepalive)
    """
    keeper = _keeper_for_type(TP)
    keeper.stuff_to_keepalive[pos] = None
    keeper.free_positions.append(pos)
unregister_keepalive._annspecialcase_ = 'specialize:arg(1)'

# ____________________________________________________________

TYPES = []
for _name in 'short int long'.split():
    for name in (_name, 'unsigned ' + _name):
        TYPES.append(name)
TYPES += ['signed char', 'unsigned char',
          'long long', 'unsigned long long',
          'size_t', 'time_t', 'wchar_t',
          'uintptr_t', 'intptr_t',
          'void*']    # generic pointer type

# This is a bit of a hack since we can't use rffi_platform here.
try:
    sizeof_c_type('__int128_t', ignore_errors=True)
    TYPES += ['__int128_t']
except CompilationError:
    pass

if os.name != 'nt':
    TYPES.append('mode_t')
    TYPES.append('pid_t')
    TYPES.append('ssize_t')
    # the types below are rare enough and not available on Windows
    TYPES.extend(['ptrdiff_t',
          'int_least8_t',  'uint_least8_t',
          'int_least16_t', 'uint_least16_t',
          'int_least32_t', 'uint_least32_t',
          'int_least64_t', 'uint_least64_t',
          'int_fast8_t',  'uint_fast8_t',
          'int_fast16_t', 'uint_fast16_t',
          'int_fast32_t', 'uint_fast32_t',
          'int_fast64_t', 'uint_fast64_t',
          'intmax_t', 'uintmax_t'])
else:
    MODE_T = lltype.Signed
    PID_T = lltype.Signed
    SSIZE_T = lltype.Signed

def populate_inttypes():
    names = []
    populatelist = []
    for name in TYPES:
        c_name = name
        if name.startswith('unsigned'):
            name = 'u' + name[9:]
            signed = False
        elif name == 'size_t' or name.startswith('uint'):
            signed = False
        else:
            signed = True
        name = name.replace(' ', '')
        names.append(name)
        populatelist.append((name.upper(), c_name, signed))
    platform.populate_inttypes(populatelist)
    return names

def setup():
    """ creates necessary c-level types
    """
    names = populate_inttypes()
    result = []
    for name in names:
        tp = platform.types[name.upper()]
        globals()['r_' + name] = platform.numbertype_to_rclass[tp]
        globals()[name.upper()] = tp
        tpp = lltype.Ptr(lltype.Array(tp, hints={'nolength': True}))
        globals()[name.upper()+'P'] = tpp
        result.append(tp)
    return result

NUMBER_TYPES = setup()
platform.numbertype_to_rclass[lltype.Signed] = int     # avoid "r_long" for common cases
r_int_real = rarithmetic.build_int("r_int_real", r_int.SIGN, r_int.BITS, True)
INT_real = lltype.build_number("INT", r_int_real)
platform.numbertype_to_rclass[INT_real] = r_int_real
NUMBER_TYPES.append(INT_real)

# ^^^ this creates at least the following names:
# --------------------------------------------------------------------
#        Type           RPython integer class doing wrap-around
# --------------------------------------------------------------------
#        SIGNEDCHAR     r_signedchar
#        UCHAR          r_uchar
#        SHORT          r_short
#        USHORT         r_ushort
#        INT            r_int
#        UINT           r_uint
#        LONG           r_long
#        ULONG          r_ulong
#        LONGLONG       r_longlong
#        ULONGLONG      r_ulonglong
#        WCHAR_T        r_wchar_t
#        SIZE_T         r_size_t
#        SSIZE_T        r_ssize_t
#        TIME_T         r_time_t
# --------------------------------------------------------------------
# Note that rffi.r_int is not necessarily the same as
# rarithmetic.r_int, etc!  rffi.INT/r_int correspond to the C-level
# 'int' type, whereas rarithmetic.r_int corresponds to the
# Python-level int type (which is a C long).  Fun.

def CStruct(name, *fields, **kwds):
    """ A small helper to create external C structure, not the
    pypy one
    """
    hints = kwds.get('hints', {})
    hints = hints.copy()
    kwds['hints'] = hints
    hints['external'] = 'C'
    hints['c_name'] = name
    # Hack: prefix all attribute names with 'c_' to cope with names starting
    # with '_'.  The genc backend removes the 'c_' prefixes...
    c_fields = [('c_' + key, value) for key, value in fields]
    return lltype.Struct(name, *c_fields, **kwds)

def CStructPtr(*args, **kwds):
    return lltype.Ptr(CStruct(*args, **kwds))

def CFixedArray(tp, size):
    return lltype.FixedSizeArray(tp, size)
CFixedArray._annspecialcase_ = 'specialize:memo'

def CArray(tp):
    return lltype.Array(tp, hints={'nolength': True})
CArray._annspecialcase_ = 'specialize:memo'

def CArrayPtr(tp):
    return lltype.Ptr(CArray(tp))
CArrayPtr._annspecialcase_ = 'specialize:memo'

def CCallback(args, res):
    return lltype.Ptr(lltype.FuncType(args, res))
CCallback._annspecialcase_ = 'specialize:memo'

def COpaque(name=None, ptr_typedef=None, hints=None, compilation_info=None):
    if compilation_info is None:
        compilation_info = ExternalCompilationInfo()
    if hints is None:
        hints = {}
    else:
        hints = hints.copy()
    hints['external'] = 'C'
    if name is not None:
        hints['c_name'] = name
    if ptr_typedef is not None:
        hints['c_pointer_typedef'] = ptr_typedef
    def lazy_getsize(cache={}):
        from rpython.rtyper.tool import rffi_platform
        try:
            return cache[name]
        except KeyError:
            val = rffi_platform.sizeof(name, compilation_info)
            cache[name] = val
            return val

    hints['getsize'] = lazy_getsize
    return lltype.OpaqueType(name, hints)

def COpaquePtr(*args, **kwds):
    typedef = kwds.pop('typedef', None)
    return lltype.Ptr(COpaque(ptr_typedef=typedef, *args, **kwds))

def CExternVariable(TYPE, name, eci, _CConstantClass=CConstant,
                    sandboxsafe=False, _nowrapper=False,
                    c_type=None, getter_only=False):
    """Return a pair of functions - a getter and a setter - to access
    the given global C variable.
    """
    from rpython.translator.c.primitive import PrimitiveType
    from rpython.translator.tool.cbuild import ExternalCompilationInfo
    # XXX we cannot really enumerate all C types here, do it on a case-by-case
    #     basis
    if c_type is None:
        if TYPE == CCHARPP:
            c_type = 'char **'
        elif TYPE == CCHARP:
            c_type = 'char *'
        elif TYPE == INT or TYPE == LONG:
            assert False, "ambiguous type on 32-bit machines: give a c_type"
        else:
            c_type = PrimitiveType[TYPE]
            assert c_type.endswith(' @')
            c_type = c_type[:-2] # cut the trailing ' @'

    getter_name = 'get_' + name
    setter_name = 'set_' + name
    getter_prototype = (
       "RPY_EXTERN %(c_type)s %(getter_name)s ();" % locals())
    setter_prototype = (
       "RPY_EXTERN void %(setter_name)s (%(c_type)s v);" % locals())
    c_getter = "%(c_type)s %(getter_name)s () { return %(name)s; }" % locals()
    c_setter = "void %(setter_name)s (%(c_type)s v) { %(name)s = v; }" % locals()

    lines = ["#include <%s>" % i for i in eci.includes]
    if sys.platform != 'win32':
        lines.append('extern %s %s;' % (c_type, name))
    lines.append(c_getter)
    if not getter_only:
        lines.append(c_setter)
    prototypes = [getter_prototype]
    if not getter_only:
        prototypes.append(setter_prototype)
    sources = ('\n'.join(lines),)
    new_eci = eci.merge(ExternalCompilationInfo(
        separate_module_sources = sources,
        post_include_bits = prototypes,
    ))

    getter = llexternal(getter_name, [], TYPE, compilation_info=new_eci,
                        sandboxsafe=sandboxsafe, _nowrapper=_nowrapper)
    if getter_only:
        return getter
    else:
        setter = llexternal(setter_name, [TYPE], lltype.Void,
                            compilation_info=new_eci, sandboxsafe=sandboxsafe,
                            _nowrapper=_nowrapper)
        return getter, setter

# char, represented as a Python character
# (use SIGNEDCHAR or UCHAR for the small integer types)
CHAR = lltype.Char

# double
DOUBLE = lltype.Float
LONGDOUBLE = lltype.LongFloat

# float - corresponds to rpython.rlib.rarithmetic.r_float, and supports no
#         operation except rffi.cast() between FLOAT and DOUBLE
FLOAT = lltype.SingleFloat
r_singlefloat = rarithmetic.r_singlefloat

# void *   - for now, represented as char *
VOIDP = lltype.Ptr(lltype.Array(lltype.Char, hints={'nolength': True, 'render_as_void': True}))
NULL = None

# void **
VOIDPP = CArrayPtr(VOIDP)

# char *
CCHARP = lltype.Ptr(lltype.Array(lltype.Char, hints={'nolength': True}))

# const char *
CONST_CCHARP = lltype.Ptr(lltype.Array(lltype.Char, hints={'nolength': True,
                                       'render_as_const': True}))

# wchar_t *
CWCHARP = lltype.Ptr(lltype.Array(lltype.UniChar, hints={'nolength': True}))

# int *, unsigned int *, etc.
#INTP = ...    see setup() above

# double *
DOUBLEP = lltype.Ptr(lltype.Array(DOUBLE, hints={'nolength': True}))

# float *
FLOATP = lltype.Ptr(lltype.Array(FLOAT, hints={'nolength': True}))

# long double *
LONGDOUBLEP = lltype.Ptr(lltype.Array(LONGDOUBLE, hints={'nolength': True}))

# Signed, Signed *
SIGNED = lltype.Signed
SIGNEDP = lltype.Ptr(lltype.Array(SIGNED, hints={'nolength': True}))

# various type mapping

# conversions between str and char*
# conversions between unicode and wchar_t*
def make_string_mappings(strtype):

    if strtype is str:
        from rpython.rtyper.lltypesystem.rstr import (STR as STRTYPE,
                                                      copy_string_to_raw,
                                                      copy_raw_to_string,
                                                      copy_string_contents,
                                                      mallocstr as mallocfn)
        from rpython.rtyper.annlowlevel import llstr as llstrtype
        from rpython.rtyper.annlowlevel import hlstr as hlstrtype
        TYPEP = CCHARP
        ll_char_type = lltype.Char
        lastchar = '\x00'
    else:
        from rpython.rtyper.lltypesystem.rstr import (
            UNICODE as STRTYPE,
            copy_unicode_to_raw as copy_string_to_raw,
            copy_raw_to_unicode as copy_raw_to_string,
            copy_unicode_contents as copy_string_contents,
            mallocunicode as mallocfn)
        from rpython.rtyper.annlowlevel import llunicode as llstrtype
        from rpython.rtyper.annlowlevel import hlunicode as hlstrtype
        TYPEP = CWCHARP
        ll_char_type = lltype.UniChar
        lastchar = u'\x00'

    # str -> char*
    def str2charp(s, track_allocation=True):
        """ str -> char*
        """
        if track_allocation:
            array = lltype.malloc(TYPEP.TO, len(s) + 1, flavor='raw', track_allocation=True)
        else:
            array = lltype.malloc(TYPEP.TO, len(s) + 1, flavor='raw', track_allocation=False)
        i = len(s)
        ll_s = llstrtype(s)
        copy_string_to_raw(ll_s, array, 0, i)
        array[i] = lastchar
        return array
    str2charp._annenforceargs_ = [strtype, bool]

    def free_charp(cp, track_allocation=True):
        if track_allocation:
            lltype.free(cp, flavor='raw', track_allocation=True)
        else:
            lltype.free(cp, flavor='raw', track_allocation=False)

    # str -> already-existing char[maxsize]
    def str2chararray(s, array, maxsize):
        length = min(len(s), maxsize)
        ll_s = llstrtype(s)
        copy_string_to_raw(ll_s, array, 0, length)
        return length
    str2chararray._annenforceargs_ = [strtype, None, int]

    # char* -> str
    # doesn't free char*
    def charp2str(cp):
        size = 0
        while cp[size] != lastchar:
            size += 1
        return assert_str0(charpsize2str(cp, size))

    # str -> char*, bool, bool
    # Can't inline this because of the raw address manipulation.
    @jit.dont_look_inside
    def get_nonmovingbuffer(data):
        """
        Either returns a non-moving copy or performs neccessary pointer
        arithmetic to return a pointer to the characters of a string if the
        string is already nonmovable or could be pinned.  Must be followed by a
        free_nonmovingbuffer call.

        First bool returned indicates if 'data' was pinned. Second bool returned
        indicates if we did a raw alloc because pinning failed. Both bools
        should never be true at the same time.
        """

        lldata = llstrtype(data)
        count = len(data)

        pinned = False
        if rgc.can_move(data):
            if rgc.pin(data):
                pinned = True
            else:
                buf = lltype.malloc(TYPEP.TO, count, flavor='raw')
                copy_string_to_raw(lldata, buf, 0, count)
                return buf, pinned, True
                # ^^^ raw malloc used to get a nonmovable copy
        #
        # following code is executed if:
        # - rgc.can_move(data) and rgc.pin(data) both returned true
        # - rgc.can_move(data) returned false
        data_start = cast_ptr_to_adr(lldata) + \
            offsetof(STRTYPE, 'chars') + itemoffsetof(STRTYPE.chars, 0)

        return cast(TYPEP, data_start), pinned, False
        # ^^^ already nonmovable. Therefore it's not raw allocated nor
        # pinned.
    get_nonmovingbuffer._always_inline_ = 'try' # get rid of the returned tuple
    get_nonmovingbuffer._annenforceargs_ = [strtype]

    # (str, char*, bool, bool) -> None
    # Can't inline this because of the raw address manipulation.
    @jit.dont_look_inside
    def free_nonmovingbuffer(data, buf, is_pinned, is_raw):
        """
        Keep 'data' alive and unpin it if it was pinned ('is_pinned' is true).
        Otherwise free the non-moving copy ('is_raw' is true).
        """
        if is_pinned:
            rgc.unpin(data)
        if is_raw:
            lltype.free(buf, flavor='raw')
        # if is_pinned and is_raw are false: data was already nonmovable,
        # we have nothing to clean up
        keepalive_until_here(data)
    free_nonmovingbuffer._annenforceargs_ = [strtype, None, bool, bool]

    # int -> (char*, str, int)
    # Can't inline this because of the raw address manipulation.
    @jit.dont_look_inside
    def alloc_buffer(count):
        """
        Returns a (raw_buffer, gc_buffer, case_num) triple,
        allocated with count bytes.
        The raw_buffer can be safely passed to a native function which expects
        it to not move. Call str_from_buffer with the returned values to get a
        safe high-level string. When the garbage collector cooperates, this
        allows for the process to be performed without an extra copy.
        Make sure to call keep_buffer_alive_until_here on the returned values.
        """
        new_buf = mallocfn(count)
        pinned = 0
        if rgc.can_move(new_buf):
            if rgc.pin(new_buf):
                pinned = 1
            else:
                raw_buf = lltype.malloc(TYPEP.TO, count, flavor='raw')
                return raw_buf, new_buf, 2
        #
        # following code is executed if:
        # - rgc.can_move(data) and rgc.pin(data) both returned true
        # - rgc.can_move(data) returned false
        data_start = cast_ptr_to_adr(new_buf) + \
            offsetof(STRTYPE, 'chars') + itemoffsetof(STRTYPE.chars, 0)
        return cast(TYPEP, data_start), new_buf, pinned
    alloc_buffer._always_inline_ = 'try' # to get rid of the returned tuple
    alloc_buffer._annenforceargs_ = [int]

    # (char*, str, int, int) -> None
    @jit.dont_look_inside
    @enforceargs(None, None, int, int, int)
    def str_from_buffer(raw_buf, gc_buf, case_num, allocated_size, needed_size):
        """
        Converts from a pair returned by alloc_buffer to a high-level string.
        The returned string will be truncated to needed_size.
        """
        assert allocated_size >= needed_size
        if allocated_size != needed_size:
            from rpython.rtyper.lltypesystem.lloperation import llop
            if llop.shrink_array(lltype.Bool, gc_buf, needed_size):
                pass     # now 'gc_buf' is smaller
            else:
                gc_buf = mallocfn(needed_size)
                case_num = 2
        if case_num == 2:
            copy_raw_to_string(raw_buf, gc_buf, 0, needed_size)
        return hlstrtype(gc_buf)

    # (char*, str, int) -> None
    @jit.dont_look_inside
    def keep_buffer_alive_until_here(raw_buf, gc_buf, case_num):
        """
        Keeps buffers alive or frees temporary buffers created by alloc_buffer.
        This must be called after a call to alloc_buffer, usually in a
        try/finally block.
        """
        keepalive_until_here(gc_buf)
        if case_num == 1:
            rgc.unpin(gc_buf)
        if case_num == 2:
            lltype.free(raw_buf, flavor='raw')

    # char* -> str, with an upper bound on the length in case there is no \x00
    @enforceargs(None, int)
    def charp2strn(cp, maxlen):
        size = 0
        while size < maxlen and cp[size] != lastchar:
            size += 1
        return assert_str0(charpsize2str(cp, size))

    # char* and size -> str (which can contain null bytes)
    def charpsize2str(cp, size):
        ll_str = mallocfn(size)
        copy_raw_to_string(cp, ll_str, 0, size)
        result = hlstrtype(ll_str)
        assert result is not None
        return result
    charpsize2str._annenforceargs_ = [None, int]

    return (str2charp, free_charp, charp2str,
            get_nonmovingbuffer, free_nonmovingbuffer,
            alloc_buffer, str_from_buffer, keep_buffer_alive_until_here,
            charp2strn, charpsize2str, str2chararray,
            )

(str2charp, free_charp, charp2str,
 get_nonmovingbuffer, free_nonmovingbuffer,
 alloc_buffer, str_from_buffer, keep_buffer_alive_until_here,
 charp2strn, charpsize2str, str2chararray,
 ) = make_string_mappings(str)

(unicode2wcharp, free_wcharp, wcharp2unicode,
 get_nonmoving_unicodebuffer, free_nonmoving_unicodebuffer,
 alloc_unicodebuffer, unicode_from_buffer, keep_unicodebuffer_alive_until_here,
 wcharp2unicoden, wcharpsize2unicode, unicode2wchararray,
 ) = make_string_mappings(unicode)

# char**
CCHARPP = lltype.Ptr(lltype.Array(CCHARP, hints={'nolength': True}))

def liststr2charpp(l):
    """ list[str] -> char**, NULL terminated
    """
    array = lltype.malloc(CCHARPP.TO, len(l) + 1, flavor='raw')
    for i in range(len(l)):
        array[i] = str2charp(l[i])
    array[len(l)] = lltype.nullptr(CCHARP.TO)
    return array
liststr2charpp._annenforceargs_ = [[annmodel.s_Str0]]  # List of strings
# Make a copy for the ll_os.py module
ll_liststr2charpp = func_with_new_name(liststr2charpp, 'll_liststr2charpp')

def free_charpp(ref):
    """ frees list of char**, NULL terminated
    """
    i = 0
    while ref[i]:
        free_charp(ref[i])
        i += 1
    lltype.free(ref, flavor='raw')

def charpp2liststr(p):
    """ char** NULL terminated -> list[str].  No freeing is done.
    """
    result = []
    i = 0
    while p[i]:
        result.append(charp2str(p[i]))
        i += 1
    return result

cast = ll2ctypes.force_cast      # a forced, no-checking cast

ptradd = ll2ctypes.force_ptradd  # equivalent of "ptr + n" in C.
                                 # the ptr must point to an array.

def size_and_sign(tp):
    size = sizeof(tp)
    try:
        unsigned = not tp._type.SIGNED
    except AttributeError:
        if not isinstance(tp, lltype.Primitive):
            unsigned = False
        elif tp in (lltype.Signed, FLOAT, DOUBLE, llmemory.Address):
            unsigned = False
        elif tp in (lltype.Char, lltype.UniChar, lltype.Bool):
            unsigned = True
        else:
            raise AssertionError("size_and_sign(%r)" % (tp,))
    return size, unsigned

def sizeof(tp):
    """Similar to llmemory.sizeof() but tries hard to return a integer
    instead of a symbolic value.
    """
    if isinstance(tp, lltype.Typedef):
        tp = tp.OF
    if isinstance(tp, lltype.FixedSizeArray):
        return sizeof(tp.OF) * tp.length
    if isinstance(tp, lltype.Struct):
        # the hint is present in structures probed by rffi_platform.
        size = tp._hints.get('size')
        if size is None:
            size = llmemory.sizeof(tp)    # a symbolic result in this case
        return size
    if isinstance(tp, lltype.Ptr) or tp is llmemory.Address:
        return globals()['r_void*'].BITS/8
    if tp is lltype.Char or tp is lltype.Bool:
        return 1
    if tp is lltype.UniChar:
        return r_wchar_t.BITS/8
    if tp is lltype.Float:
        return 8
    if tp is lltype.SingleFloat:
        return 4
    if tp is lltype.LongFloat:
        # :-/
        return sizeof_c_type("long double")
    assert isinstance(tp, lltype.Number)
    if tp is lltype.Signed:
        return LONG_BIT/8
    return tp._type.BITS/8
sizeof._annspecialcase_ = 'specialize:memo'

def offsetof(STRUCT, fieldname):
    """Similar to llmemory.offsetof() but tries hard to return a integer
    instead of a symbolic value.
    """
    # the hint is present in structures probed by rffi_platform.
    fieldoffsets = STRUCT._hints.get('fieldoffsets')
    if fieldoffsets is not None:
        # a numeric result when known
        for index, name in enumerate(STRUCT._names):
            if name == fieldname:
                return fieldoffsets[index]
    # a symbolic result as a fallback
    return llmemory.offsetof(STRUCT, fieldname)
offsetof._annspecialcase_ = 'specialize:memo'

# check that we have a sane configuration
"""assert maxint == (1 << (8 * sizeof(llmemory.Address) - 1)) - 1, (
    "Mixed configuration of the word size of the machine:\n\t"
    "the underlying Python was compiled with maxint=%d,\n\t"
    "but the C compiler says that 'void *' is %d bytes" % (
    maxint, sizeof(llmemory.Address)))"""
"""assert sizeof(lltype.Signed) == sizeof(llmemory.Address), (
    "Bad configuration: we should manage to get lltype.Signed "
    "be an integer type of the same size as llmemory.Address, "
    "but we got %s != %s" % (sizeof(lltype.Signed),
                             sizeof(llmemory.Address)))"""

# ********************** some helpers *******************

def make(STRUCT, **fields):
    """ Malloc a structure and populate it's fields
    """
    ptr = lltype.malloc(STRUCT, flavor='raw')
    for name, value in fields.items():
        setattr(ptr, name, value)
    return ptr

class MakeEntry(ExtRegistryEntry):
    _about_ = make

    def compute_result_annotation(self, s_type, **s_fields):
        TP = s_type.const
        if not isinstance(TP, lltype.Struct):
            raise TypeError("make called with %s instead of Struct as first argument" % TP)
        return SomePtr(lltype.Ptr(TP))

    def specialize_call(self, hop, **fields):
        assert hop.args_s[0].is_constant()
        vlist = [hop.inputarg(lltype.Void, arg=0)]
        flags = {'flavor':'raw'}
        vlist.append(hop.inputconst(lltype.Void, flags))
        hop.has_implicit_exception(MemoryError)   # record that we know about it
        hop.exception_is_here()
        v_ptr = hop.genop('malloc', vlist, resulttype=hop.r_result.lowleveltype)
        for name, i in fields.items():
            name = name[2:]
            v_arg = hop.inputarg(hop.args_r[i], arg=i)
            v_name = hop.inputconst(lltype.Void, name)
            hop.genop('setfield', [v_ptr, v_name, v_arg])
        return v_ptr


def structcopy(pdst, psrc):
    """Copy all the fields of the structure given by 'psrc'
    into the structure given by 'pdst'.
    """
    copy_fn = _get_structcopy_fn(lltype.typeOf(pdst), lltype.typeOf(psrc))
    copy_fn(pdst, psrc)
structcopy._annspecialcase_ = 'specialize:ll'

def _get_structcopy_fn(PDST, PSRC):
    assert PDST == PSRC
    if isinstance(PDST.TO, lltype.Struct):
        STRUCT = PDST.TO
        padding = STRUCT._hints.get('padding', ())
        fields = [(name, STRUCT._flds[name]) for name in STRUCT._names
                                             if name not in padding]
        unrollfields = unrolling_iterable(fields)

        def copyfn(pdst, psrc):
            for name, TYPE in unrollfields:
                if isinstance(TYPE, lltype.ContainerType):
                    structcopy(getattr(pdst, name), getattr(psrc, name))
                else:
                    setattr(pdst, name, getattr(psrc, name))

        return copyfn
    else:
        raise NotImplementedError('structcopy: type %r' % (PDST.TO,))
_get_structcopy_fn._annspecialcase_ = 'specialize:memo'


def setintfield(pdst, fieldname, value):
    """Maybe temporary: a helper to set an integer field into a structure,
    transparently casting between the various integer types.
    """
    STRUCT = lltype.typeOf(pdst).TO
    TSRC = lltype.typeOf(value)
    TDST = getattr(STRUCT, fieldname)
    assert isinstance(TSRC, lltype.Number)
    assert isinstance(TDST, lltype.Number)
    setattr(pdst, fieldname, cast(TDST, value))
setintfield._annspecialcase_ = 'specialize:ll_and_arg(1)'

def getintfield(pdst, fieldname):
    """As temporary as previous: get integer from a field in structure,
    casting it to lltype.Signed
    """
    return cast(lltype.Signed, getattr(pdst, fieldname))
getintfield._annspecialcase_ = 'specialize:ll_and_arg(1)'

class scoped_str2charp:
    def __init__(self, value):
        if value is not None:
            self.buf = str2charp(value)
        else:
            self.buf = lltype.nullptr(CCHARP.TO)
    __init__._annenforceargs_ = [None, annmodel.SomeString(can_be_None=True)]
    def __enter__(self):
        return self.buf
    def __exit__(self, *args):
        if self.buf:
            free_charp(self.buf)


class scoped_unicode2wcharp:
    def __init__(self, value):
        if value is not None:
            self.buf = unicode2wcharp(value)
        else:
            self.buf = lltype.nullptr(CWCHARP.TO)
    __init__._annenforceargs_ = [None,
                                 annmodel.SomeUnicodeString(can_be_None=True)]
    def __enter__(self):
        return self.buf
    def __exit__(self, *args):
        if self.buf:
            free_wcharp(self.buf)


class scoped_nonmovingbuffer:
    def __init__(self, data):
        self.data = data
    def __enter__(self):
        self.buf, self.pinned, self.is_raw = get_nonmovingbuffer(self.data)
        return self.buf
    def __exit__(self, *args):
        free_nonmovingbuffer(self.data, self.buf, self.pinned, self.is_raw)
    __init__._always_inline_ = 'try'
    __enter__._always_inline_ = 'try'
    __exit__._always_inline_ = 'try'

class scoped_nonmoving_unicodebuffer:
    def __init__(self, data):
        self.data = data
    def __enter__(self):
        self.buf, self.pinned, self.is_raw = get_nonmoving_unicodebuffer(self.data)
        return self.buf
    def __exit__(self, *args):
        free_nonmoving_unicodebuffer(self.data, self.buf, self.pinned, self.is_raw)
    __init__._always_inline_ = 'try'
    __enter__._always_inline_ = 'try'
    __exit__._always_inline_ = 'try'

class scoped_alloc_buffer:
    def __init__(self, size):
        self.size = size
    def __enter__(self):
        self.raw, self.gc_buf, self.case_num = alloc_buffer(self.size)
        return self
    def __exit__(self, *args):
        keep_buffer_alive_until_here(self.raw, self.gc_buf, self.case_num)
    def str(self, length):
        return str_from_buffer(self.raw, self.gc_buf, self.case_num,
                               self.size, length)

class scoped_alloc_unicodebuffer:
    def __init__(self, size):
        self.size = size
    def __enter__(self):
        self.raw, self.gc_buf, self.case_num = alloc_unicodebuffer(self.size)
        return self
    def __exit__(self, *args):
        keep_unicodebuffer_alive_until_here(self.raw, self.gc_buf, self.case_num)
    def str(self, length):
        return unicode_from_buffer(self.raw, self.gc_buf, self.case_num,
                                   self.size, length)

# You would have to have a *huge* amount of data for this to block long enough
# to be worth it to release the GIL.
c_memcpy = llexternal("memcpy",
            [VOIDP, VOIDP, SIZE_T],
            lltype.Void,
            releasegil=False
        )
