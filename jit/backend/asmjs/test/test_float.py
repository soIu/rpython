
import py
from rpython.jit.backend.asmjs.test.test_basic import JitASMJSMixin
from rpython.jit.metainterp.test.test_float import FloatTests

class TestFloat(JitASMJSMixin, FloatTests):
    # for the individual tests see
    # ====> ../../../metainterp/test/test_float.py

    def test_singlefloat(self):
        py.test.skip("singlefloat not supported")
