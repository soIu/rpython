from rpython.rlib.objectmodel import we_are_translated


def ResOperation(opnum, args, result, descr=None):
    cls = opclasses[opnum]
    op = cls(result)
    op.initarglist(args)
    if descr is not None:
        assert isinstance(op, ResOpWithDescr)
        if opnum == rop.FINISH:
            assert descr.final_descr
        elif op.is_guard():
            assert not descr.final_descr
        op.setdescr(descr)
    return op


class AbstractResOp(object):
    """The central ResOperation class, representing one operation."""

    # debug
    name = ""
    pc = 0
    opnum = 0
    _cls_has_bool_result = False
    boolreflex = -1
    boolinverse = -1

    _attrs_ = ('result',)

    def __init__(self, result):
        self.result = result

    def getopnum(self):
        return self.opnum

    # methods implemented by the arity mixins
    # ---------------------------------------

    def initarglist(self, args):
        "This is supposed to be called only just after the ResOp has been created"
        raise NotImplementedError

    def getarglist(self):
        raise NotImplementedError

    def getarg(self, i):
        raise NotImplementedError

    def setarg(self, i, box):
        raise NotImplementedError

    def numargs(self):
        raise NotImplementedError

    # methods implemented by GuardResOp
    # ---------------------------------

    def getfailargs(self):
        return None

    def setfailargs(self, fail_args):
        raise NotImplementedError

    # methods implemented by ResOpWithDescr
    # -------------------------------------

    def getdescr(self):
        return None

    def setdescr(self, descr):
        raise NotImplementedError

    def cleardescr(self):
        pass

    # common methods
    # --------------

    def copy_and_change(self, opnum, args=None, result=None, descr=None):
        "shallow copy: the returned operation is meant to be used in place of self"
        if args is None:
            args = self.getarglist()
        if result is None:
            result = self.result
        if descr is None:
            descr = self.getdescr()
        newop = ResOperation(opnum, args, result, descr)
        return newop

    def clone(self):
        args = self.getarglist()
        descr = self.getdescr()
        op = ResOperation(self.getopnum(), args[:], self.result, descr)
        if not we_are_translated():
            op.name = self.name
            op.pc = self.pc
        return op

    def __repr__(self):
        try:
            return self.repr()
        except NotImplementedError:
            return object.__repr__(self)

    def repr(self, graytext=False):
        # RPython-friendly version
        if self.result is not None:
            sres = '%s = ' % (self.result,)
        else:
            sres = ''
        if self.name:
            prefix = "%s:%s   " % (self.name, self.pc)
            if graytext:
                prefix = "\f%s\f" % prefix
        else:
            prefix = ""
        args = self.getarglist()
        descr = self.getdescr()
        if descr is None or we_are_translated():
            return '%s%s%s(%s)' % (prefix, sres, self.getopname(),
                                   ', '.join([str(a) for a in args]))
        else:
            return '%s%s%s(%s)' % (prefix, sres, self.getopname(),
                                   ', '.join([str(a) for a in args] +
                                             ['descr=%r' % descr]))

    def getopname(self):
        try:
            return opname[self.getopnum()].lower()
        except KeyError:
            return '<%d>' % self.getopnum()

    def is_guard(self):
        return rop._GUARD_FIRST <= self.getopnum() <= rop._GUARD_LAST

    def is_foldable_guard(self):
        return rop._GUARD_FOLDABLE_FIRST <= self.getopnum() <= rop._GUARD_FOLDABLE_LAST

    def is_guard_exception(self):
        return (self.getopnum() == rop.GUARD_EXCEPTION or
                self.getopnum() == rop.GUARD_NO_EXCEPTION)

    def is_guard_overflow(self):
        return (self.getopnum() == rop.GUARD_OVERFLOW or
                self.getopnum() == rop.GUARD_NO_OVERFLOW)

    def is_always_pure(self):
        return rop._ALWAYS_PURE_FIRST <= self.getopnum() <= rop._ALWAYS_PURE_LAST

    def has_no_side_effect(self):
        return rop._NOSIDEEFFECT_FIRST <= self.getopnum() <= rop._NOSIDEEFFECT_LAST

    def can_raise(self):
        return rop._CANRAISE_FIRST <= self.getopnum() <= rop._CANRAISE_LAST

    def is_malloc(self):
        # a slightly different meaning from can_malloc
        return rop._MALLOC_FIRST <= self.getopnum() <= rop._MALLOC_LAST

    def can_malloc(self):
        return self.is_call() or self.is_malloc()

    def is_call(self):
        return rop._CALL_FIRST <= self.getopnum() <= rop._CALL_LAST

    def is_ovf(self):
        return rop._OVF_FIRST <= self.getopnum() <= rop._OVF_LAST

    def is_comparison(self):
        return self.is_always_pure() and self.returns_bool_result()

    def is_final(self):
        return rop._FINAL_FIRST <= self.getopnum() <= rop._FINAL_LAST

    def returns_bool_result(self):
        return self._cls_has_bool_result


