
import py
from rpython.jit.backend.asmjs.test.test_basic import JitASMJSMixin
from rpython.jit.metainterp.test import test_quasiimmut

class TestLoopSpec(JitASMJSMixin, test_quasiimmut.QuasiImmutTests):
    # for the individual tests see
    # ====> ../../../metainterp/test/test_quasiimmut.py
    pass
