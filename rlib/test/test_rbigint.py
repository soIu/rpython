from __future__ import division

import operator
import sys
import math
from random import random, randint, sample, seed

import py

from rpython.rlib import rbigint as lobj
from rpython.rlib.rarithmetic import r_uint, r_longlong, r_ulonglong, intmask
from rpython.rlib.rbigint import (rbigint, SHIFT, MASK, KARATSUBA_CUTOFF,
    _store_digit, _mask_digit, InvalidEndiannessError, InvalidSignednessError)
from rpython.rlib.rfloat import NAN
from rpython.rtyper.test.test_llinterp import interpret
from rpython.translator.c.test.test_standalone import StandaloneTests

long_vals_not_too_big = range(17) + [
        37, 50,
        127, 128, 129, 511, 512, 513, sys.maxint, sys.maxint + 1,
        123456789123456789000000L,
        ]

long_vals = long_vals_not_too_big + [
        1 << 100, 3 ** 10000]

class TestRLong(object):
    def test_simple(self):
        for op1 in [-2, -1, 0, 1, 2, 50]:
            for op2 in [-2, -1, 0, 1, 2, 50]:
                rl_op1 = rbigint.fromint(op1)
                rl_op2 = rbigint.fromint(op2)
                for op in "add sub mul".split():
                    r1 = getattr(rl_op1, op)(rl_op2)
                    r2 = getattr(operator, op)(op1, op2)
                    print op, op1, op2
                    assert r1.tolong() == r2

    def test_frombool(self):
        assert rbigint.frombool(False).tolong() == 0
        assert rbigint.frombool(True).tolong() == 1

    def test_str(self):
        n = 1
        r1 = rbigint.fromint(1)
        three = rbigint.fromint(3)
        for i in range(300):
            n *= 3
            r1 = r1.mul(three)
            assert r1.str() == str(n)
            r2 = r1.neg()
            assert r2.str() == str(-n)

    def test_floordiv(self):
        for op1 in gen_signs(long_vals):
            for op2 in gen_signs(long_vals):
                if not op2:
                    continue
                rl_op1 = rbigint.fromlong(op1)
                rl_op2 = rbigint.fromlong(op2)
                r1 = rl_op1.floordiv(rl_op2)
                r2 = op1 // op2
                assert r1.tolong() == r2

    def test_truediv(self):
        for op1 in gen_signs(long_vals_not_too_big):
            for op2 in gen_signs(long_vals):
                if not op2:
                    continue
                rl_op1 = rbigint.fromlong(op1)
                rl_op2 = rbigint.fromlong(op2)
                r1 = rl_op1.truediv(rl_op2)
                r2 = op1 / op2
                assert r1 == r2

    def test_truediv_precision(self):
        op1 = rbigint.fromlong(12345*2**30)
        op2 = rbigint.fromlong(98765*7**81)
        f = op1.truediv(op2)
        assert f == 4.7298422347492634e-61      # exactly

    def test_truediv_overflow(self):
        overflowing = 2**1024 - 2**(1024-53-1)
        op1 = rbigint.fromlong(overflowing-1)
        op2 = rbigint.fromlong(1)
        f = op1.truediv(op2)
        assert f == 1.7976931348623157e+308     # exactly

        op1 = rbigint.fromlong(overflowing-1)
        op2 = rbigint.fromlong(-1)
        f = op1.truediv(op2)
        assert f == -1.7976931348623157e+308    # exactly

        op1 = rbigint.fromlong(-overflowing+1)
        op2 = rbigint.fromlong(-1)
        f = op1.truediv(op2)
        assert f == +1.7976931348623157e+308    # exactly

        op1 = rbigint.fromlong(overflowing)
        op2 = rbigint.fromlong(1)
        py.test.raises(OverflowError, op1.truediv, op2)

    def test_truediv_overflow2(self):
        overflowing = 2**1024 - 2**(1024-53-1)
        op1 = rbigint.fromlong(2*overflowing - 10)
        op2 = rbigint.fromlong(2)
        f = op1.truediv(op2)
        assert f == 1.7976931348623157e+308    # exactly
        op2 = rbigint.fromlong(-2)
        f = op1.truediv(op2)
        assert f == -1.7976931348623157e+308   # exactly

    def test_mod(self):
        for op1 in gen_signs(long_vals):
            for op2 in gen_signs(long_vals):
                if not op2:
                    continue
                rl_op1 = rbigint.fromlong(op1)
                rl_op2 = rbigint.fromlong(op2)
                r1 = rl_op1.mod(rl_op2)
                r2 = op1 % op2
                print op1, op2
                assert r1.tolong() == r2

    def test_int_mod(self):
        for x in gen_signs(long_vals):
            for y in gen_signs([1, 2, 4, 8, 8888, sys.maxint, 2 ** 19, 2 ** 18 - 1]):
                op1 = rbigint.fromlong(x)
                r1 = op1.int_mod(y)
                r2 = x % y
                assert r1.tolong() == r2

    def test_pow(self):
        for op1 in gen_signs(long_vals_not_too_big):
            for op2 in [0, 1, 2, 8, 9, 10, 11]:
                rl_op1 = rbigint.fromlong(op1)
                rl_op2 = rbigint.fromint(op2)
                r1 = rl_op1.pow(rl_op2)
                r2 = op1 ** op2
                assert r1.tolong() == r2

    def test_touint(self):
        result = r_uint(sys.maxint + 42)
        rl = rbigint.fromint(sys.maxint).add(rbigint.fromint(42))
        assert rl.touint() == result

    def test_eq_ne_operators(self):
        a1 = rbigint.fromint(12)
        a2 = rbigint.fromint(12)
        a3 = rbigint.fromint(123)

        assert a1 == a2
        assert a1 != a3
        assert not (a1 != a2)
        assert not (a1 == a3)


