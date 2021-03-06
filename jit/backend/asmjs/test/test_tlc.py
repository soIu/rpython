
import py
from rpython.jit.backend.asmjs.test.test_basic import JitASMJSMixin
from rpython.jit.metainterp.test.test_tlc import TLCTests
from rpython.jit.tl import tlc

class TestTL(JitASMJSMixin, TLCTests):
    # for the individual tests see
    # ====> ../../test/test_tlc.py
    
    def test_accumulator(self):
        py.test.skip("investigate, maybe")

    def test_fib(self):
        py.test.skip("investigate, maybe")