# ===================
# Top of the hierachy
# ===================

class PlainResOp(AbstractResOp):
    pass


class ResOpWithDescr(AbstractResOp):

    _descr = None

    def getdescr(self):
        return self._descr

    def setdescr(self, descr):
        # for 'call', 'new', 'getfield_gc'...: the descr is a prebuilt
        # instance provided by the backend holding details about the type
        # of the operation.  It must inherit from AbstractDescr.  The
        # backend provides it with cpu.fielddescrof(), cpu.arraydescrof(),
        # cpu.calldescrof(), and cpu.typedescrof().
        self._check_descr(descr)
        self._descr = descr

    def cleardescr(self):
        self._descr = None

    def _check_descr(self, descr):
        if not we_are_translated() and getattr(descr, 'I_am_a_descr', False):
            return # needed for the mock case in oparser_model
        from rpython.jit.metainterp.history import check_descr
        check_descr(descr)


class GuardResOp(ResOpWithDescr):

    _fail_args = None

    rd_snapshot = None
    rd_frame_info_list = None

    def getfailargs(self):
        return self._fail_args

    def setfailargs(self, fail_args):
        self._fail_args = fail_args

    def copy_and_change(self, opnum, args=None, result=None, descr=None):
        newop = AbstractResOp.copy_and_change(self, opnum, args, result, descr)
        assert isinstance(newop, GuardResOp)
        newop.setfailargs(self.getfailargs())
        newop.rd_snapshot = self.rd_snapshot
        newop.rd_frame_info_list = self.rd_frame_info_list
        return newop

    def clone(self):
        newop = AbstractResOp.clone(self)
        assert isinstance(newop, GuardResOp)
        newop.setfailargs(self.getfailargs())
        newop.rd_snapshot = self.rd_snapshot
        newop.rd_frame_info_list = self.rd_frame_info_list
        return newop

# ============
# arity mixins
# ============

class NullaryOp(object):
    _mixin_ = True

    def initarglist(self, args):
        assert len(args) == 0

    def getarglist(self):
        return []

    def numargs(self):
        return 0

    def getarg(self, i):
        raise IndexError

    def setarg(self, i, box):
        raise IndexError


class UnaryOp(object):
    _mixin_ = True
    _arg0 = None

    def initarglist(self, args):
        assert len(args) == 1
        self._arg0, = args

    def getarglist(self):
        return [self._arg0]

    def numargs(self):
        return 1

    def getarg(self, i):
        if i == 0:
            return self._arg0
        else:
            raise IndexError

    def setarg(self, i, box):
        if i == 0:
            self._arg0 = box
        else:
            raise IndexError


