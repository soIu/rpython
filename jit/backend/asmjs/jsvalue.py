
import os
import textwrap

from rpython.rlib.rarithmetic import intmask
from rpython.memory.gctypelayout import GCData
from rpython.rtyper.lltypesystem import lltype, rffi, llmemory
from rpython.rtyper.lltypesystem.lloperation import llop
from rpython.jit.backend.llsupport.descr import unpack_fielddescr
from rpython.jit.metainterp.history import (AbstractValue, Box, Const,
                                            INT, REF, FLOAT, ConstInt,
                                            ConstPtr, ConstFloat)

from rpython.jit.backend.asmjs.arch import SANITYCHECK, WORD

STANDARD_FUNCTIONS = ("imul", "sqrt", "jitInvoke")

# Type markers to distinguish the type of values.
# This is a class heirarchy, but rpython wont let me use issubclass().

Unknown = 0
Doublish = 1
Double = 2
Intish = 3
Int = 4
Signed = 5
Unsigned = 6
Fixnum = 7


def gettype(value):
    if value is None:
        return Fixnum
    if isinstance(value, ASMJSValue):
        return value.jstype
    if isinstance(value, Box) or isinstance(value, Const):
        if value.type == FLOAT:
            return Double
        if value.type == INT:
            return Fixnum
        if value.type == REF:
            return Fixnum
    os.write(2, "Unknown ASMJS value: %s" % (value,))
    raise RuntimeError("Unknown ASMJS value: %s" % (value,))


def istype(value, typ):
    vtyp = gettype(value)
    if typ == Unknown:
        return True
    if typ == Doublish:
        return vtyp == Doublish or vtyp == Double
    if typ == Double:
        return vtyp == Double
    if typ == Intish:
        return vtyp == Intish or vtyp == Int or vtyp == Signed \
            or vtyp == Unsigned or vtyp == Fixnum
    if typ == Int:
        return vtyp == Int or vtyp == Signed \
            or vtyp == Unsigned or vtyp == Fixnum
    if typ == Signed:
        return vtyp == Signed
    if typ == Unsigned:
        return vtyp == Unsigned
    if typ == Fixnum:
        return vtyp == Fixnum
    os.write(2, "Unexpected jstype: %s\n" % (typ,))
    raise RuntimeError("Unexpected jstype: %s" % (typ,))


def _getint(value):
    if isinstance(value, ConstInt):
        return rffi.cast(lltype.Signed, value.getint())
    if isinstance(value, ConstPtr):
        return rffi.cast(lltype.Signed, value.getref_base())
    os.write(2, "Unexpected jstype: %s\n" % (value,))
    raise RuntimeError("Unexpected jstype: %s" % (value,))


#  Singleton instances used to indicate the type of data being loaded from
#  or stored to the heap.


class HeapType(object):
    """Object representing different types that can be loaded from the heap.

    Instances of HeapType are used as simple type markers when generating
    heap loads and stores.  They control the particular view of the heap used
    as well as the size of the resulting value.
    """

    def __init__(self, jstype, heap_name,  shift, signed):
        self.jstype = jstype
        self.heap_name = heap_name
        self.shift = shift
        self.size = 2**shift
        self.signed = signed

    def cast_integer(self, value):
        """Cast an integer to this type."""
        return cast_integer(value, self.size, self.signed)

    @staticmethod
    def from_kind(kind):
        """Determine the HeapType marker that matches the given ll kind."""
        if kind == FLOAT:
            return Float64
        if kind == INT:
            return Int32
        if kind == REF:
            return UInt32
        os.write(2, "Unsupported kind: %s\n" % (kind,))
        raise RuntimeError("unsupported kind: %s" % (kind,))

    @staticmethod
    def from_box(box):
        """Determine the HeapType marker that matches the given Box."""
        if box is None:
            return Int32
        elif isinstance(box, Box) or isinstance(box, Const):
            if box.type == FLOAT:
                return Float64
            if box.type == INT:
                return Int32
            if box.type == REF:
                return UInt32
            raise NotImplementedError("unsupported box type: %s" % (box.type,))
        else:
            raise NotImplementedError("from_box does not support %s" % (box,))

    @staticmethod
    def from_size(size):
        """Determine the HeapType marker for objects of the given size."""
        if size == 1:
            return UInt8
        if size == 2:
            return UInt16
        if size == 4:
            return UInt32
        if size == 8:
            return Float64
        raise NotImplementedError("unsupported box size: %d" % (size,))

    @staticmethod
    def from_size_and_sign(size, sign):
        """Determine HeapType marker matching the given size and signedness."""
        if size == 1:
            if not sign:
                return UInt8
            return Int8
        if size == 2:
            if not sign:
                return UInt16
            return Int16
        if size == 4:
            if not sign:
                return UInt32
            return Int32
        if size == 8:
            return Float64
        raise NotImplementedError("unsupported box size: %d" % (size,))


