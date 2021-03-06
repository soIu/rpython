import py
from rpython.jit.metainterp.test import test_string
from rpython.jit.backend.asmjs.test.test_basic import JitASMJSMixin

class TestString(JitASMJSMixin, test_string.TestLLtype):
    # for the individual tests see
    # ====> ../../../metainterp/test/test_string.py
    pass

class TestUnicode(JitASMJSMixin, test_string.TestLLtypeUnicode):
    # for the individual tests see
    # ====> ../../../metainterp/test/test_string.py
    pass