class BinaryOp(object):
    _mixin_ = True
    _arg0 = None
    _arg1 = None

    def initarglist(self, args):
        assert len(args) == 2
        self._arg0, self._arg1 = args

    def numargs(self):
        return 2

    def getarg(self, i):
        if i == 0:
            return self._arg0
        elif i == 1:
            return self._arg1
        else:
            raise IndexError

    def setarg(self, i, box):
        if i == 0:
            self._arg0 = box
        elif i == 1:
            self._arg1 = box
        else:
            raise IndexError

    def getarglist(self):
        return [self._arg0, self._arg1]


class TernaryOp(object):
    _mixin_ = True
    _arg0 = None
    _arg1 = None
    _arg2 = None

    def initarglist(self, args):
        assert len(args) == 3
        self._arg0, self._arg1, self._arg2 = args

    def getarglist(self):
        return [self._arg0, self._arg1, self._arg2]

    def numargs(self):
        return 3

    def getarg(self, i):
        if i == 0:
            return self._arg0
        elif i == 1:
            return self._arg1
        elif i == 2:
            return self._arg2
        else:
            raise IndexError

    def setarg(self, i, box):
        if i == 0:
            self._arg0 = box
        elif i == 1:
            self._arg1 = box
        elif i == 2:
            self._arg2 = box
        else:
            raise IndexError


class N_aryOp(object):
    _mixin_ = True
    _args = None

    def initarglist(self, args):
        self._args = args
        if not we_are_translated() and \
               self.__class__.__name__.startswith('FINISH'):   # XXX remove me
            assert len(args) <= 1      # FINISH operations take 0 or 1 arg now

    def getarglist(self):
        return self._args

    def numargs(self):
        return len(self._args)

    def getarg(self, i):
        return self._args[i]

    def setarg(self, i, box):
        self._args[i] = box


# ____________________________________________________________