def gen_signs(l):
    for s in l:
        if s == 0:
            yield s
        else:
            yield s
            yield -s

def bigint(lst, sign):
    for digit in lst:
        assert digit & MASK == digit    # wrongly written test!
    return rbigint(map(_store_digit, map(_mask_digit, lst)), sign)


class Test_rbigint(object):

    def test_args_from_long(self):
        BASE = 1 << SHIFT
        assert rbigint.fromlong(0).eq(bigint([0], 0))
        assert rbigint.fromlong(17).eq(bigint([17], 1))
        assert rbigint.fromlong(BASE-1).eq(bigint([intmask(BASE-1)], 1))
        assert rbigint.fromlong(BASE).eq(bigint([0, 1], 1))
        assert rbigint.fromlong(BASE**2).eq(bigint([0, 0, 1], 1))
        assert rbigint.fromlong(-17).eq(bigint([17], -1))
        assert rbigint.fromlong(-(BASE-1)).eq(bigint([intmask(BASE-1)], -1))
        assert rbigint.fromlong(-BASE).eq(bigint([0, 1], -1))
        assert rbigint.fromlong(-(BASE**2)).eq(bigint([0, 0, 1], -1))
#        assert rbigint.fromlong(-sys.maxint-1).eq(
#            rbigint.digits_for_most_neg_long(-sys.maxint-1), -1)

    def test_args_from_int(self):
        BASE = 1 << 31 # Can't can't shift here. Shift might be from longlonglong
        MAX = int(BASE-1)
        assert rbigint.fromrarith_int(0).eq(bigint([0], 0))
        assert rbigint.fromrarith_int(17).eq(bigint([17], 1))
        assert rbigint.fromrarith_int(MAX).eq(bigint([MAX], 1))
        # No longer true.
        """assert rbigint.fromrarith_int(r_longlong(BASE)).eq(bigint([0, 1], 1))
        assert rbigint.fromrarith_int(r_longlong(BASE**2)).eq(
            bigint([0, 0, 1], 1))"""
        assert rbigint.fromrarith_int(-17).eq(bigint([17], -1))
        assert rbigint.fromrarith_int(-MAX).eq(bigint([MAX], -1))
        """assert rbigint.fromrarith_int(-MAX-1).eq(bigint([0, 1], -1))
        assert rbigint.fromrarith_int(r_longlong(-(BASE**2))).eq(
            bigint([0, 0, 1], -1))"""
