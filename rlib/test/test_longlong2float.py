from rpython.translator.c.test.test_genc import compile
from rpython.rlib.longlong2float import longlong2float, float2longlong
from rpython.rlib.longlong2float import uint2singlefloat, singlefloat2uint
from rpython.rlib.rarithmetic import r_singlefloat
from rpython.rtyper.test.test_llinterp import interpret


def fn(f1):
    ll = float2longlong(f1)
    f2 = longlong2float(ll)
    return f2

def enum_floats():
    inf = 1e200 * 1e200
    yield 0.0
    yield -0.0
    yield 1.0
    yield -2.34567
    yield 2.134891117e22
    yield inf
    yield -inf
    yield inf / inf     # nan

def test_longlong_as_float():
    for x in enum_floats():
        res = fn(x)
        assert repr(res) == repr(x)

def test_compiled():
    fn2 = compile(fn, [float])
    for x in enum_floats():
        res = fn2(x)
        assert repr(res) == repr(x)

def test_interpreted():
    def f(f1):
        try:
            ll = float2longlong(f1)
            return longlong2float(ll)
        except Exception:
            return 500

    for x in enum_floats():
        res = interpret(f, [x])
        assert repr(res) == repr(x)

# ____________________________________________________________

def fnsingle(f1):
    sf1 = r_singlefloat(f1)
    ii = singlefloat2uint(sf1)
    sf2 = uint2singlefloat(ii)
    f2 = float(sf2)
    return f2

def test_int_as_singlefloat():
    for x in enum_floats():
        res = fnsingle(x)
        assert repr(res) == repr(float(r_singlefloat(x)))

def test_compiled_single():
    fn2 = compile(fnsingle, [float])
    for x in enum_floats():
        res = fn2(x)
        assert repr(res) == repr(float(r_singlefloat(x)))