Int8 = HeapType(Intish, "HI8", 0, True)
Int16 = HeapType(Intish, "HI16", 1, True)
Int32 = HeapType(Intish, "HI32", 2, True)
UInt8 = HeapType(Intish, "HU8", 0, False)
UInt16 = HeapType(Intish, "HU16", 1, False)
UInt32 = HeapType(Intish, "HU32", 2, False)
Float32 = HeapType(Doublish, "HF32", 2, True)
Float64 = HeapType(Doublish, "HF64", 3, True)


#  Classes or factories that produce ASMJSValue objects.
#  These are objects that know how to render a particular value into asmjs
#  output code, via a "ASMJSBuilder" object.


def iter_variables(expr):
    varlist = []
    _gather_variables(expr, varlist)
    return varlist


def _gather_variables(expr, varlist):
    if isinstance(expr, ASMJSValue):
        expr._gather_variables(varlist)


class ASMJSValue(AbstractValue):
    """An AbstractValue that knows how to render its own asmjs code."""

    jstype = Unknown

    def emit_value(self, js):
        raise NotImplementedError

    def _gather_variables(self, varlist):
        pass


class Variable(ASMJSValue):
    """An ASMJSValue representing a variable."""

    def __init__(self, varname):
        self.varname = varname

    def emit_value(self, js):
        js.emit(self.varname)

    def _gather_variables(self, varlist):
        varlist.append(self)


class IntVar(Variable):
    """An ASMJSValue representing a variable of type Int."""
    jstype = Int


class DoubleVar(Variable):
    """An ASMJSValue representing a variable of type Double."""
    jstype = Double


class TempDoublePtr(IntVar):
    """ASMJSValue representing the address of double storage scratch-space.

    Since we cannot guarantee that memory allocations are aligned to 8-byte
    boundaries, we may need to use some aligned scratch space when reading or
    writing floats.  Emscripten provides a "tempDoublePtr" variable pointing
    to a chunk of memory just for this purpose; this class exposes it as a
    value we can use in expressions.
    """

    jstype = Int

    def __init__(self):
        IntVar.__init__(self, "tempDoublePtr")


tempDoublePtr = TempDoublePtr()


zero = false = ConstInt(0)
one = true = ConstInt(1)
word = ConstInt(WORD)
dword = ConstInt(2*WORD)


class HeapData(ASMJSValue):
    """ASMJSValue representing data read from the heap."""

    jstype = Unknown

    def __init__(self, heaptype, addr):
        assert isinstance(heaptype, HeapType)
        assert isinstance(addr, AbstractValue)
        if not istype(addr, Intish):
            addr = IntCast(addr)
        self.jstype = heaptype.jstype
        self.heaptype = heaptype
        self.addr = addr
        if SANITYCHECK:
            # Alignment issues mean we can't reliably read multi-word data...
            assert heaptype.size <= 4

    def emit_value(self, js):
        js.emit(self.heaptype.heap_name)
        js.emit("[(")
        js.emit_value(self.addr)
        js.emit(")>>%d]" % (self.heaptype.shift,))


