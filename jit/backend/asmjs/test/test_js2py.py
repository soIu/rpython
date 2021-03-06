#
# Test that the javascript-to-python converter actually implements
# the correct semantics of javascript operators.
#
# Currently this only tests arithmetic expressions.
#

import random
import unittest
import subprocess

from rpython.translator.platform import emscripten_platform
from rpython.jit.backend.asmjs import support, jsvalue, jsbuilder

MAX_INT_32 = int(2**31 - 1)
MIN_INT_32 = int(-(2**31))
MAX_INT_16 = int(2**15 - 1)
MIN_INT_16 = int(-(2**15))
MAX_INT_8 = int(2**7 - 1)
MIN_INT_8 = int(-(2**7))

BINARY_OPS = []
UNARY_OPS = []

for nm in dir(jsvalue):
    cls = getattr(jsvalue, nm)
    if not isinstance(cls, type):
        continue
    if issubclass(cls, jsvalue.ASMJSUnaryOp):
        if cls is not jsvalue.ASMJSUnaryOp and cls.__name__[0] != "_":
            if cls.__name__ != "UNot":
                UNARY_OPS.append(cls)
    elif issubclass(cls, jsvalue.ASMJSBinaryOp):
        if cls is not jsvalue.ASMJSBinaryOp and cls.__name__[0] != "_":
            if not issubclass(cls, jsvalue._Comparison):
                if cls.__name__ != "Mul":
                    BINARY_OPS.append(cls)


def make_random_expr(size, seed=None, r=None):
    if r is None:
        r = random.Random()
    if seed is not None:
        r.seed(seed)
    if size <= 0:
        bounds = random.choice(((MIN_INT_32, MAX_INT_32),
                                (MIN_INT_16, MAX_INT_16),
                                (MIN_INT_8, MAX_INT_8)))
        return jsvalue.ConstInt(r.randint(*bounds))
    def subexpr():
        return make_random_expr(r.randint(0, size - 1), r=r)
    if r.randint(1, 2) == 1:
        op = r.choice(UNARY_OPS)
        return op(subexpr())
    else:
        op = r.choice(BINARY_OPS)
        return op(subexpr(), subexpr())


def build_jssrc(expr):
    bldr = jsbuilder.ASMJSBuilder(None)
    bldr.emit_assignment(jsvalue.frame, expr)
    return bldr.finish()


def execute_py(jssrc):
    """Execute the  given JS by converting it to a python function."""
    F = support.load_asmjs(jssrc)
    return F(0, 0)


def execute_js(jssrc):
    """Execute the  given JS by shelling out to an interpreter."""
    jsshell = emscripten_platform.find_javascript_shell()
    p = subprocess.Popen([jsshell], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    p.stdin.write(jssrc)
    p.stdin.write("\n")
    stdlib = "{Math:Math, Int8Array:Int8Array, Int16Array:Int16Array, " \
             "Int32Array:Int32Array, Uint8Array:Uint8Array, " \
             "Uint16Array:Uint16Array, Uint32Array:Uint32Array, " \
             "Float32Array:Float32Array, Float64Array:Float64Array}"
    p.stdin.write("var stdlib = " + stdlib + "\n")
    p.stdin.write("var F = M(stdlib, {}, new ArrayBuffer(0x1000))\n")
    p.stdin.write("print(F(0, 0))\n")
    p.stdin.close()
    assert p.wait() == 0
    return int(p.stdout.read().strip().split("\n")[-2])
 

class TestJSToPythonConversion(unittest.TestCase):

    def _perform_integer_operations(self):
        raise unittest.SkipTest
        r = random.Random()
        for _ in xrange(100):
            seed = r.uniform(0, 1000)
            expr = make_random_expr(20, seed)
            jssrc = build_jssrc(expr)
            self.assertEqual(execute_py(jssrc), execute_js(jssrc))

    def test_integer_operations_1(self):
        self._perform_integer_operations()

    def test_integer_operations_2(self):
        self._perform_integer_operations()

    def test_integer_operations_3(self):
        self._perform_integer_operations()
