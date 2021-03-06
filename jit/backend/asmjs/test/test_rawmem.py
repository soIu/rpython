
from rpython.jit.backend.asmjs.test.test_basic import JitASMJSMixin
from rpython.jit.metainterp.test.test_rawmem import RawMemTests


class TestRawMem(JitASMJSMixin, RawMemTests):
    # for the individual tests see
    # ====> ../../../metainterp/test/test_rawmem.py
    pass
