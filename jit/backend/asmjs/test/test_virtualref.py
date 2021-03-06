from rpython.jit.metainterp.test.test_virtualref import VRefTests
from rpython.jit.backend.asmjs.test.test_basic import JitASMJSMixin

class TestVRef(JitASMJSMixin, VRefTests):
    # for the individual tests see
    # ====> ../../../metainterp/test/test_virtualref.py
    pass