_oplist = [
    '_FINAL_FIRST',
    'JUMP/*d',
    'FINISH/*d',
    '_FINAL_LAST',

    'LABEL/*d',

    '_GUARD_FIRST',
    '_GUARD_FOLDABLE_FIRST',
    'GUARD_TRUE/1d',
    'GUARD_FALSE/1d',
    'GUARD_VALUE/2d',
    'GUARD_CLASS/2d',
    'GUARD_NONNULL/1d',
    'GUARD_ISNULL/1d',
    'GUARD_NONNULL_CLASS/2d',
    '_GUARD_FOLDABLE_LAST',
    'GUARD_NO_EXCEPTION/0d',    # may be called with an exception currently set
    'GUARD_EXCEPTION/1d',       # may be called with an exception currently set
    'GUARD_NO_OVERFLOW/0d',
    'GUARD_OVERFLOW/0d',
    'GUARD_NOT_FORCED/0d',      # may be called with an exception currently set
    'GUARD_NOT_FORCED_2/0d',    # same as GUARD_NOT_FORCED, but for finish()
    'GUARD_NOT_INVALIDATED/0d',
    'GUARD_FUTURE_CONDITION/0d', # is removable, may be patched by an optimization
    '_GUARD_LAST', # ----- end of guard operations -----

    '_NOSIDEEFFECT_FIRST', # ----- start of no_side_effect operations -----
    '_ALWAYS_PURE_FIRST', # ----- start of always_pure operations -----
    'INT_ADD/2',
    'INT_SUB/2',
    'INT_MUL/2',
    'INT_FLOORDIV/2',
    'UINT_FLOORDIV/2',
    'INT_MOD/2',
    'INT_AND/2',
    'INT_OR/2',
    'INT_XOR/2',
    'INT_RSHIFT/2',
    'INT_LSHIFT/2',
    'UINT_RSHIFT/2',
    'INT_SIGNEXT/2',
    'FLOAT_ADD/2',
    'FLOAT_SUB/2',
    'FLOAT_MUL/2',
    'FLOAT_TRUEDIV/2',
    'FLOAT_NEG/1',
    'FLOAT_ABS/1',
    'CAST_FLOAT_TO_INT/1',          # don't use for unsigned ints; we would
    'CAST_INT_TO_FLOAT/1',          # need some messy code in the backend
    'CAST_FLOAT_TO_SINGLEFLOAT/1',
    'CAST_SINGLEFLOAT_TO_FLOAT/1',
    'CONVERT_FLOAT_BYTES_TO_LONGLONG/1',
    'CONVERT_LONGLONG_BYTES_TO_FLOAT/1',
    #
    'INT_LT/2b',
    'INT_LE/2b',
    'INT_EQ/2b',
    'INT_NE/2b',
    'INT_GT/2b',
    'INT_GE/2b',
    'UINT_LT/2b',
    'UINT_LE/2b',
    'UINT_GT/2b',
    'UINT_GE/2b',
    'FLOAT_LT/2b',
    'FLOAT_LE/2b',
    'FLOAT_EQ/2b',
    'FLOAT_NE/2b',
    'FLOAT_GT/2b',
    'FLOAT_GE/2b',
    #
    'INT_IS_ZERO/1b',
    'INT_IS_TRUE/1b',
    'INT_NEG/1',
    'INT_INVERT/1',
    'INT_FORCE_GE_ZERO/1',
    #
    'SAME_AS/1',      # gets a Const or a Box, turns it into another Box
    'CAST_PTR_TO_INT/1',
    'CAST_INT_TO_PTR/1',
    #
    'PTR_EQ/2b',
    'PTR_NE/2b',
    'INSTANCE_PTR_EQ/2b',
    'INSTANCE_PTR_NE/2b',
    #
    'ARRAYLEN_GC/1d',
    'STRLEN/1',
    'STRGETITEM/2',
    'GETFIELD_GC_PURE/1d',
    'GETFIELD_RAW_PURE/1d',
    'GETARRAYITEM_GC_PURE/2d',
    'GETARRAYITEM_RAW_PURE/2d',
    'UNICODELEN/1',
    'UNICODEGETITEM/2',
    #
    '_ALWAYS_PURE_LAST',  # ----- end of always_pure operations -----

    'GETARRAYITEM_GC/2d',
    'GETARRAYITEM_RAW/2d',
    'GETINTERIORFIELD_GC/2d',
    'RAW_LOAD/2d',
    'GETFIELD_GC/1d',
    'GETFIELD_RAW/1d',
    '_MALLOC_FIRST',
    'NEW/0d',             #-> GcStruct, gcptrs inside are zeroed (not the rest)
    'NEW_WITH_VTABLE/1',  #-> GcStruct with vtable, gcptrs inside are zeroed
    'NEW_ARRAY/1d',       #-> GcArray, not zeroed. only for arrays of primitives
    'NEW_ARRAY_CLEAR/1d', #-> GcArray, fully zeroed
    'NEWSTR/1',           #-> STR, the hash field is zeroed
    'NEWUNICODE/1',       #-> UNICODE, the hash field is zeroed
    '_MALLOC_LAST',
    'FORCE_TOKEN/0',
    'VIRTUAL_REF/2',         # removed before it's passed to the backend
    'MARK_OPAQUE_PTR/1b',
    # this one has no *visible* side effect, since the virtualizable
    # must be forced, however we need to execute it anyway
    '_NOSIDEEFFECT_LAST', # ----- end of no_side_effect operations -----

    'INCREMENT_DEBUG_COUNTER/1',
    'SETARRAYITEM_GC/3d',
    'SETARRAYITEM_RAW/3d',
    'SETINTERIORFIELD_GC/3d',
    'SETINTERIORFIELD_RAW/3d',    # right now, only used by tests
    'RAW_STORE/3d',
    'SETFIELD_GC/2d',
    'ZERO_PTR_FIELD/2', # only emitted by the rewrite, clears a pointer field
                        # at a given constant offset, no descr
    'ZERO_ARRAY/3d',    # only emitted by the rewrite, clears (part of) an array
                        # [arraygcptr, firstindex, length], descr=ArrayDescr
    'SETFIELD_RAW/2d',
    'STRSETITEM/3',
    'UNICODESETITEM/3',
    'COND_CALL_GC_WB/1d',       # [objptr] (for the write barrier)
    'COND_CALL_GC_WB_ARRAY/2d', # [objptr, arrayindex] (write barr. for array)
    'DEBUG_MERGE_POINT/*',      # debugging only
    'ENTER_PORTAL_FRAME/2',     # debugging only
    'LEAVE_PORTAL_FRAME/1',     # debugging only
    'JIT_DEBUG/*',              # debugging only
    'VIRTUAL_REF_FINISH/2',   # removed before it's passed to the backend
    'COPYSTRCONTENT/5',       # src, dst, srcstart, dststart, length
    'COPYUNICODECONTENT/5',
    'QUASIIMMUT_FIELD/1d',    # [objptr], descr=SlowMutateDescr
    'RECORD_KNOWN_CLASS/2',   # [objptr, clsptr]
    'KEEPALIVE/1',

    '_CANRAISE_FIRST', # ----- start of can_raise operations -----
    '_CALL_FIRST',
    'CALL/*d',
    'COND_CALL/*d', # a conditional call, with first argument as a condition
    'CALL_ASSEMBLER/*d',  # call already compiled assembler
    'CALL_MAY_FORCE/*d',
    'CALL_LOOPINVARIANT/*d',
    'CALL_RELEASE_GIL/*d',  # release the GIL and "close the stack" for asmgcc
    'CALL_PURE/*d',             # removed before it's passed to the backend
    'CALL_MALLOC_GC/*d',      # like CALL, but NULL => propagate MemoryError
    'CALL_MALLOC_NURSERY/1',  # nursery malloc, const number of bytes, zeroed
    'CALL_MALLOC_NURSERY_VARSIZE/3d',
    'CALL_MALLOC_NURSERY_VARSIZE_FRAME/1',
    # nursery malloc, non-const number of bytes, zeroed
    # note that the number of bytes must be well known to be small enough
    # to fulfill allocating in the nursery rules (and no card markings)
    '_CALL_LAST',
    '_CANRAISE_LAST', # ----- end of can_raise operations -----

    '_OVF_FIRST', # ----- start of is_ovf operations -----
    'INT_ADD_OVF/2',
    'INT_SUB_OVF/2',
    'INT_MUL_OVF/2',
    '_OVF_LAST', # ----- end of is_ovf operations -----
    '_LAST',     # for the backend to add more internal operations
]

