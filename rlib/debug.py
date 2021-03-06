import sys, time
from rpython.rtyper.extregistry import ExtRegistryEntry
from rpython.rlib.objectmodel import we_are_translated
from rpython.rlib.rarithmetic import is_valid_int


def ll_assert(x, msg):
    """After translation to C, this becomes an RPyAssert."""
    assert type(x) is bool, "bad type! got %r" % (type(x),)
    assert x, msg

class Entry(ExtRegistryEntry):
    _about_ = ll_assert

    def compute_result_annotation(self, s_x, s_msg):
        assert s_msg.is_constant(), ("ll_assert(x, msg): "
                                     "the msg must be constant")
        return None

    def specialize_call(self, hop):
        from rpython.rtyper.lltypesystem import lltype
        vlist = hop.inputargs(lltype.Bool, lltype.Void)
        hop.exception_cannot_occur()
        hop.genop('debug_assert', vlist)

class FatalError(Exception):
    pass

def fatalerror(msg):
    # print the RPython traceback and abort with a fatal error
    if not we_are_translated():
        raise FatalError(msg)
    from rpython.rtyper.lltypesystem import lltype
    from rpython.rtyper.lltypesystem.lloperation import llop
    llop.debug_print_traceback(lltype.Void)
    llop.debug_fatalerror(lltype.Void, msg)
fatalerror._dont_inline_ = True
fatalerror._jit_look_inside_ = False
fatalerror._annenforceargs_ = [str]

def fatalerror_notb(msg):
    # a variant of fatalerror() that doesn't print the RPython traceback
    if not we_are_translated():
        raise FatalError(msg)
    from rpython.rtyper.lltypesystem import lltype
    from rpython.rtyper.lltypesystem.lloperation import llop
    llop.debug_fatalerror(lltype.Void, msg)
fatalerror_notb._dont_inline_ = True
fatalerror_notb._jit_look_inside_ = False
fatalerror_notb._annenforceargs_ = [str]


class DebugLog(list):
    def debug_print(self, *args):
        self.append(('debug_print',) + args)
    def debug_start(self, category, time=None):
        self.append(('debug_start', category, time))
    def debug_stop(self, category, time=None):
        for i in xrange(len(self)-1, -1, -1):
            if self[i][0] == 'debug_start':
                assert self[i][1] == category, (
                    "nesting error: starts with %r but stops with %r" %
                    (self[i][1], category))
                starttime = self[i][2]
                if starttime is not None or time is not None:
                    self[i:] = [(category, starttime, time, self[i+1:])]
                else:
                    self[i:] = [(category, self[i+1:])]
                return
        assert False, ("nesting error: no start corresponding to stop %r" %
                       (category,))
    def __repr__(self):
        import pprint
        return pprint.pformat(list(self))

_log = None       # patched from tests to be an object of class DebugLog
                  # or compatible

def debug_print(*args):
    for arg in args:
        print >> sys.stderr, arg,
    print >> sys.stderr
    if _log is not None:
        _log.debug_print(*args)

class Entry(ExtRegistryEntry):
    _about_ = debug_print

    def compute_result_annotation(self, *args_s):
        return None

    def specialize_call(self, hop):
        vlist = hop.inputargs(*hop.args_r)
        hop.exception_cannot_occur()
        t = hop.rtyper.annotator.translator
        if t.config.translation.log:
            hop.genop('debug_print', vlist)


if sys.stderr.isatty():
    _start_colors_1 = "\033[1m\033[31m"
    _start_colors_2 = "\033[31m"
    _stop_colors = "\033[0m"
else:
    _start_colors_1 = ""
    _start_colors_2 = ""
    _stop_colors = ""

def debug_start(category):
    c = int(time.clock() * 100)
    print >> sys.stderr, '%s[%x] {%s%s' % (_start_colors_1, c,
                                           category, _stop_colors)
    if _log is not None:
        _log.debug_start(category)

def debug_stop(category):
    c = int(time.clock() * 100)
    print >> sys.stderr, '%s[%x] %s}%s' % (_start_colors_2, c,
                                           category, _stop_colors)
    if _log is not None:
        _log.debug_stop(category)

class Entry(ExtRegistryEntry):
    _about_ = debug_start, debug_stop

    def compute_result_annotation(self, s_category):
        return None

    def specialize_call(self, hop):
        from rpython.rtyper.lltypesystem.rstr import string_repr
        fn = self.instance
        vlist = hop.inputargs(string_repr)
        hop.exception_cannot_occur()
        t = hop.rtyper.annotator.translator
        if t.config.translation.log:
            hop.genop(fn.__name__, vlist)


def have_debug_prints():
    # returns True if the next calls to debug_print show up,
    # and False if they would not have any effect.
    return True