class ASMJSUnaryOp(ASMJSValue):
    """ASMJSValue representing a unary operation on a single value."""

    operator = None
    operand = None

    def __init__(self, operand):
        self.operand = operand

    def __str__(self):
        return "%s(%s)" % (self.__class__.__name__, self.operand)

    def emit_value(self, js):
        js.emit(self.operator)
        js.emit("(")
        js.emit_value(self.operand)
        js.emit(")")

    def _gather_variables(self, varlist):
        _gather_variables(self.operand, varlist)


class UPlus(ASMJSUnaryOp):
    """ASMJSValue representing unary plus."""

    jstype = Double
    operator = "+"

    def __init__(self, operand):
        if istype(operand, Doublish):
            pass
        elif istype(operand, Signed):
            pass
        elif istype(operand, Unsigned):
            pass
        else:
            operand = SignedCast(operand)
        ASMJSUnaryOp.__init__(self, operand)


class UMinus(ASMJSUnaryOp):
    """ASMJSValue representing unary minus."""

    operator = "-"

    def __init__(self, operand):
        if istype(operand, Doublish):
            self.jstype = Double
        else:
            if not istype(operand, Int):
                if SANITYCHECK:
                    assert istype(operand, Intish)
                operand = IntCast(operand)
            self.jstype = Intish
        ASMJSUnaryOp.__init__(self, operand)


class UNeg(ASMJSUnaryOp):
    """ASMJSValue representing unary negation."""

    jstype = Signed
    operator = "~"


class UNot(ASMJSUnaryOp):
    """ASMJSValue representing unary logical-not."""
    jstype = Int
    operator = "!"

    def __init__(self, operand):
        if not istype(operand, Int):
            operand = IntCast(operand)
        ASMJSUnaryOp.__init__(self, operand)


class ASMJSBinaryOp(ASMJSValue):
    """ASMJSValue representing a binary operation on two other values."""

    operator = None
    lhs = None
    rhs = None

    def __init__(self, lhs, rhs):
        self.lhs = lhs
        self.rhs = rhs

    def __str__(self):
        return "%s(%s, %s)" % (self.__class__.__name__, self.lhs, self.rhs)

    def emit_value(self, js):
        js.emit("(")
        js.emit_value(self.lhs)
        js.emit(")")
        js.emit(self.operator)
        js.emit("(")
        js.emit_value(self.rhs)
        js.emit(")")

    def _gather_variables(self, varlist):
        _gather_variables(self.lhs, varlist)
        _gather_variables(self.rhs, varlist)


class Plus(ASMJSBinaryOp):
    """ASMJSBinaryOp representing addition."""

    jstype = Double
    operator = "+"

    def __init__(self, lhs, rhs):
        if istype(lhs, Intish):
            if not istype(lhs, Int):
                lhs = IntCast(lhs)
            if not istype(rhs, Int):
                rhs = IntCast(rhs)
            self.jstype = Intish
        else:
            if not istype(lhs, Double):
                lhs = DoubleCast(lhs)
            if not istype(rhs, Double):
                rhs = DoubleCast(lhs)
        ASMJSBinaryOp.__init__(self, lhs, rhs)


class Minus(ASMJSBinaryOp):
    """ASMJSBinaryOp representing subtraction."""

    jstype = Double
    operator = "-"

    def __init__(self, lhs, rhs):
        if istype(lhs, Intish):
            if not istype(lhs, Int):
                lhs = IntCast(lhs)
            if not istype(rhs, Int):
                rhs = IntCast(rhs)
        else:
            if SANITYCHECK:
                assert istype(lhs, Doublish)
            if not istype(rhs, Doublish):
                rhs = DoubleCast(rhs)
        ASMJSBinaryOp.__init__(self, lhs, rhs)


class Mul(ASMJSBinaryOp):
    """ASMJSBinaryOp representing multiplication."""

    jstype = Double
    operator = "*"

    def __init__(self, lhs, rhs):
        if SANITYCHECK:
            assert istype(lhs, Doublish)
            assert istype(rhs, Doublish)
        ASMJSBinaryOp.__init__(self, lhs, rhs)


