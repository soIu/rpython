import py
from rpython.jit.backend.asmjs.test.test_basic import JitASMJSMixin
from rpython.jit.metainterp.test import test_loop_unroll

class TestLoopSpec(JitASMJSMixin, test_loop_unroll.LoopUnrollTest):
    # for the individual tests see
    # ====> ../../../metainterp/test/test_loop.py
    pass
