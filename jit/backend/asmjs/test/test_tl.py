
import py
from rpython.jit.metainterp.test.test_tl import ToyLanguageTests
from rpython.jit.backend.asmjs.test.test_basic import JitASMJSMixin

class TestTL(JitASMJSMixin, ToyLanguageTests):
    # for the individual tests see
    # ====> ../../../metainterp/test/test_tl.py
    pass

