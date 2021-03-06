from rpython.jit.metainterp.test.test_recursive import RecursiveTests
from rpython.jit.backend.asmjs.test.test_basic import JitASMJSMixin

class TestRecursive(JitASMJSMixin, RecursiveTests):
    # for the individual tests see
    # ====> ../../../metainterp/test/test_recursive.py
    pass
