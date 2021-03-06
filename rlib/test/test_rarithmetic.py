from rpython.rtyper.test.tool import BaseRtypingTest
from rpython.rtyper.test.test_llinterp import interpret
from rpython.rlib.rarithmetic import *
from rpython.rlib.rstring import ParseStringError, ParseStringOverflowError
import sys
import py

maxint_mask = (sys.maxint*2 + 1)
machbits = 0
i = 1
l = 1L
while i == l and type(i) is int:
    i *= 2
    l *= 2
    machbits += 1
#print machbits


class Test_r_int:
    def test__add__(self):
        self.binary_test(lambda x, y: x + y)
    def test__sub__(self):
        self.binary_test(lambda x, y: x - y)
    def test__mul__(self):
        self.binary_test(lambda x, y: x * y)
        x = 3; y = [2]
        assert x*y == r_int(x)*y
        assert y*x == y*r_int(x)
    def test__div__(self):
        self.binary_test(lambda x, y: x // y)
    def test__mod__(self):
        self.binary_test(lambda x, y: x % y)
    def test__divmod__(self):
        self.binary_test(divmod)
    def test__lshift__(self):
        self.binary_test(lambda x, y: x << y, (1, 2, 3))
    def test__rshift__(self):
        self.binary_test(lambda x, y: x >> y, (1, 2, 3))
    def test__or__(self):
        self.binary_test(lambda x, y: x | y)
    def test__and__(self):
        self.binary_test(lambda x, y: x & y)
    def test__xor__(self):
        self.binary_test(lambda x, y: x ^ y)
    def test__neg__(self):
        self.unary_test(lambda x: -x)
    def test__pos__(self):
        self.unary_test(lambda x: +x)
    def test__invert__(self):
        self.unary_test(lambda x: ~x)
    def test__pow__(self):
        self.binary_test(lambda x, y: x**y, (2, 3))
        self.binary_test(lambda x, y: pow(x, y, 42L), (2, 3, 5, 1000))

    def unary_test(self, f):
        for arg in (-10, -1, 0, 3, 12345):
            res = f(arg)
            cmp = f(r_int(arg))
            assert res == cmp

    def binary_test(self, f, rargs = None):
        if not rargs:
            rargs = (-10, -1, 3, 55)
        for larg in (-10, -1, 0, 3, 1234):
            for rarg in rargs:
                for types in ((int, r_int), (r_int, int), (r_int, r_int)):
                    res = f(larg, rarg)
                    left, right = types
                    cmp = f(left(larg), right(rarg))
                    assert res == cmp

class Test_r_uint:
    def test__add__(self):
        self.binary_test(lambda x, y: x + y)
    def test__sub__(self):
        self.binary_test(lambda x, y: x - y)
    def test__mul__(self):
        self.binary_test(lambda x, y: x * y)
        x = 3; y = [2]
        assert x*y == r_uint(x)*y
        assert y*x == y*r_uint(x)
    def test__div__(self):
        self.binary_test(lambda x, y: x // y)
    def test__mod__(self):
        self.binary_test(lambda x, y: x % y)
    def test__divmod__(self):
        self.binary_test(divmod)
    def test__lshift__(self):
        self.binary_test(lambda x, y: x << y, (1, 2, 3))
    def test__rshift__(self):
        self.binary_test(lambda x, y: x >> y, (1, 2, 3))
    def test__or__(self):
        self.binary_test(lambda x, y: x | y)
    def test__and__(self):
        self.binary_test(lambda x, y: x & y)
    def test__xor__(self):
        self.binary_test(lambda x, y: x ^ y)
    def test__neg__(self):
        self.unary_test(lambda x: -x)
    def test__pos__(self):
        self.unary_test(lambda x: +x)
    def test__invert__(self):
        self.unary_test(lambda x: ~x)
    def test__pow__(self):
        self.binary_test(lambda x, y: x**y, (2, 3))
        # pow is buggy, dowsn't allow our type
        #self.binary_test(lambda x, y: pow(x, y, 42), (2, 3, 5, 1000))

    def test_back_to_int(self):
        #assert int(r_uint(-1)) == -1
        # ^^^ that looks wrong IMHO: int(x) should not by itself return
        #     an integer that has a different value than x, especially
        #     if x is a subclass of long.
        assert int(r_uint(1)) == 1

    def unary_test(self, f):
        for arg in (0, 3, 12345):
            res = f(arg) & maxint_mask
            cmp = f(r_uint(arg))
            assert res == cmp

    def binary_test(self, f, rargs = None, translated=False):
        mask = maxint_mask
        if not rargs:
            rargs = (1, 3, 55)
        # when translated merging different int types is not allowed
        if translated:
            alltypes = [(r_uint, r_uint)]
        else:
            alltypes = [(int, r_uint), (r_uint, int), (r_uint, r_uint)]
        for larg in (0, 1, 2, 3, 1234):
            for rarg in rargs:
                for types in alltypes:
                    res = f(larg, rarg)
                    left, right = types
                    cmp = f(left(larg), right(rarg))
                    if type(res) is tuple:
                        res = res[0] & mask, res[1] & mask
                    else:
                        res = res & mask
                    assert res == cmp

    def test_from_float(self):
        assert r_uint(2.3) == 2
        assert r_uint(sys.maxint * 1.234) == long(sys.maxint * 1.234)

    def test_to_float(self):
        assert float(r_uint(2)) == 2.0
        val = long(sys.maxint * 1.234)
        assert float(r_uint(val)) == float(val)

def test_mixed_types():
    types = [r_uint, r_ulonglong]
    for left in types:
        for right in types:
            x = left(3) + right(5)
            expected = max(types.index(left), types.index(right))
            assert types.index(type(x)) == expected

def test_limits():
    for cls in r_uint, r_ulonglong:
        mask = cls.MASK
        assert cls(mask) == mask
        assert cls(mask+1) == 0

    for cls in r_int, r_longlong:
        mask = cls.MASK>>1
        assert cls(mask) == mask
        assert cls(-mask-1) == -mask-1
        py.test.raises(OverflowError, "cls(mask) + 1")
        py.test.raises(OverflowError, "cls(-mask-1) - 1")

def test_intmask():
    assert intmask(1) == 1
    assert intmask(sys.maxint) == sys.maxint
    minint = -sys.maxint-1
    assert intmask(minint) == minint
    assert intmask(2*sys.maxint+1) == -1
    assert intmask(sys.maxint*2) == -2
    assert intmask(sys.maxint*2+2) == 0
    assert intmask(2*(sys.maxint*1+1)) == 0
    assert intmask(1 << (machbits-1)) == 1 << (machbits-1)
    assert intmask(sys.maxint+1) == minint
    assert intmask(minint-1) == sys.maxint
    assert intmask(r_uint(-1)) == -1
    assert intmask(r_ulonglong(-1)) == -1

def test_intmask_small():
    from rpython.rtyper.lltypesystem import rffi
    for tp in [rffi.r_signedchar, rffi.r_short, rffi.r_int,
               rffi.r_long, rffi.r_longlong]:
        x = intmask(tp(5))
        assert (type(x), x) == (int, 5)
        x = intmask(tp(-5))
        assert (type(x), x) == (int, -5)
    for tp in [rffi.r_uchar, rffi.r_ushort, rffi.r_uint,
               rffi.r_ulong, rffi.r_ulonglong]:
        x = intmask(tp(5))
        assert (type(x), x) == (int, 5)

def test_bug_creating_r_int():
    minint = -sys.maxint-1
    assert r_int(r_int(minint)) == minint

def test_ovfcheck():
    one = 1
    x = sys.maxint
    minusx = -sys.maxint
    n = -sys.maxint-1
    y = sys.maxint-1
    # sanity
    py.test.raises(AssertionError, ovfcheck, r_uint(0))

    # not overflowing
    try:
        ovfcheck(y+one)
    except OverflowError:
        assert False
    else:
        pass
    try:
        ovfcheck(minusx-one)
    except OverflowError:
        assert False
    else:
        pass
    try:
        ovfcheck(x-x)
    except OverflowError:
        assert False
    else:
        pass
    try:
        ovfcheck(n-n)
    except OverflowError:
        assert False
    else:
        pass

    # overflowing
    try:
        ovfcheck(x+one)
    except OverflowError:
        pass
    else:
        assert False
    try:
        ovfcheck(x+x)
    except OverflowError:
        pass
    else:
        assert False
    try:
        ovfcheck(n-one)
    except OverflowError:
        pass
    else:
        assert False
    try:
        ovfcheck(n-y)
    except OverflowError:
        pass
    else:
        assert False

def test_ovfcheck_float_to_int():
    assert ovfcheck_float_to_int(1.0) == 1
    assert ovfcheck_float_to_int(0.0) == 0
    assert ovfcheck_float_to_int(13.0) == 13
    assert ovfcheck_float_to_int(-1.0) == -1
    assert ovfcheck_float_to_int(-13.0) == -13

    # strange things happening for float to int on 64 bit:
    # int(float(i)) != i  because of rounding issues
    x = sys.maxint
    while int(float(x)) > sys.maxint:
        x -= 1
    assert ovfcheck_float_to_int(float(x)) == int(float(x))

    x = sys.maxint + 1
    while int(float(x)) <= sys.maxint:
        x += 1
    py.test.raises(OverflowError, ovfcheck_float_to_int, x)

    x = -sys.maxint-1
    while int(float(x)) < -sys.maxint-1:
        x += 1
    assert ovfcheck_float_to_int(float(x)) == int(float(x))

    x = -sys.maxint-1
    while int(float(x)) >= -sys.maxint-1:
        x -= 1
    py.test.raises(OverflowError, ovfcheck_float_to_int, x)


def test_abs():
    assert type(abs(r_longlong(1))) is r_longlong


def test_r_singlefloat():
    x = r_singlefloat(2.5)       # exact number
    assert float(x) == 2.5
    x = r_singlefloat(2.1)       # approximate number, bits are lost
    assert float(x) != 2.1
    assert abs(float(x) - 2.1) < 1E-6

def test_r_singlefloat_eq():
    x = r_singlefloat(2.5)       # exact number
    y = r_singlefloat(2.5)
    assert x == y
    assert not x != y
    assert not x == 2.5
    assert x != 2.5
    py.test.raises(TypeError, "x>y")

class TestRarithmetic(BaseRtypingTest):
    def test_compare_singlefloat_crashes(self):
        from rpython.rlib.rarithmetic import r_singlefloat
        from rpython.rtyper.error import MissingRTypeOperation
        def f(x):
            a = r_singlefloat(x)
            b = r_singlefloat(x+1)
            return a == b
        py.test.raises(MissingRTypeOperation, "self.interpret(f, [42.0])")

    def test_is_valid_int(self):
        def f(x):
            return (is_valid_int(x)     * 4 +
                    is_valid_int(x > 0) * 2 +
                    is_valid_int(x + 0.5))
        assert f(123) == 4 + 2
        res = self.interpret(f, [123])
        assert res == 4 + 2


def test_int_real_union():
    from rpython.rtyper.lltypesystem.rffi import r_int_real
    assert compute_restype(r_int_real, r_int_real) is r_int_real

def test_compute_restype_incompatible():
    from rpython.rtyper.lltypesystem.rffi import r_int_real, r_short, r_ushort
    testcases = [(r_uint, r_longlong), (r_int_real, r_uint),
                (r_short, r_ushort)]
    for t1, t2 in testcases:
        py.test.raises(AssertionError, compute_restype, t1, t2)
        py.test.raises(AssertionError, compute_restype, t2, t1)

def test_most_neg_value_of():
    assert most_neg_value_of_same_type(123) == -sys.maxint-1
    assert most_neg_value_of_same_type(r_uint(123)) == 0
    llmin = -(2**(r_longlong.BITS-1))
    assert most_neg_value_of_same_type(r_longlong(123)) == llmin
    assert most_neg_value_of_same_type(r_ulonglong(123)) == 0

def test_most_pos_value_of():
    assert most_pos_value_of_same_type(123) == sys.maxint
    assert most_pos_value_of_same_type(r_uint(123)) == 2 * sys.maxint + 1
    llmax_sign = (2**(r_longlong.BITS-1))-1
    llmax_unsign = (2**r_longlong.BITS)-1
    assert most_pos_value_of_same_type(r_longlong(123)) == llmax_sign
    assert most_pos_value_of_same_type(r_ulonglong(123)) == llmax_unsign

def test_is_signed_integer_type():
    from rpython.rtyper.lltypesystem import lltype, rffi
    assert is_signed_integer_type(lltype.Signed)
    assert is_signed_integer_type(rffi.SIGNEDCHAR)
    assert is_signed_integer_type(lltype.SignedLongLong)
    assert not is_signed_integer_type(lltype.Unsigned)
    assert not is_signed_integer_type(lltype.UnsignedLongLong)
    assert not is_signed_integer_type(lltype.Char)
    assert not is_signed_integer_type(lltype.UniChar)
    assert not is_signed_integer_type(lltype.Bool)

def test_r_ulonglong():
    x = r_longlong(-1)
    y = r_ulonglong(x)
    assert long(y) == 2**r_ulonglong.BITS - 1

def test_highest_bit():
    py.test.raises(AssertionError, highest_bit, 0)
    py.test.raises(AssertionError, highest_bit, 14)
    for i in xrange(31):
        assert highest_bit(2**i) == i

def test_int_between():
    assert int_between(1, 1, 3)
    assert int_between(1, 2, 3)
    assert not int_between(1, 0, 2)
    assert not int_between(1, 5, 2)
    assert not int_between(1, 2, 2)
    assert not int_between(1, 1, 1)

# these can't be prebuilt on 32bit
U1 = r_ulonglong(0x0102030405060708L)
U2 = r_ulonglong(0x0807060504030201L)
S1 = r_longlong(0x0102030405060708L)
S2 = r_longlong(0x0807060504030201L)

def test_byteswap():
    from rpython.rtyper.lltypesystem import rffi, lltype

    assert rffi.cast(lltype.Signed, byteswap(rffi.cast(rffi.USHORT, 0x0102))) == 0x0201
    assert rffi.cast(lltype.Signed, byteswap(rffi.cast(rffi.INT, 0x01020304))) == 0x04030201
    assert byteswap(U1) == U2
    assert byteswap(S1) == S2
    assert ((byteswap(2.3) - 1.903598566252326e+185) / 1e185) < 0.000001
    assert (rffi.cast(lltype.Float, byteswap(rffi.cast(lltype.SingleFloat, 2.3))) - 4.173496037651603e-08) < 1e-16

def test_byteswap_interpret():
    interpret(test_byteswap, [])


class TestStringToInt:

    def test_string_to_int(self):
        cases = [('0', 0),
                 ('1', 1),
                 ('9', 9),
                 ('10', 10),
                 ('09', 9),
                 ('0000101', 101),    # not octal unless base 0 or 8
                 ('5123', 5123),
                 (' 0', 0),
                 ('0  ', 0),
                 (' \t \n   32313  \f  \v   \r  \n\r    ', 32313),
                 ('+12', 12),
                 ('-5', -5),
                 ('- 5', -5),
                 ('+ 5', 5),
                 ('  -123456789 ', -123456789),
                 ]
        for s, expected in cases:
            assert string_to_int(s) == expected
            #assert string_to_bigint(s).tolong() == expected

    def test_string_to_int_base(self):
        cases = [('111', 2, 7),
                 ('010', 2, 2),
                 ('102', 3, 11),
                 ('103', 4, 19),
                 ('107', 8, 71),
                 ('109', 10, 109),
                 ('10A', 11, 131),
                 ('10a', 11, 131),
                 ('10f', 16, 271),
                 ('10F', 16, 271),
                 ('0x10f', 16, 271),
                 ('0x10F', 16, 271),
                 ('10z', 36, 1331),
                 ('10Z', 36, 1331),
                 ('12',   0, 12),
                 ('015',  0, 13),
                 ('0x10', 0, 16),
                 ('0XE',  0, 14),
                 ('0',    0, 0),
                 ('0b11', 2, 3),
                 ('0B10', 2, 2),
                 ('0o77', 8, 63),
                 ]
        for s, base, expected in cases:
            assert string_to_int(s, base) == expected
            assert string_to_int('+'+s, base) == expected
            assert string_to_int('-'+s, base) == -expected
            assert string_to_int(s+'\n', base) == expected
            assert string_to_int('  +'+s, base) == expected
            assert string_to_int('-'+s+'  ', base) == -expected

    def test_string_to_int_error(self):
        cases = ['0x123',    # must use base 0 or 16
                 ' 0X12 ',
                 '0b01',
                 '0o01',
                 '',
                 '++12',
                 '+-12',
                 '-+12',
                 '--12',
                 '12a6',
                 '12A6',
                 'f',
                 'Z',
                 '.',
                 '@',
                 ]
        for s in cases:
            py.test.raises(ParseStringError, string_to_int, s)
            py.test.raises(ParseStringError, string_to_int, '  '+s)
            py.test.raises(ParseStringError, string_to_int, s+'  ')
            py.test.raises(ParseStringError, string_to_int, '+'+s)
            py.test.raises(ParseStringError, string_to_int, '-'+s)
        py.test.raises(ParseStringError, string_to_int, '0x', 16)
        py.test.raises(ParseStringError, string_to_int, '-0x', 16)

        exc = py.test.raises(ParseStringError, string_to_int, '')
        assert exc.value.msg == "invalid literal for int() with base 10"
        exc = py.test.raises(ParseStringError, string_to_int, '', 0)
        assert exc.value.msg == "invalid literal for int() with base 0"

    def test_string_to_int_overflow(self):
        import sys
        py.test.raises(ParseStringOverflowError, string_to_int,
               str(sys.maxint*17))

    def test_string_to_int_not_overflow(self):
        import sys
        for x in [-sys.maxint-1, sys.maxint]:
            y = string_to_int(str(x))
            assert y == x

    def test_string_to_int_base_error(self):
        cases = [('1', 1),
                 ('1', 37),
                 ('a', 0),
                 ('9', 9),
                 ('0x123', 7),
                 ('145cdf', 15),
                 ('12', 37),
                 ('12', 98172),
                 ('12', -1),
                 ('12', -908),
                 ('12.3', 10),
                 ('12.3', 13),
                 ('12.3', 16),
                 ]
        for s, base in cases:
            py.test.raises(ParseStringError, string_to_int, s, base)
            py.test.raises(ParseStringError, string_to_int, '  '+s, base)
            py.test.raises(ParseStringError, string_to_int, s+'  ', base)
            py.test.raises(ParseStringError, string_to_int, '+'+s, base)
            py.test.raises(ParseStringError, string_to_int, '-'+s, base)
