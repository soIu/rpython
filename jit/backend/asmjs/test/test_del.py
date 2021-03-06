
from rpython.jit.backend.asmjs.test.test_basic import JitASMJSMixin
from rpython.jit.metainterp.test.test_del import DelTests

class TestDel(JitASMJSMixin, DelTests):
    # for the individual tests see
    # ====> ../../../metainterp/test/test_del.py
    pass