def have_debug_prints_for(category_prefix):
    # returns True if debug prints are enabled for at least some
    # category strings starting with "prefix" (must be a constant).
    assert len(category_prefix) > 0
    return True

class Entry(ExtRegistryEntry):
    _about_ = have_debug_prints, have_debug_prints_for

    def compute_result_annotation(self, s_prefix=None):
        from rpython.annotator import model as annmodel
        t = self.bookkeeper.annotator.translator
        if t.config.translation.log:
            return annmodel.s_Bool
        else:
            return self.bookkeeper.immutablevalue(False)

    def specialize_call(self, hop):
        from rpython.rtyper.lltypesystem import lltype
        t = hop.rtyper.annotator.translator
        hop.exception_cannot_occur()
        if t.config.translation.log:
            if hop.args_v:
                [c_prefix] = hop.args_v
                assert len(c_prefix.value) > 0
                args = [hop.inputconst(lltype.Void, c_prefix.value)]
                return hop.genop('have_debug_prints_for', args,
                                 resulttype=lltype.Bool)
            return hop.genop('have_debug_prints', [], resulttype=lltype.Bool)
        else:
            return hop.inputconst(lltype.Bool, False)


def debug_offset():
    """ Return an offset in log file
    """
    return -1

class Entry(ExtRegistryEntry):
    _about_ = debug_offset

    def compute_result_annotation(self):
        from rpython.annotator import model as annmodel
        return annmodel.SomeInteger()

    def specialize_call(self, hop):
        from rpython.rtyper.lltypesystem import lltype
        hop.exception_cannot_occur()
        return hop.genop('debug_offset', [], resulttype=lltype.Signed)


def debug_flush():
    """ Flushes the debug file
    """
    pass

class Entry(ExtRegistryEntry):
    _about_ = debug_flush

    def compute_result_annotation(self):
        return None

    def specialize_call(self, hop):
        hop.exception_cannot_occur()
        return hop.genop('debug_flush', [])


def debug_forked(original_offset):
    """ Call after a fork(), passing as argument the result of
        debug_offset() called before the fork.
    """
    pass

class Entry(ExtRegistryEntry):
    _about_ = debug_forked

    def compute_result_annotation(self, s_original_offset):
        return None

    def specialize_call(self, hop):
        from rpython.rtyper.lltypesystem import lltype
        vlist = hop.inputargs(lltype.Signed)
        hop.exception_cannot_occur()
        return hop.genop('debug_forked', vlist)


def llinterpcall(RESTYPE, pythonfunction, *args):
    """When running on the llinterp, this causes the llinterp to call to
    the provided Python function with the run-time value of the given args.
    The Python function should return a low-level object of type RESTYPE.
    This should never be called after translation: use this only if
    running_on_llinterp is true.
    """
    raise NotImplementedError

class Entry(ExtRegistryEntry):
    _about_ = llinterpcall

    def compute_result_annotation(self, s_RESTYPE, s_pythonfunction, *args_s):
        from rpython.annotator import model as annmodel
        from rpython.rtyper.llannotation import lltype_to_annotation
        from rpython.rtyper.lltypesystem import lltype
        assert s_RESTYPE.is_constant()
        assert s_pythonfunction.is_constant()
        s_result = s_RESTYPE.const
        if isinstance(s_result, lltype.LowLevelType):
            s_result = lltype_to_annotation(s_result)
        assert isinstance(s_result, annmodel.SomeObject)
        return s_result

    def specialize_call(self, hop):
        from rpython.annotator import model as annmodel
        from rpython.rtyper.lltypesystem import lltype
        RESTYPE = hop.args_s[0].const
        if not isinstance(RESTYPE, lltype.LowLevelType):
            assert isinstance(RESTYPE, annmodel.SomeObject)
            r_result = hop.rtyper.getrepr(RESTYPE)
            RESTYPE = r_result.lowleveltype
        pythonfunction = hop.args_s[1].const
        c_pythonfunction = hop.inputconst(lltype.Void, pythonfunction)
        args_v = [hop.inputarg(hop.args_r[i], arg=i)
                  for i in range(2, hop.nb_args)]
        hop.exception_is_here()
        return hop.genop('debug_llinterpcall', [c_pythonfunction] + args_v,
                         resulttype=RESTYPE)


def check_annotation(arg, checker):
    """ Function checking if annotation is as expected when translating,
    does nothing when just run. Checker is supposed to be a constant
    callable which checks if annotation is as expected,
    arguments passed are (current annotation, bookkeeper)
    """
    return arg

