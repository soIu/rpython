
import py
from rpython.jit.metainterp.test.test_virtual import VirtualTests, VirtualMiscTests
from rpython.jit.backend.asmjs.test.test_basic import JitASMJSMixin

class MyClass:
    pass

class TestsVirtual(JitASMJSMixin, VirtualTests):
    # for the individual tests see
    # ====> ../../../metainterp/test/test_virtual.py
    _new_op = 'new_with_vtable'
    _field_prefix = 'inst_'
    
    @staticmethod
    def _new():
        return MyClass()


class TestsVirtualMisc(JitASMJSMixin, VirtualMiscTests):
    # for the individual tests see
    # ====> ../../../metainterp/test/test_virtual.py
    pass

