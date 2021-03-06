import py
from rpython.jit.backend.detect_cpu import getcpuclass
from rpython.jit.metainterp.warmspot import ll_meta_interp
from rpython.jit.metainterp.test import support, test_ajit
from rpython.jit.codewriter.policy import StopAtXPolicy
from rpython.rlib.jit import JitDriver


class JitASMJSMixin(support.LLJitMixin):
    type_system = 'lltype'
    CPUClass = getcpuclass('asmjs')

    def check_jumps(self, maxcount):
        pass


class TestBasic(JitASMJSMixin, test_ajit.BaseLLtypeTests):

    def test_r_dict(self):
        # a Struct that belongs to the hash table is not seen as being
        # included in the larger Array
        py.test.skip("issue with ll2ctypes")

    def test_free_object(self):
        py.test.skip("issue of freeing, probably with ll2ctypes")

    def test_read_timestamp(self):
        py.test.skip("not implemented yet; test requires longlong support")

    def test_ulonglong_mod(self):
        py.test.skip("not implemented yet; test requires longlong support")