# ____________________________________________________________

class rop(object):
    pass

opclasses = []   # mapping numbers to the concrete ResOp class
opname = {}      # mapping numbers to the original names, for debugging
oparity = []     # mapping numbers to the arity of the operation or -1
opwithdescr = [] # mapping numbers to a flag "takes a descr"


def setup(debug_print=False):
    for i, name in enumerate(_oplist):
        if debug_print:
            print '%30s = %d' % (name, i)
        if '/' in name:
            name, arity = name.split('/')
            withdescr = 'd' in arity
            boolresult = 'b' in arity
            arity = arity.rstrip('db')
            if arity == '*':
                arity = -1
            else:
                arity = int(arity)
        else:
            arity, withdescr, boolresult = -1, True, False       # default
        setattr(rop, name, i)
        if not name.startswith('_'):
            opname[i] = name
            cls = create_class_for_op(name, i, arity, withdescr)
            cls._cls_has_bool_result = boolresult
        else:
            cls = None
        opclasses.append(cls)
        oparity.append(arity)
        opwithdescr.append(withdescr)
    assert len(opclasses) == len(oparity) == len(opwithdescr) == len(_oplist)

def get_base_class(mixin, base):
    try:
        return get_base_class.cache[(mixin, base)]
    except KeyError:
        arity_name = mixin.__name__[:-2]  # remove the trailing "Op"
        name = arity_name + base.__name__ # something like BinaryPlainResOp
        bases = (mixin, base)
        cls = type(name, bases, {})
        get_base_class.cache[(mixin, base)] = cls
        return cls
