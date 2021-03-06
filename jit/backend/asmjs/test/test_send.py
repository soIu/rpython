
import py
from rpython.jit.metainterp.test.test_send import SendTests
from rpython.jit.backend.asmjs.test.test_basic import JitASMJSMixin
from rpython.rlib import jit

class TestSend(JitASMJSMixin, SendTests):
    # for the individual tests see
    # ====> ../../../metainterp/test/test_send.py
    pass