#        assert rbigint.fromrarith_int(-sys.maxint-1).eq((
#            rbigint.digits_for_most_neg_long(-sys.maxint-1), -1)

    def test_args_from_uint(self):
        BASE = 1 << SHIFT
        assert rbigint.fromrarith_int(r_uint(0)).eq(bigint([0], 0))
        assert rbigint.fromrarith_int(r_uint(17)).eq(bigint([17], 1))
        assert rbigint.fromrarith_int(r_uint(BASE-1)).eq(bigint([intmask(BASE-1)], 1))
        assert rbigint.fromrarith_int(r_uint(BASE)).eq(bigint([0, 1], 1))
        #assert rbigint.fromrarith_int(r_uint(BASE**2)).eq(bigint([0], 0))
        assert rbigint.fromrarith_int(r_uint(sys.maxint)).eq(
            rbigint.fromint(sys.maxint))
        assert rbigint.fromrarith_int(r_uint(sys.maxint+1)).eq(
            rbigint.fromlong(sys.maxint+1))
        assert rbigint.fromrarith_int(r_uint(2*sys.maxint+1)).eq(
            rbigint.fromlong(2*sys.maxint+1))

    def test_fromdecimalstr(self):
        x = rbigint.fromdecimalstr("12345678901234567890523897987")
        assert x.tolong() == 12345678901234567890523897987L
        assert x.tobool() is True
        x = rbigint.fromdecimalstr("+12345678901234567890523897987")
        assert x.tolong() == 12345678901234567890523897987L
        assert x.tobool() is True
        x = rbigint.fromdecimalstr("-12345678901234567890523897987")
        assert x.tolong() == -12345678901234567890523897987L
        assert x.tobool() is True
        x = rbigint.fromdecimalstr("+0")
        assert x.tolong() == 0
        assert x.tobool() is False
        x = rbigint.fromdecimalstr("-0")
        assert x.tolong() == 0
        assert x.tobool() is False

    def test_fromstr(self):
        from rpython.rlib.rstring import ParseStringError
        assert rbigint.fromstr('123L').tolong() == 123
        assert rbigint.fromstr('123L  ').tolong() == 123
        py.test.raises(ParseStringError, rbigint.fromstr, 'L')
        py.test.raises(ParseStringError, rbigint.fromstr, 'L  ')
        assert rbigint.fromstr('123L', 4).tolong() == 27
        assert rbigint.fromstr('123L', 30).tolong() == 27000 + 1800 + 90 + 21
        assert rbigint.fromstr('123L', 22).tolong() == 10648 + 968 + 66 + 21
        assert rbigint.fromstr('123L', 21).tolong() == 441 + 42 + 3
        assert rbigint.fromstr('1891234174197319').tolong() == 1891234174197319

    def test_from_numberstring_parser(self):
        from rpython.rlib.rstring import NumberStringParser
        parser = NumberStringParser("1231231241", "1231231241", 10, "long")
        assert rbigint._from_numberstring_parser(parser).tolong() == 1231231241

    def test_add(self):
        x = 123456789123456789000000L
        y = 123858582373821923936744221L
        for i in [-1, 1]:
            for j in [-1, 1]:
                f1 = rbigint.fromlong(x * i)
                f2 = rbigint.fromlong(y * j)
                result = f1.add(f2)
                assert result.tolong() == x * i + y * j

    def test_int_add(self):
        for x in gen_signs(long_vals):
            for y in gen_signs([0, 1, 9999, sys.maxint, 2 ** 19, 2 ** 18 - 1]):
                f1 = rbigint.fromlong(x)
                result = f1.int_add(y)
                assert result.tolong() == x + y

    def test_sub(self):
        x = 12378959520302182384345L
        y = 88961284756491823819191823L
        for i in [-1, 1]:
            for j in [-1, 1]:
                f1 = rbigint.fromlong(x * i)
                f2 = rbigint.fromlong(y * j)
                result = f1.sub(f2)
                assert result.tolong() == x * i - y * j

    def test_int_sub(self):
        for x in gen_signs([0, 123456789123456789000000L, 1 << 100, 3 ** 10000]):
            for y in gen_signs([0, 1, 8888, sys.maxint, 2 ** 19, 2 ** 18 - 1]):
                f1 = rbigint.fromlong(x)
                result = f1.int_sub(y)
                assert result.tolong() == x - y

    def test_subzz(self):
        w_l0 = rbigint.fromint(0)
        assert w_l0.sub(w_l0).tolong() == 0

    def test_mul(self):
        for x in gen_signs(long_vals):
            f1 = rbigint.fromlong(x)
            for y in gen_signs(long_vals_not_too_big):
                f2 = rbigint.fromlong(y)
                result = f1.mul(f2)
                assert result.tolong() == x * y
            # there's a special case for a is b
            result = f1.mul(f1)
            assert result.tolong() == x * x

    def test_int_mul(self):
        for x in gen_signs([39, 128, 111111111, 123456789123456789000000L, 1 << 100, 3 ** 10000]):
            for y in gen_signs([0, 1, 8888, sys.maxint, 2 ** 19, 2 ** 18 - 1]):
                f1 = rbigint.fromlong(x)
                result = f1.int_mul(y)
                assert result.tolong() == x * y

    def test_tofloat(self):
        x = 12345678901234567890L ** 10
        f1 = rbigint.fromlong(x)
        d = f1.tofloat()
        assert d == float(x)
        x = x ** 100
        f1 = rbigint.fromlong(x)
        assert py.test.raises(OverflowError, f1.tofloat)
        f2 = rbigint.fromlong(2097152 << SHIFT)
        d = f2.tofloat()
        assert d == float(2097152 << SHIFT)

    def test_tofloat_precision(self):
        assert rbigint.fromlong(0).tofloat() == 0.0
        for sign in [1, -1]:
            for p in xrange(100):
                x = long(2**p * (2**53 + 1) + 1) * sign
                y = long(2**p * (2**53+ 2)) * sign
                rx = rbigint.fromlong(x)
                rxf = rx.tofloat()
                assert rxf == float(y)
                assert rbigint.fromfloat(rxf).tolong() == y
                #
                x = long(2**p * (2**53 + 1)) * sign
                y = long(2**p * 2**53) * sign
                rx = rbigint.fromlong(x)
                rxf = rx.tofloat()
                assert rxf == float(y)
                assert rbigint.fromfloat(rxf).tolong() == y

    def test_fromfloat(self):
        x = 1234567890.1234567890
        f1 = rbigint.fromfloat(x)
        y = f1.tofloat()
        assert f1.tolong() == long(x)
        # check overflow
        #x = 12345.6789e10000000000000000000000000000
        # XXX don't use such consts. marshal doesn't handle them right.
        x = 12345.6789e200
        x *= x
        assert py.test.raises(OverflowError, rbigint.fromfloat, x)
        assert py.test.raises(ValueError, rbigint.fromfloat, NAN)
        #
        f1 = rbigint.fromfloat(9007199254740991.0)
        assert f1.tolong() == 9007199254740991

        null = rbigint.fromfloat(-0.0)
        assert null.int_eq(0)

    def test_eq(self):
        x = 5858393919192332223L
        y = 585839391919233111223311112332L
        f1 = rbigint.fromlong(x)
        f2 = rbigint.fromlong(-x)
        f3 = rbigint.fromlong(y)
        assert f1.eq(f1)
        assert f2.eq(f2)
        assert f3.eq(f3)
        assert not f1.eq(f2)
        assert not f1.eq(f3)

    def test_eq_fastpath(self):
        x = 1234
        y = 1234
        f1 = rbigint.fromint(x)
        f2 = rbigint.fromint(y)
        assert f1.eq(f2)

    def test_lt(self):
        val = [0, 0x111111111111, 0x111111111112, 0x111111111112FFFF]
        for x in gen_signs(val):
            for y in gen_signs(val):
                f1 = rbigint.fromlong(x)
                f2 = rbigint.fromlong(y)
                assert (x < y) ==  f1.lt(f2)

    def test_int_comparison(self):
        for x in gen_signs(long_vals):
            for y in gen_signs([0, 1, 0x11111111, 0x11111112, 8888, sys.maxint, 2 ** 19, 2 ** 18 - 1]):
                f1 = rbigint.fromlong(x)
                assert (x < y) ==  f1.int_lt(y)
                assert (x <= y) ==  f1.int_le(y)
                assert (x > y) ==  f1.int_gt(y)
                assert (x >= y) ==  f1.int_ge(y)
                assert (x == y) ==  f1.int_eq(y)
                assert (x != y) ==  f1.int_ne(y)

    def test_order(self):
        f6 = rbigint.fromint(6)
        f7 = rbigint.fromint(7)
        assert (f6.lt(f6), f6.lt(f7), f7.lt(f6)) == (0,1,0)
        assert (f6.le(f6), f6.le(f7), f7.le(f6)) == (1,1,0)
        assert (f6.gt(f6), f6.gt(f7), f7.gt(f6)) == (0,0,1)
        assert (f6.ge(f6), f6.ge(f7), f7.ge(f6)) == (1,0,1)

    def test_int_order(self):
        f6 = rbigint.fromint(6)
        f7 = rbigint.fromint(7)
        assert (f6.int_lt(6), f6.int_lt(7), f7.int_lt(6)) == (0,1,0)
        assert (f6.int_le(6), f6.int_le(7), f7.int_le(6)) == (1,1,0)
        assert (f6.int_gt(6), f6.int_gt(7), f7.int_gt(6)) == (0,0,1)
        assert (f6.int_ge(6), f6.int_ge(7), f7.int_ge(6)) == (1,0,1)

    def test_int_conversion(self):
        f1 = rbigint.fromlong(12332)
        f2 = rbigint.fromint(12332)
        assert f2.tolong() == f1.tolong()
        assert f2.toint()
        assert rbigint.fromlong(42).tolong() == 42
        assert rbigint.fromlong(-42).tolong() == -42

        u = f2.touint()
        assert u == 12332
        assert type(u) is r_uint

    def test_conversions(self):
        for v in (0, 1, -1, sys.maxint, -sys.maxint-1):
            assert rbigint.fromlong(long(v)).tolong() == long(v)
            l = rbigint.fromint(v)
            assert l.toint() == v
            if v >= 0:
                u = l.touint()
                assert u == v
                assert type(u) is r_uint
            else:
                py.test.raises(ValueError, l.touint)

        toobig_lv1 = rbigint.fromlong(sys.maxint+1)
        assert toobig_lv1.tolong() == sys.maxint+1
        toobig_lv2 = rbigint.fromlong(sys.maxint+2)
        assert toobig_lv2.tolong() == sys.maxint+2
        toobig_lv3 = rbigint.fromlong(-sys.maxint-2)
        assert toobig_lv3.tolong() == -sys.maxint-2

        for lv in (toobig_lv1, toobig_lv2, toobig_lv3):
            py.test.raises(OverflowError, lv.toint)

        lmaxuint = rbigint.fromlong(2*sys.maxint+1)
        toobig_lv4 = rbigint.fromlong(2*sys.maxint+2)

        u = lmaxuint.touint()
        assert u == 2*sys.maxint+1

        py.test.raises(ValueError, toobig_lv3.touint)
        py.test.raises(OverflowError, toobig_lv4.touint)


    def test_pow_lll(self):
        x = 10L
        y = 2L
        z = 13L
        f1 = rbigint.fromlong(x)
        f2 = rbigint.fromlong(y)
        f3 = rbigint.fromlong(z)
        v = f1.pow(f2, f3)
        assert v.tolong() == pow(x, y, z)
        f3n = f3.neg()
        v = f1.pow(f2, f3n)
        assert v.tolong() == pow(x, y, -z)
        #
        f1, f2, f3 = [rbigint.fromlong(i)
                      for i in (10L, -1L, 42L)]
        py.test.raises(TypeError, f1.pow, f2, f3)
        f1, f2, f3 = [rbigint.fromlong(i)
                      for i in (10L, 5L, 0L)]
        py.test.raises(ValueError, f1.pow, f2, f3)
        #
        MAX = 1E20
        x = long(random() * MAX) + 1
        y = long(random() * MAX) + 1
        z = long(random() * MAX) + 1
        f1 = rbigint.fromlong(x)
        f2 = rbigint.fromlong(y)
        f3 = rbigint.fromlong(z)
        print f1
        print f2
        print f3
        v = f1.pow(f2, f3)
        print '--->', v
        assert v.tolong() == pow(x, y, z)

    def test_pow_lll_bug(self):
        two = rbigint.fromint(2)
        t = rbigint.fromlong(2655689964083835493447941032762343136647965588635159615997220691002017799304)
        for n, expected in [(37, 9), (1291, 931), (67889, 39464)]:
            v = two.pow(t, rbigint.fromint(n))
            assert v.toint() == expected
        #
        # more tests, comparing against CPython's answer
        enabled = sample(range(5*32), 10)
        for i in range(5*32):
            t = t.mul(two)      # add one random bit
            if random() >= 0.5:
                t = t.add(rbigint.fromint(1))
            if i not in enabled:
                continue    # don't take forever
            n = randint(1, sys.maxint)
            v = two.pow(t, rbigint.fromint(n))
            assert v.toint() == pow(2, t.tolong(), n)

    def test_pow_lll_bug2(self):
        x = rbigint.fromlong(2)
        y = rbigint.fromlong(5100894665148900058249470019412564146962964987365857466751243988156579407594163282788332839328303748028644825680244165072186950517295679131100799612871613064597)
        z = rbigint.fromlong(538564)
        expected = rbigint.fromlong(163464)
        got = x.pow(y, z)
        assert got.eq(expected)

    def test_pow_lln(self):
        x = 10L
        y = 2L
        f1 = rbigint.fromlong(x)
        f2 = rbigint.fromlong(y)
        v = f1.pow(f2)
        assert v.tolong() == x ** y

    def test_normalize(self):
        f1 = bigint([1, 0], 1)
        f1._normalize()
        assert f1.size == 1
        f0 = bigint([0], 0)
        assert f1.sub(f1).eq(f0)

    def test_invert(self):
        x = 3 ** 40
        f1 = rbigint.fromlong(x)
        f2 = rbigint.fromlong(-x)
        r1 = f1.invert()
        r2 = f2.invert()
        assert r1.tolong() == -(x + 1)
        assert r2.tolong() == -(-x + 1)

    def test_shift(self):
        negative = -23
        masks_list = [int((1 << i) - 1) for i in range(1, r_uint.BITS-1)]
        for x in gen_signs([3L ** 30L, 5L ** 20L, 7 ** 300, 0L, 1L]):
            f1 = rbigint.fromlong(x)
            py.test.raises(ValueError, f1.lshift, negative)
            py.test.raises(ValueError, f1.rshift, negative)
            for y in [0L, 1L, 32L, 2304L, 11233L, 3 ** 9]:
                res1 = f1.lshift(int(y)).tolong()
                res2 = f1.rshift(int(y)).tolong()
                assert res1 == x << y
                assert res2 == x >> y
                for mask in masks_list:
                    res3 = f1.abs_rshift_and_mask(r_ulonglong(y), mask)
                    assert res3 == (abs(x) >> y) & mask

    def test_from_list_n_bits(self):
        for x in ([3L ** 30L, 5L ** 20L, 7 ** 300] +
                  [1L << i for i in range(130)] +
                  [(1L << i) - 1L for i in range(130)]):
            for nbits in range(1, SHIFT+1):
                mask = (1 << nbits) - 1
                lst = []
                got = x
                while got > 0:
                    lst.append(int(got & mask))
                    got >>= nbits
                f1 = rbigint.from_list_n_bits(lst, nbits)
                assert f1.tolong() == x

    def test_bitwise(self):
        for x in gen_signs([0, 1, 5, 11, 42, 43, 3 ** 30]):
            for y in gen_signs([0, 1, 5, 11, 42, 43, 3 ** 30, 3 ** 31]):
                lx = rbigint.fromlong(x)
                ly = rbigint.fromlong(y)
                for mod in "xor and_ or_".split():
                    res1 = getattr(lx, mod)(ly).tolong()
                    res2 = getattr(operator, mod)(x, y)
                    assert res1 == res2

    def test_int_bitwise(self):
        for x in gen_signs([0, 1, 5, 11, 42, 43, 2 ** 30]):
            for y in gen_signs([0, 1, 5, 11, 42, 43, 3 ** 30, 2 ** 31]):
                if y != intmask(y):
                    continue      # skip 'y' too large for 32-bit
                lx = rbigint.fromlong(x)
                for mod in "xor and_ or_".split():
                    res1 = getattr(lx, 'int_' + mod)(y).tolong()
                    res2 = getattr(operator, mod)(x, y)
                    assert res1 == res2

    def test_mul_eq_shift(self):
        p2 = rbigint.fromlong(1).lshift(63)
        f1 = rbigint.fromlong(0).lshift(63)
        f2 = rbigint.fromlong(0).mul(p2)
        assert f1.eq(f2)

    def test_tostring(self):
        z = rbigint.fromlong(0)
        assert z.str() == '0'
        assert z.repr() == '0L'
        assert z.hex() == '0x0L'
        assert z.oct() == '0L'
        x = rbigint.fromlong(-18471379832321)
        assert x.str() == '-18471379832321'
        assert x.repr() == '-18471379832321L'
        assert x.hex() == '-0x10ccb4088e01L'
        assert x.oct() == '-0414626402107001L'
        assert x.format('.!') == (
            '-!....!!..!!..!.!!.!......!...!...!!!........!')
        assert x.format('abcdefghijkl', '<<', '>>') == '-<<cakdkgdijffjf>>'
        x = rbigint.fromlong(-18471379832321000000000000000000000000000000000000000000)
        assert x.str() == '-18471379832321000000000000000000000000000000000000000000'
        assert x.repr() == '-18471379832321000000000000000000000000000000000000000000L'
        assert x.hex() == '-0xc0d9a6f41fbcf1718b618443d45516a051e40000000000L'
        assert x.oct() == '-014033151572037571705614266060420752125055201217100000000000000L'

    def test_format_caching(self):
        big = rbigint.fromlong(2 ** 1000)
        res1 = big.str()
        oldpow = rbigint.__dict__['pow']
        rbigint.pow = None
        # make sure pow is not used the second time
        try:
            res2 = big.str()
            assert res2 == res1
        finally:
            rbigint.pow = oldpow

    def test_overzelous_assertion(self):
        a = rbigint.fromlong(-1<<10000)
        b = rbigint.fromlong(-1<<3000)
        assert a.mul(b).tolong() == (-1<<10000)*(-1<<3000)

    def test_bit_length(self):
        assert rbigint.fromlong(0).bit_length() == 0
        assert rbigint.fromlong(1).bit_length() == 1
        assert rbigint.fromlong(2).bit_length() == 2
        assert rbigint.fromlong(3).bit_length() == 2
        assert rbigint.fromlong(4).bit_length() == 3
        assert rbigint.fromlong(-3).bit_length() == 2
        assert rbigint.fromlong(-4).bit_length() == 3
        assert rbigint.fromlong(1<<40).bit_length() == 41

    def test_hash(self):
        for i in [0, 1, 2, 3, 4, 5, 6, 7, 8, 9,
                  sys.maxint-3, sys.maxint-2, sys.maxint-1, sys.maxint,
                  ] + [randint(0, sys.maxint) for _ in range(100)]:
            # hash of machine-sized integers
            assert rbigint.fromint(i).hash() == i
            # hash of negative machine-sized integers
            assert rbigint.fromint(-i-1).hash() == -i-1
        #
        for i in range(200):
            # hash of large integers: should be equal to the hash of the
            # integer reduced modulo 2**64-1, to make decimal.py happy
            x = randint(0, sys.maxint**5)
            y = x % (2**64-1)
            assert rbigint.fromlong(x).hash() == rbigint.fromlong(y).hash()
            assert rbigint.fromlong(-x).hash() == rbigint.fromlong(-y).hash()

    def test_log(self):
        from rpython.rlib.rfloat import ulps_check
        for op in long_vals:
            if not op:
                continue
            for base in [0, 2, 4, 8, 16, 10, math.e]:
                l = rbigint.fromlong(op).log(base)
                if base:
                    assert ulps_check(l, math.log(op, base)) is None
                else:
                    assert ulps_check(l, math.log(op)) is None