class IMul(ASMJSBinaryOp):
    """ASMJSBinaryOp representing integer multiplication."""

    jstype = Signed
    operator = "*"

    def __init__(self, lhs, rhs):
        if not istype(lhs, Int):
            lhs = IntCast(lhs)
        if not istype(rhs, Int):
            rhs = IntCast(rhs)
        if isinstance(rhs, ConstInt):
            self.jstype = Intish
        ASMJSBinaryOp.__init__(self, lhs, rhs)

    def emit_value(self, js):
        # For small constant RHS, we can emit normal multiplication.
        # For other arguments we must use Math.imul helper.
        if isinstance(self.rhs, ConstInt):
            if -2**20 < self.rhs.getint() < 2**20:
                return ASMJSBinaryOp.emit_value(self, js)
        js.emit("imul(")
        js.emit_value(self.lhs)
        js.emit(",")
        js.emit_value(self.rhs)
        js.emit(")|0")


class _Divish(ASMJSBinaryOp):
    """ASMJSBinaryOp representing division or modulus."""

    def __init__(self, lhs, rhs):
        if istype(lhs, Signed):
            self.jstype = Intish
            if not istype(rhs, Signed):
                rhs = SignedCast(rhs)
        elif istype(lhs, Unsigned):
            self.jstype = Intish
            if not istype(rhs, Unsigned):
                rhs = UnsignedCast(rhs)
        elif istype(lhs, Intish):
            self.jstype = Intish
            lhs = SignedCast(lhs)
            if not istype(rhs, Signed):
                rhs = SignedCast(rhs)
        else:
            if SANITYCHECK:
                assert istype(lhs, Doublish)
            if not istype(rhs, Doublish):
                rhs = DoubleCast(rhs)
            self.jstype = Double
        ASMJSBinaryOp.__init__(self, lhs, rhs)


class Div(_Divish):
    """ASMJSBinaryOp representing division."""
    operator = "/"


class Mod(_Divish):
    """ASMJSBinaryOp representing modulus."""
    operator = "%"


class _Bitwise(ASMJSBinaryOp):
    """ASMJSBinaryOp representing a bitwise logical operator."""
    jstype = Signed


class Or(_Bitwise):
    """ASMJSBinaryOp representing bitwise-or."""
    operator = "|"


class And(_Bitwise):
    """ASMJSBinaryOp representing bitwise-and."""
    operator = "&"


class Xor(_Bitwise):
    """ASMJSBinaryOp representing bitwise-xor."""
    operator = "^"


class LShift(_Bitwise):
    """ASMJSBinaryOp representing bitwise-left-shift."""
    operator = "<<"


class RShift(_Bitwise):
    """ASMJSBinaryOp representing bitwise-right-shift."""
    operator = ">>"


class URShift(_Bitwise):
    """ASMJSBinaryOp representing bitwise-unsigned-right-shift."""
    jstype = Unsigned
    operator = ">>>"


class _Comparison(ASMJSBinaryOp):
    """ASMJSBinaryOp representing a comparison operator."""

    jstype = Int

    def __init__(self, lhs, rhs):
        if istype(lhs, Signed):
            if not istype(rhs, Signed):
                rhs = SignedCast(rhs)
        elif istype(lhs, Unsigned):
            if not istype(rhs, Unsigned):
                rhs = UnsignedCast(rhs)
        elif istype(lhs, Intish):
            lhs = SignedCast(lhs)
            if not istype(rhs, Signed):
                rhs = SignedCast(rhs)
        else:
            if SANITYCHECK:
                assert istype(lhs, Double)
                assert istype(rhs, Double)
        ASMJSBinaryOp.__init__(self, lhs, rhs)


class LessThan(_Comparison):
    operator = "<"


class LessThanEq(_Comparison):
    operator = "<="


class GreaterThan(_Comparison):
    operator = ">"


class GreaterThanEq(_Comparison):
    operator = ">="


