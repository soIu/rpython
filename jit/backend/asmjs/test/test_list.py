
from rpython.jit.metainterp.test.test_list import ListTests
from rpython.jit.backend.asmjs.test.test_basic import JitASMJSMixin

class TestList(JitASMJSMixin, ListTests):
    # for individual tests see
    # ====> ../../../metainterp/test/test_list.py
    pass
