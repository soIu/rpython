
from rpython.jit.backend.asmjs.test.test_basic import JitASMJSMixin
from rpython.jit.metainterp.test.test_dict import DictTests


class TestDict(JitASMJSMixin, DictTests):
    # for the individual tests see
    # ====> ../../../metainterp/test/test_dict.py
    pass
