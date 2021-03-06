import sys
sys.setrecursionlimit(10000)

import py
from rpython.rtyper.lltypesystem import lltype, llmemory, rffi, rstr
from rpython.jit.metainterp.history import ResOperation, TargetToken,\
     JitCellToken
from rpython.jit.metainterp.history import (BoxInt, BoxPtr, ConstInt,
                                            ConstPtr, Box,
                                            BasicFailDescr, BasicFinalDescr)
from rpython.jit.backend.detect_cpu import getcpuclass
from rpython.jit.backend.x86.arch import WORD
from rpython.jit.backend.x86.rx86 import fits_in_32bits
from rpython.jit.backend.llsupport import symbolic
from rpython.jit.metainterp.resoperation import rop
from rpython.jit.metainterp.executor import execute
from rpython.jit.backend.test.runner_test import LLtypeBackendTest
from rpython.jit.tool.oparser import parse
import ctypes

CPU = getcpuclass("asmjs")

class FakeStats(object):
    pass

U = LLtypeBackendTest.U
S = LLtypeBackendTest.S

# ____________________________________________________________

class TestASMJSRunner(LLtypeBackendTest):

    # for the individual tests see
    # ====> ../../test/runner_test.py

    def get_cpu(self):
        cpu = CPU(rtyper=None, stats=FakeStats())
        cpu.setup_once()
        return cpu

    def test_free_loop_and_bridges(self):
        py.test.xfail("XXX TODO we don't use the asmmemmgr thing")

    def test_compile_asmlen(self):
        py.test.skip("not relevant for asmjs?")

    def test_call_to_c_function(self):
        py.test.xfail("XXX TODO libffi calls not implemented yet")

    def test_call_to_c_function_with_callback(self):
        py.test.xfail("XXX TODO libffi callbacks not implemented yet")

    def test_compile_bridge_while_running(self):
        py.test.xfail("XXX TODO can't bridge from running code yet")

    def test_compile_bridge_while_running_guard_no_exc(self):
        py.test.xfail("XXX TODO can't bridge from running code yet")

    def test_execute_ptr_operation(self):
        cpu = self.cpu
        u = lltype.malloc(U)
        u_box = BoxPtr(lltype.cast_opaque_ptr(llmemory.GCREF, u))
        ofs = cpu.fielddescrof(S, 'value')
        assert self.execute_operation(rop.SETFIELD_GC,
                                      [u_box, BoxInt(3)],
                                     'void', ofs) == None
        assert u.parent.parent.value == 3
        u.parent.parent.value += 100
        assert (self.execute_operation(rop.GETFIELD_GC, [u_box], 'int', ofs)
                .value == 103)

    def test_unicode(self):
        ofs = symbolic.get_field_token(rstr.UNICODE, 'chars', False)[0]
        u = rstr.mallocunicode(13)
        for i in range(13):
            u.chars[i] = unichr(ord(u'a') + i)
        b = BoxPtr(lltype.cast_opaque_ptr(llmemory.GCREF, u))
        r = self.execute_operation(rop.UNICODEGETITEM, [b, ConstInt(2)], 'int')
        assert r.value == ord(u'a') + 2
        self.execute_operation(rop.UNICODESETITEM, [b, ConstInt(2),
                                                    ConstInt(ord(u'z'))],
                               'void')
        assert u.chars[2] == u'z'
        assert u.chars[3] == u'd'

    @staticmethod
    def _resbuf(res, item_tp=ctypes.c_long):
        return ctypes.cast(res.value._obj.intval, ctypes.POINTER(item_tp))

    def test_stringitems(self):
        from rpython.rtyper.lltypesystem.rstr import STR
        ofs = symbolic.get_field_token(STR, 'chars', False)[0]
        ofs_items = symbolic.get_field_token(STR.chars, 'items', False)[0]

        res = self.execute_operation(rop.NEWSTR, [ConstInt(10)], 'ref')
        self.execute_operation(rop.STRSETITEM, [res, ConstInt(2), ConstInt(ord('d'))], 'void')
        resbuf = self._resbuf(res, ctypes.c_char)
        assert resbuf[ofs + ofs_items + 2] == 'd'
        self.execute_operation(rop.STRSETITEM, [res, BoxInt(2), ConstInt(ord('z'))], 'void')
        assert resbuf[ofs + ofs_items + 2] == 'z'
        r = self.execute_operation(rop.STRGETITEM, [res, BoxInt(2)], 'int')
        assert r.value == ord('z')

    def test_arrayitems(self):
        TP = lltype.GcArray(lltype.Signed)
        ofs = symbolic.get_field_token(TP, 'length', False)[0]
        itemsofs = symbolic.get_field_token(TP, 'items', False)[0]
        descr = self.cpu.arraydescrof(TP)
        res = self.execute_operation(rop.NEW_ARRAY, [ConstInt(10)],
                                     'ref', descr)
        resbuf = self._resbuf(res)
        assert resbuf[ofs/WORD] == 10
        self.execute_operation(rop.SETARRAYITEM_GC, [res,
                                                     ConstInt(2), BoxInt(38)],
                               'void', descr)
        assert resbuf[itemsofs/WORD + 2] == 38

        self.execute_operation(rop.SETARRAYITEM_GC, [res,
                                                     BoxInt(3), BoxInt(42)],
                               'void', descr)
        assert resbuf[itemsofs/WORD + 3] == 42

        r = self.execute_operation(rop.GETARRAYITEM_GC, [res, ConstInt(2)],
                                   'int', descr)
        assert r.value == 38
        r = self.execute_operation(rop.GETARRAYITEM_GC, [res.constbox(),
                                                         BoxInt(2)],
                                   'int', descr)
        assert r.value == 38
        r = self.execute_operation(rop.GETARRAYITEM_GC, [res.constbox(),
                                                         ConstInt(2)],
                                   'int', descr)
        assert r.value == 38
        r = self.execute_operation(rop.GETARRAYITEM_GC, [res,
                                                         BoxInt(2)],
                                   'int', descr)
        assert r.value == 38

        r = self.execute_operation(rop.GETARRAYITEM_GC, [res, BoxInt(3)],
                                   'int', descr)
        assert r.value == 42

    def test_arrayitems_not_int(self):
        TP = lltype.GcArray(lltype.Char)
        ofs = symbolic.get_field_token(TP, 'length', False)[0]
        itemsofs = symbolic.get_field_token(TP, 'items', False)[0]
        descr = self.cpu.arraydescrof(TP)
        res = self.execute_operation(rop.NEW_ARRAY, [ConstInt(10)],
                                     'ref', descr)
        resbuf = self._resbuf(res, ctypes.c_char)
        assert resbuf[ofs] == chr(10)
        for i in range(10):
            self.execute_operation(rop.SETARRAYITEM_GC, [res,
                                                   ConstInt(i), BoxInt(i)],
                                   'void', descr)
        for i in range(10):
            assert resbuf[itemsofs + i] == chr(i)
        for i in range(10):
            r = self.execute_operation(rop.GETARRAYITEM_GC, [res,
                                                             ConstInt(i)],
                                         'int', descr)
            assert r.value == i

    def test_getfield_setfield(self):
        TP = lltype.GcStruct('x', ('s', lltype.Signed),
                             ('i', rffi.INT),
                             ('f', lltype.Float),
                             ('u', rffi.USHORT),
                             ('c1', lltype.Char),
                             ('c2', lltype.Char),
                             ('c3', lltype.Char))
        res = self.execute_operation(rop.NEW, [],
                                     'ref', self.cpu.sizeof(TP))
        ofs_s = self.cpu.fielddescrof(TP, 's')
        ofs_i = self.cpu.fielddescrof(TP, 'i')
        #ofs_f = self.cpu.fielddescrof(TP, 'f')
        ofs_u = self.cpu.fielddescrof(TP, 'u')
        ofsc1 = self.cpu.fielddescrof(TP, 'c1')
        ofsc2 = self.cpu.fielddescrof(TP, 'c2')
        ofsc3 = self.cpu.fielddescrof(TP, 'c3')
        self.execute_operation(rop.SETFIELD_GC, [res, ConstInt(3)], 'void',
                               ofs_s)
        # XXX ConstFloat
        #self.execute_operation(rop.SETFIELD_GC, [res, ofs_f, 1e100], 'void')
        # XXX we don't support shorts (at all)
        #self.execute_operation(rop.SETFIELD_GC, [res, ofs_u, ConstInt(5)], 'void')
        s = self.execute_operation(rop.GETFIELD_GC, [res], 'int', ofs_s)
        assert s.value == 3
        self.execute_operation(rop.SETFIELD_GC, [res, BoxInt(3)], 'void',
                               ofs_s)
        s = self.execute_operation(rop.GETFIELD_GC, [res], 'int', ofs_s)
        assert s.value == 3

        self.execute_operation(rop.SETFIELD_GC, [res, BoxInt(1234)], 'void', ofs_i)
        i = self.execute_operation(rop.GETFIELD_GC, [res], 'int', ofs_i)
        assert i.value == 1234

        #u = self.execute_operation(rop.GETFIELD_GC, [res, ofs_u], 'int')
        #assert u.value == 5
        self.execute_operation(rop.SETFIELD_GC, [res, ConstInt(1)], 'void',
                               ofsc1)
        self.execute_operation(rop.SETFIELD_GC, [res, ConstInt(3)], 'void',
                               ofsc3)
        self.execute_operation(rop.SETFIELD_GC, [res, ConstInt(2)], 'void',
                               ofsc2)
        c = self.execute_operation(rop.GETFIELD_GC, [res], 'int', ofsc1)
        assert c.value == 1
        c = self.execute_operation(rop.GETFIELD_GC, [res], 'int', ofsc2)
        assert c.value == 2
        c = self.execute_operation(rop.GETFIELD_GC, [res], 'int', ofsc3)
        assert c.value == 3

    def test_nullity_with_guard(self):
        allops = [rop.INT_IS_TRUE]
        guards = [rop.GUARD_TRUE, rop.GUARD_FALSE]
        p = lltype.cast_opaque_ptr(llmemory.GCREF,
                                   lltype.malloc(lltype.GcStruct('x')))
        nullptr = lltype.nullptr(llmemory.GCREF.TO)
        f = BoxInt()
        for op in allops:
            for guard in guards:
                if op == rop.INT_IS_TRUE:
                    bp = BoxInt(1)
                    n = BoxInt(0)
                else:
                    bp = BoxPtr(p)
                    n = BoxPtr(nullptr)
                for b in (bp, n):
                    i1 = BoxInt(1)
                    ops = [
                        ResOperation(rop.SAME_AS, [ConstInt(1)], i1),
                        ResOperation(op, [b], f),
                        ResOperation(guard, [f], None,
                                     descr=BasicFailDescr()),
                        ResOperation(rop.FINISH, [ConstInt(0)], None,
                                     descr=BasicFinalDescr()),
                        ]
                    ops[-2].setfailargs([i1])
                    looptoken = JitCellToken()
                    self.cpu.compile_loop([b], ops, looptoken)
                    deadframe = self.cpu.execute_token(looptoken, b.value)
                    result = self.cpu.get_int_value(deadframe, 0)
                    if guard == rop.GUARD_FALSE:
                        assert result == execute(self.cpu, None,
                                                 op, None, b).value
                    else:
                        assert result != execute(self.cpu, None,
                                                 op, None, b).value

    def test_stuff_followed_by_guard(self):
        boxes = [(BoxInt(1), BoxInt(0)),
                 (BoxInt(0), BoxInt(1)),
                 (BoxInt(1), BoxInt(1)),
                 (BoxInt(-1), BoxInt(1)),
                 (BoxInt(1), BoxInt(-1)),
                 (ConstInt(1), BoxInt(0)),
                 (ConstInt(0), BoxInt(1)),
                 (ConstInt(1), BoxInt(1)),
                 (ConstInt(-1), BoxInt(1)),
                 (ConstInt(1), BoxInt(-1)),
                 (BoxInt(1), ConstInt(0)),
                 (BoxInt(0), ConstInt(1)),
                 (BoxInt(1), ConstInt(1)),
                 (BoxInt(-1), ConstInt(1)),
                 (BoxInt(1), ConstInt(-1))]
        guards = [rop.GUARD_FALSE, rop.GUARD_TRUE]
        all = [rop.INT_EQ, rop.INT_NE, rop.INT_LE, rop.INT_LT, rop.INT_GT,
               rop.INT_GE, rop.UINT_GT, rop.UINT_LT, rop.UINT_LE, rop.UINT_GE]
        for a, b in boxes:
            for guard in guards:
                for op in all:
                    res = BoxInt()
                    i1 = BoxInt(1)
                    ops = [
                        ResOperation(rop.SAME_AS, [ConstInt(1)], i1),
                        ResOperation(op, [a, b], res),
                        ResOperation(guard, [res], None,
                                     descr=BasicFailDescr()),
                        ResOperation(rop.FINISH, [ConstInt(0)], None,
                                     descr=BasicFinalDescr()),
                        ]
                    ops[-2].setfailargs([i1])
                    inputargs = [i for i in (a, b) if isinstance(i, Box)]
                    looptoken = JitCellToken()
                    self.cpu.compile_loop(inputargs, ops, looptoken)
                    inputvalues = [box.value for box in inputargs]
                    deadframe = self.cpu.execute_token(looptoken, *inputvalues)
                    result = self.cpu.get_int_value(deadframe, 0)
                    expected = execute(self.cpu, None, op, None, a, b).value
                    if guard == rop.GUARD_FALSE:
                        assert result == expected
                    else:
                        assert result != expected