class Equal(_Comparison):
    operator = "=="


class NotEqual(_Comparison):
    operator = "!="


def IntCast(value):
    """Explicitly cast a value to type Int."""
    if istype(value, Int):
        return value
    return Or(value, zero)


def SignedCast(value):
    """Explicitly cast a value to type Signed."""
    if istype(value, Intish):
        return Or(value, zero)
    else:
        if SANITYCHECK:
            assert istype(value, Double)
        return UNeg(UNeg(value))


def UnsignedCast(value):
    """Explicitly cast a value to type Unsigned."""
    if not istype(value, Intish):
        value = SignedCast(value)
    return URShift(value, zero)


def DoubleCast(value):
    """Explicitly cast a value to type Double."""
    if istype(value, Double):
        return value
    return UPlus(value)


def SignedShortCast(value):
    """Explicitly cast a value to type 'signed short'."""
    shift = ConstInt(16)
    return RShift(LShift(value, shift), shift)


def UnsignedShortCast(value):
    """Explicitly cast a value to type 'usigned short'."""
    return UnsignedCast(And(value, ConstInt(0xFFFF)))


def SignedCharCast(value):
    """Explicitly cast a value to type 'signed char'."""
    shift = ConstInt(24)
    return RShift(LShift(value, shift), shift)


def UnsignedCharCast(value):
    """Explicitly cast a value to type 'unsigned char'."""
    return UnsignedCast(And(value, ConstInt(0xFF)))


def cast_integer(value, size, sign):
    """Cast integer to the given size and signedness."""
    if size == WORD:
        if sign:
            return SignedCast(value)
        else:
            return UnsignedCast(value)
    elif size == WORD // 2:
        if sign:
            return SignedShortCast(value)
        else:
            return UnsignedShortCast(value)
    elif size == WORD // 4:
        if sign:
            return SignedCharCast(value)
        else:
            return UnsignedCharCast(value)
    else:
        os.write(2, "Unknown integer size: %d\n" % (size,))
        raise RuntimeError("Unknown integer size: %d" % (size,))


class _CallFunc(ASMJSValue):
    """ASMJSValue representing result of external function call."""

    jstype = Unknown

    def __init__(self, funcname, arguments):
        self.funcname = funcname
        self.arguments = arguments

    def emit_value(self, js):
        if self.funcname not in STANDARD_FUNCTIONS:
            js.imported_functions[self.funcname] = self.funcname
        js.emit(self.funcname)
        js.emit("(")
        for i in xrange(len(self.arguments)):
            if i > 0:
                js.emit(",")
            arg = self.arguments[i]
            if not istype(arg, Double):
                if not istype(arg, Signed):
                    if not istype(arg, Unsigned):
                        if istype(arg, Doublish):
                            arg = DoubleCast(arg)
                        else:
                            arg = SignedCast(arg)
            js.emit_value(arg)
        js.emit(")")

    def _gather_variables(self, varlist):
        for arg in self.arguments:
            _gather_variables(arg, varlist)


class CallFunc(_CallFunc):
    """ASMJSValue representing result of external function call."""

    def __init__(self, funcname, arguments):
        if funcname not in STANDARD_FUNCTIONS:
            funcname = "_" + funcname
        _CallFunc.__init__(self, funcname, arguments)


class DynCallFunc(_CallFunc):
    """ASMJSValue representing result of dynamic external function call."""

    def __init__(self, callsig, addr, arguments):
        funcname = "dynCall_" + callsig
        arguments = [addr] + arguments
        _CallFunc.__init__(self, funcname, arguments)
        if callsig[0] == "i":
            self.jstype = Intish
        elif callsig[0] == "d":
            self.jstype = Doublish


frame = IntVar("frame")
tladdr = IntVar("tladdr")
label = IntVar("label")


class _FrameFieldAddr(ASMJSValue):
    """ASMJSValue representing the address of a field within the frame."""

    jstype = Intish

    def __init__(self, framevar=None):
        if framevar is None:
            framevar = frame
        self.framevar = framevar

    def emit_value(self, js):
        addr = Plus(self.framevar, ConstInt(self.calculate_offset(js.cpu)))
        addr.emit_value(js)


