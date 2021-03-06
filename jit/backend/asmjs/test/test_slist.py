import py
from rpython.jit.metainterp.test import test_slist
from rpython.jit.backend.asmjs.test.test_basic import JitASMJSMixin

class TestSList(JitASMJSMixin, test_slist.ListTests):
    # for the individual tests see
    # ====> ../../../metainterp/test/test_slist.py
    def test_list_of_voids(self):
        py.test.skip("list of voids unsupported by ll2ctypes")