class TestInternalFunctions(object):
    def test__inplace_divrem1(self):
        # signs are not handled in the helpers!
        for x, y in [(1238585838347L, 3), (1234123412311231L, 1231231), (99, 100)]:
            if y > MASK:
                continue
            f1 = rbigint.fromlong(x)
            f2 = y
            remainder = lobj._inplace_divrem1(f1, f1, f2)
            assert (f1.tolong(), remainder) == divmod(x, y)
        out = bigint([99, 99], 1)
        remainder = lobj._inplace_divrem1(out, out, 100)

    def test__divrem1(self):
        # signs are not handled in the helpers!
        x = 1238585838347L
        y = 3
        f1 = rbigint.fromlong(x)
        f2 = y
        div, rem = lobj._divrem1(f1, f2)
        assert (div.tolong(), rem) == divmod(x, y)

    def test__muladd1(self):
        x = 1238585838347L
        y = 3
        z = 42
        f1 = rbigint.fromlong(x)
        f2 = y
        f3 = z
        prod = lobj._muladd1(f1, f2, f3)
        assert prod.tolong() == x * y + z

    def test__x_divrem(self):
        x = 12345678901234567890L
        for i in range(100):
            y = long(randint(1, 1 << 60))
            y <<= 60
            y += randint(1, 1 << 60)
            if y > x:
                x <<= 100
                
            f1 = rbigint.fromlong(x)
            f2 = rbigint.fromlong(y)
            div, rem = lobj._x_divrem(f1, f2)
            _div, _rem = divmod(x, y)
            assert div.tolong() == _div
            assert rem.tolong() == _rem

    def test__x_divrem2(self):
        Rx = 1 << 130
        Rx2 = 1 << 150
        Ry = 1 << 127
        Ry2 = 1<< 150
        for i in range(10):
            x = long(randint(Rx, Rx2))
            y = long(randint(Ry, Ry2))
            f1 = rbigint.fromlong(x)
            f2 = rbigint.fromlong(y)
            div, rem = lobj._x_divrem(f1, f2)
            _div, _rem = divmod(x, y)
            assert div.tolong() == _div
            assert rem.tolong() == _rem
            
    def test_divmod(self):
        x = 12345678901234567890L
        for i in range(100):
            y = long(randint(0, 1 << 60))
            y <<= 60
            y += randint(0, 1 << 60)
            for sx, sy in (1, 1), (1, -1), (-1, -1), (-1, 1):
                sx *= x
                sy *= y
                f1 = rbigint.fromlong(sx)
                f2 = rbigint.fromlong(sy)
                div, rem = f1.divmod(f2)
                _div, _rem = divmod(sx, sy)
                assert div.tolong() == _div
                assert rem.tolong() == _rem

    # testing Karatsuba stuff
    def test__v_iadd(self):
        f1 = bigint([lobj.MASK] * 10, 1)
        f2 = bigint([1], 1)
        carry = lobj._v_iadd(f1, 1, len(f1._digits)-1, f2, 1)
        assert carry == 1
        assert f1.tolong() == lobj.MASK

    def test__v_isub(self):
        f1 = bigint([lobj.MASK] + [0] * 9 + [1], 1)
        f2 = bigint([1], 1)
        borrow = lobj._v_isub(f1, 1, len(f1._digits)-1, f2, 1)
        assert borrow == 0
        assert f1.tolong() == (1 << lobj.SHIFT) ** 10 - 1

    def test__kmul_split(self):
        split = 5
        diglo = [0] * split
        dighi = [lobj.MASK] * split
        f1 = bigint(diglo + dighi, 1)
        hi, lo = lobj._kmul_split(f1, split)
        assert lo._digits == [_store_digit(0)]
        assert hi._digits == map(_store_digit, dighi)

    def test__k_mul(self):
        digs = KARATSUBA_CUTOFF * 5
        f1 = bigint([lobj.MASK] * digs, 1)
        f2 = lobj._x_add(f1, bigint([1], 1))
        ret = lobj._k_mul(f1, f2)
        assert ret.tolong() == f1.tolong() * f2.tolong()

    def test__k_lopsided_mul(self):
        digs_a = KARATSUBA_CUTOFF + 3
        digs_b = 3 * digs_a
        f1 = bigint([lobj.MASK] * digs_a, 1)
        f2 = bigint([lobj.MASK] * digs_b, 1)
        ret = lobj._k_lopsided_mul(f1, f2)
        assert ret.tolong() == f1.tolong() * f2.tolong()

    def test_longlong(self):
        max = 1L << (r_longlong.BITS-1)
        f1 = rbigint.fromlong(max-1)    # fits in r_longlong
        f2 = rbigint.fromlong(-max)     # fits in r_longlong
        f3 = rbigint.fromlong(max)      # overflows
        f4 = rbigint.fromlong(-max-1)   # overflows
        assert f1.tolonglong() == max-1
        assert f2.tolonglong() == -max
        py.test.raises(OverflowError, f3.tolonglong)
        py.test.raises(OverflowError, f4.tolonglong)

    def test_uintmask(self):
        assert rbigint.fromint(-1).uintmask() == r_uint(-1)
        assert rbigint.fromint(0).uintmask() == r_uint(0)
        assert (rbigint.fromint(sys.maxint).uintmask() ==
                r_uint(sys.maxint))
        assert (rbigint.fromlong(sys.maxint+1).uintmask() ==
                r_uint(-sys.maxint-1))

    def test_ulonglongmask(self):
        assert rbigint.fromlong(-1).ulonglongmask() == r_ulonglong(-1)
        assert rbigint.fromlong(0).ulonglongmask() == r_ulonglong(0)
        assert (rbigint.fromlong(sys.maxint).ulonglongmask() ==
                r_ulonglong(sys.maxint))
        assert (rbigint.fromlong(9**50).ulonglongmask() ==
                r_ulonglong(9**50))
        assert (rbigint.fromlong(-9**50).ulonglongmask() ==
                r_ulonglong(-9**50))

    def test_parse_digit_string(self):
        from rpython.rlib.rbigint import parse_digit_string
        class Parser:
            def __init__(self, base, sign, digits):
                self.base = base
                self.sign = sign
                self.next_digit = iter(digits + [-1]).next
        x = parse_digit_string(Parser(10, 1, [6]))
        assert x.eq(rbigint.fromint(6))
        x = parse_digit_string(Parser(10, 1, [6, 2, 3]))
        assert x.eq(rbigint.fromint(623))
        x = parse_digit_string(Parser(10, -1, [6, 2, 3]))
        assert x.eq(rbigint.fromint(-623))
        x = parse_digit_string(Parser(16, 1, [0xA, 0x4, 0xF]))
        assert x.eq(rbigint.fromint(0xA4F))
        num = 0
        for i in range(36):
            x = parse_digit_string(Parser(36, 1, range(i)))
            assert x.eq(rbigint.fromlong(num))
            num = num * 36 + i
        x = parse_digit_string(Parser(16, -1, range(15,-1,-1)*99))
        assert x.eq(rbigint.fromlong(long('-0x' + 'FEDCBA9876543210'*99, 16)))
        assert x.tobool() is True
        x = parse_digit_string(Parser(7, 1, [0, 0, 0]))
        assert x.tobool() is False
        x = parse_digit_string(Parser(7, -1, [0, 0, 0]))
        assert x.tobool() is False