class Entry(ExtRegistryEntry):
    _about_ = check_annotation

    def compute_result_annotation(self, s_arg, s_checker):
        if not s_checker.is_constant():
            raise ValueError("Second argument of check_annotation must be constant")
        checker = s_checker.const
        checker(s_arg, self.bookkeeper)
        return s_arg

    def specialize_call(self, hop):
        hop.exception_cannot_occur()
        return hop.inputarg(hop.args_r[0], arg=0)

def make_sure_not_resized(arg):
    """ Function checking whether annotation of SomeList is never resized,
    useful for debugging. Does nothing when run directly
    """
    return arg

class Entry(ExtRegistryEntry):
    _about_ = make_sure_not_resized

    def compute_result_annotation(self, s_arg):
        from rpython.annotator.model import SomeList, s_None
        if s_None.contains(s_arg):
            return s_arg    # only None: just return
        assert isinstance(s_arg, SomeList)
        # the logic behind it is that we try not to propagate
        # make_sure_not_resized, when list comprehension is not on
        if self.bookkeeper.annotator.translator.config.translation.list_comprehension_operations:
            s_arg.listdef.never_resize()
        else:
            from rpython.annotator.annrpython import log
            log.WARNING('make_sure_not_resized called, but has no effect since list_comprehension is off')
        return s_arg

    def specialize_call(self, hop):
        hop.exception_cannot_occur()
        return hop.inputarg(hop.args_r[0], arg=0)


def mark_dict_non_null(d):
    """ Mark dictionary as having non-null keys and values. A warning would
    be emitted (not an error!) in case annotation disagrees.
    """
    assert isinstance(d, dict)
    return d


class DictMarkEntry(ExtRegistryEntry):
    _about_ = mark_dict_non_null

    def compute_result_annotation(self, s_dict):
        from rpython.annotator.model import SomeDict

        assert isinstance(s_dict, SomeDict)
        s_dict.dictdef.force_non_null = True
        return s_dict

    def specialize_call(self, hop):
        hop.exception_cannot_occur()
        return hop.inputarg(hop.args_r[0], arg=0)

class IntegerCanBeNegative(Exception):
    pass

class UnexpectedRUInt(Exception):
    pass

class ExpectedRegularInt(Exception):
    pass

def check_nonneg(x):
    """Give a translation-time error if 'x' is not known to be non-negative.
    To help debugging, this also gives a translation-time error if 'x' is
    actually typed as an r_uint (in which case the call to check_nonneg()
    is a bit strange and probably unexpected).
    """
    assert type(x)(-1) < 0     # otherwise, 'x' is a r_uint or similar
    assert x >= 0
    return x

class Entry(ExtRegistryEntry):
    _about_ = check_nonneg

    def compute_result_annotation(self, s_arg):
        from rpython.annotator.model import SomeInteger
        if isinstance(s_arg, SomeInteger) and s_arg.unsigned:
            raise UnexpectedRUInt("check_nonneg() arg is a %s" % (
                s_arg.knowntype,))
        s_nonneg = SomeInteger(nonneg=True)
        if not s_nonneg.contains(s_arg):
            raise IntegerCanBeNegative
        return s_arg

    def specialize_call(self, hop):
        hop.exception_cannot_occur()
        return hop.inputarg(hop.args_r[0], arg=0)

def check_regular_int(x):
    """Give a translation-time error if 'x' is not a plain int
    (e.g. if it's a r_longlong or an r_uint).
    """
    assert is_valid_int(x)
    return x

class Entry(ExtRegistryEntry):
    _about_ = check_regular_int

    def compute_result_annotation(self, s_arg):
        from rpython.annotator.model import SomeInteger
        if not SomeInteger().contains(s_arg):
            raise ExpectedRegularInt(s_arg)
        return s_arg

    def specialize_call(self, hop):
        hop.exception_cannot_occur()
        return hop.inputarg(hop.args_r[0], arg=0)

def check_list_of_chars(l):
    if not we_are_translated():
        assert isinstance(l, list)
        for x in l:
            assert isinstance(x, (unicode, str)) and len(x) == 1
    return l

class NotAListOfChars(Exception):
    pass

class Entry(ExtRegistryEntry):
    _about_ = check_list_of_chars

    def compute_result_annotation(self, s_arg):
        from rpython.annotator.model import SomeList, s_None
        from rpython.annotator.model import SomeChar, SomeUnicodeCodePoint
        from rpython.annotator.model import SomeImpossibleValue
        if s_None.contains(s_arg):
            return s_arg    # only None: just return
        assert isinstance(s_arg, SomeList)
        if not isinstance(
                s_arg.listdef.listitem.s_value,
                (SomeChar, SomeUnicodeCodePoint, SomeImpossibleValue)):
            raise NotAListOfChars
        return s_arg

    def specialize_call(self, hop):
        hop.exception_cannot_occur()
        return hop.inputarg(hop.args_r[0], arg=0)