class FrameSlotAddr(_FrameFieldAddr):
    """ASMJSValue representing the address of a slot in frame scratch-space."""

    def __init__(self, offset, framevar=None):
        self.offset = offset
        _FrameFieldAddr.__init__(self, framevar)

    def calculate_offset(self, cpu):
        return cpu.get_baseofs_of_frame_field() + self.offset


class FrameDescrAddr(_FrameFieldAddr):
    """ASMJSValue representing the address of frame.jf_descr."""

    def calculate_offset(self, cpu):
        return cpu.get_ofs_of_frame_field("jf_descr")


class FrameForceDescrAddr(_FrameFieldAddr):
    """ASMJSValue representing the address of frame.jf_force_descr."""

    def calculate_offset(self, cpu):
        return cpu.get_ofs_of_frame_field("jf_force_descr")


class FrameNextCallAddr(_FrameFieldAddr):
    """ASMJSValue representing the address of frame.jf_extra_stack_depth."""

    def calculate_offset(self, cpu):
        return cpu.get_ofs_of_frame_field("jf_extra_stack_depth")


class FrameGuardExcAddr(_FrameFieldAddr):
    """ASMJSValue representing the address of frame.jf_guard_exc."""

    def calculate_offset(self, cpu):
        return cpu.get_ofs_of_frame_field("jf_guard_exc")


class FrameGCMapAddr(_FrameFieldAddr):
    """ASMJSValue representing the address of frame.jf_gcmap."""

    def calculate_offset(self, cpu):
        return cpu.get_ofs_of_frame_field("jf_gcmap")


class FrameSizeAddr(_FrameFieldAddr):
    """ASMJSValue representing the address of frame size field."""

    def calculate_offset(self, cpu):
        descrs = cpu.gc_ll_descr.getframedescrs(cpu)
        offset, size, _ = unpack_fielddescr(descrs.arraydescr.lendescr)
        assert size == WORD
        return offset


def ClassPtrTypeID(classptr):
    """ASMJSValue representing the typeid for a class pointer.

    This is a special value for handling class comparisons when we're not
    using type pointers.  It extracts an "expected type id" from the class
    pointer, which can be compared agaist the first half-word in the object
    pointer.  Logic shamelessly cargo-culted from x86 backend.
    """
    sizeof_ti = rffi.sizeof(GCData.TYPE_INFO)
    type_info_group = llop.gc_get_type_info_group(llmemory.Address)
    type_info_group = rffi.cast(lltype.Signed, type_info_group)
    typeid = Minus(classptr, ConstInt(sizeof_ti + type_info_group))
    typeid = RShift(typeid, ConstInt(2))
    typeid = And(typeid, ConstInt(0xFFFF))
    return typeid


# Turn binary operators into constant-folding factory functions.
# This is a simple way to reduce code duplication and to let us
# easily switch this behaviour on or off.

for nm in globals().keys():
    binop = globals()[nm]
    if not isinstance(binop, type) or not issubclass(binop, ASMJSBinaryOp):
        continue
    if not binop.operator or nm.startswith("_") or nm == "URShift":
        continue
    if nm == "Div" or nm == "Mod":
        # XXX TODO: python and js semantics differ for these operations.
        # Need to define some helpers to fold them correctly.
        continue
    binopnm = "_" + nm
    assert binopnm not in globals()
    globals()[binopnm] = binop
    wrapper_defn = textwrap.dedent("""
        def %s(lhs, rhs):
            if isinstance(lhs, ConstInt) or isinstance(lhs, ConstPtr):
                if isinstance(rhs, ConstInt) or isinstance(rhs, ConstPtr):
                    return ConstInt(intmask(_getint(lhs) %s _getint(rhs)))
            return %s(lhs, rhs)
    """) % (nm, binop.operator, binopnm)
    exec wrapper_defn in globals()
    del wrapper_defn