BASE = 2 ** SHIFT

class TestTranslatable(object):
    def test_square(self):
        def test():
            xlo = rbigint.fromint(1410065408)
            xhi = rbigint.fromint(4)
            x = xlo.or_(xhi.lshift(31))
            y = x.mul(x)
            return y.str()
        res = interpret(test, [])
        assert "".join(res.chars) == test()

    def test_add(self):
        x = rbigint.fromint(-2147483647)
        y = rbigint.fromint(-1)
        z = rbigint.fromint(-2147483648)
        def test():
            return x.add(y).eq(z)
        assert test()
        res = interpret(test, [])
        assert res

    def test_args_from_rarith_int(self):
        from rpython.rtyper.tool.rfficache import platform
        from rpython.rlib.rarithmetic import r_int
        from rpython.rtyper.lltypesystem.rffi import r_int_real
        classlist = platform.numbertype_to_rclass.values()
        fnlist = []
        for r in classlist:
            if r in (r_int, r_int_real):     # and also r_longlong on 64-bit
                continue
            if r is int:
                mask = sys.maxint*2+1
                signed = True
            else:
                mask = r.MASK
                signed = r.SIGNED
            values = [0, -1, mask>>1, -(mask>>1)-1]
            if not signed:
                values = [x & mask for x in values]
            values = [r(x) for x in values]

            def fn(i):
                n = rbigint.fromrarith_int(values[i])
                return n.str()

            for i in range(len(values)):
                res = fn(i)
                assert res == str(long(values[i]))
                res = interpret(fn, [i])
                assert ''.join(res.chars) == str(long(values[i]))

    def test_truediv_overflow(self):
        overflowing = 2**1024 - 2**(1024-53-1)
        op1 = rbigint.fromlong(overflowing)

        def fn():
            try:
                return op1.truediv(rbigint.fromint(1))
            except OverflowError:
                return -42.0

        res = interpret(fn, [])
        assert res == -42.0

    def test_frombytes(self):
        bigint = rbigint.frombytes('', byteorder='big', signed=True)
        assert bigint.tolong() == 0
        s = "\xFF\x12\x34\x56"
        bigint = rbigint.frombytes(s, byteorder="big", signed=False)
        assert bigint.tolong() == 0xFF123456
        bigint = rbigint.frombytes(s, byteorder="little", signed=False)
        assert bigint.tolong() == 0x563412FF
        s = "\xFF\x02\x03\x04\x05\x06\x07\x08\x09\x10\x11\x12\x13\x14\x15\xFF"
        bigint = rbigint.frombytes(s, byteorder="big", signed=False)
        assert s == bigint.tobytes(16, byteorder="big", signed=False)
        py.test.raises(InvalidEndiannessError, bigint.frombytes, '\xFF', 'foo',
               signed=True)
        bigint = rbigint.frombytes('\x82', byteorder='big', signed=True)
        assert bigint.tolong() == -126

    def test_tobytes(self):
        assert rbigint.fromint(0).tobytes(1, 'big', signed=True) == '\x00'
        assert rbigint.fromint(1).tobytes(2, 'big', signed=True) == '\x00\x01'
        py.test.raises(OverflowError, rbigint.fromint(255).tobytes, 1, 'big', signed=True)
        assert rbigint.fromint(-129).tobytes(2, 'big', signed=True) == '\xff\x7f'
        assert rbigint.fromint(-129).tobytes(2, 'little', signed=True) == '\x7f\xff'
        assert rbigint.fromint(65535).tobytes(3, 'big', signed=True) == '\x00\xff\xff'
        assert rbigint.fromint(-65536).tobytes(3, 'little', signed=True) == '\x00\x00\xff'
        assert rbigint.fromint(65535).tobytes(2, 'big', signed=False) == '\xff\xff'
        assert rbigint.fromint(-8388608).tobytes(3, 'little', signed=True) == '\x00\x00\x80'
        i = rbigint.fromint(-8388608)
        py.test.raises(InvalidEndiannessError, i.tobytes, 3, 'foo', signed=True)
        py.test.raises(InvalidSignednessError, i.tobytes, 3, 'little', signed=False)
        py.test.raises(OverflowError, i.tobytes, 2, 'little', signed=True)


class TestTranslated(StandaloneTests):

    def test_gcc_4_9(self):
        MIN = -sys.maxint-1

        def entry_point(argv):
            print rbigint.fromint(MIN+1)._digits
            print rbigint.fromint(MIN)._digits
            return 0

        t, cbuilder = self.compile(entry_point)
        data = cbuilder.cmdexec('hi there')
        assert data == '[%d]\n[0, 1]\n' % sys.maxint