get_base_class.cache = {}

def create_class_for_op(name, opnum, arity, withdescr):
    arity2mixin = {
        0: NullaryOp,
        1: UnaryOp,
        2: BinaryOp,
        3: TernaryOp
    }

    is_guard = name.startswith('GUARD')
    if is_guard:
        assert withdescr
        baseclass = GuardResOp
    elif withdescr:
        baseclass = ResOpWithDescr
    else:
        baseclass = PlainResOp
    mixin = arity2mixin.get(arity, N_aryOp)

    cls_name = '%s_OP' % name
    bases = (get_base_class(mixin, baseclass),)
    dic = {'opnum': opnum}
    return type(cls_name, bases, dic)

setup(__name__ == '__main__')   # print out the table when run directly
del _oplist

_opboolinverse = {
    rop.INT_EQ: rop.INT_NE,
    rop.INT_NE: rop.INT_EQ,
    rop.INT_LT: rop.INT_GE,
    rop.INT_GE: rop.INT_LT,
    rop.INT_GT: rop.INT_LE,
    rop.INT_LE: rop.INT_GT,

    rop.UINT_LT: rop.UINT_GE,
    rop.UINT_GE: rop.UINT_LT,
    rop.UINT_GT: rop.UINT_LE,
    rop.UINT_LE: rop.UINT_GT,

    rop.FLOAT_EQ: rop.FLOAT_NE,
    rop.FLOAT_NE: rop.FLOAT_EQ,
    rop.FLOAT_LT: rop.FLOAT_GE,
    rop.FLOAT_GE: rop.FLOAT_LT,
    rop.FLOAT_GT: rop.FLOAT_LE,
    rop.FLOAT_LE: rop.FLOAT_GT,

    rop.PTR_EQ: rop.PTR_NE,
    rop.PTR_NE: rop.PTR_EQ,
}

_opboolreflex = {
    rop.INT_EQ: rop.INT_EQ,
    rop.INT_NE: rop.INT_NE,
    rop.INT_LT: rop.INT_GT,
    rop.INT_GE: rop.INT_LE,
    rop.INT_GT: rop.INT_LT,
    rop.INT_LE: rop.INT_GE,

    rop.UINT_LT: rop.UINT_GT,
    rop.UINT_GE: rop.UINT_LE,
    rop.UINT_GT: rop.UINT_LT,
    rop.UINT_LE: rop.UINT_GE,

    rop.FLOAT_EQ: rop.FLOAT_EQ,
    rop.FLOAT_NE: rop.FLOAT_NE,
    rop.FLOAT_LT: rop.FLOAT_GT,
    rop.FLOAT_GE: rop.FLOAT_LE,
    rop.FLOAT_GT: rop.FLOAT_LT,
    rop.FLOAT_LE: rop.FLOAT_GE,

    rop.PTR_EQ: rop.PTR_EQ,
    rop.PTR_NE: rop.PTR_NE,
}

def setup2():
    for cls in opclasses:
        if cls is None:
            continue
        opnum = cls.opnum
        if opnum in _opboolreflex:
            cls.boolreflex = _opboolreflex[opnum]
        if opnum in _opboolinverse:
            cls.boolinverse = _opboolinverse[opnum]

setup2()
del _opboolinverse
del _opboolreflex

def get_deep_immutable_oplist(operations):
    """
    When not we_are_translated(), turns ``operations`` into a frozenlist and
    monkey-patch its items to make sure they are not mutated.

    When we_are_translated(), do nothing and just return the old list.
    """
    from rpython.tool.frozenlist import frozenlist
    if we_are_translated():
        return operations
    #
    def setarg(*args):
        assert False, "operations cannot change at this point"
    def setdescr(*args):
        assert False, "operations cannot change at this point"
    newops = frozenlist(operations)
    for op in newops:
        op.setarg = setarg
        op.setdescr = setdescr
    return newops
