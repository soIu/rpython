import py, sys, random, os, struct, operator
from rpython.jit.metainterp.history import (AbstractFailDescr,
                                         AbstractDescr,
                                         BasicFailDescr,
                                         BasicFinalDescr,
                                         BoxInt, Box, BoxPtr,
                                         JitCellToken, TargetToken,
                                         ConstInt, ConstPtr,
                                         BoxFloat, ConstFloat)
from rpython.jit.metainterp.resoperation import ResOperation, rop
from rpython.jit.metainterp.typesystem import deref
from rpython.jit.codewriter.effectinfo import EffectInfo
from rpython.jit.tool.oparser import parse
from rpython.rtyper.lltypesystem import lltype, llmemory, rstr, rffi
from rpython.rtyper import rclass
from rpython.rtyper.annlowlevel import llhelper
from rpython.rtyper.llinterp import LLException
from rpython.jit.codewriter import heaptracker, longlong
from rpython.rlib import longlong2float
from rpython.rlib.rarithmetic import intmask, is_valid_int
from rpython.jit.backend.detect_cpu import autodetect
from rpython.jit.backend.llsupport import jitframe
from rpython.jit.backend.llsupport.llmodel import AbstractLLCPU


IS_32_BIT = sys.maxint < 2**32
IS_64_BIT = sys.maxint > 2**32

def boxfloat(x):
    return BoxFloat(longlong.getfloatstorage(x))

def constfloat(x):
    return ConstFloat(longlong.getfloatstorage(x))

def boxlonglong(ll):
    if longlong.is_64_bit:
        return BoxInt(ll)
    else:
        return BoxFloat(ll)

STUFF = lltype.GcStruct('STUFF')
random_gcref = lltype.cast_opaque_ptr(llmemory.GCREF,
                                      lltype.malloc(STUFF, immortal=True))


class Runner(object):

    add_loop_instructions = ['overload for a specific cpu']
    bridge_loop_instructions = ['overload for a specific cpu']
    bridge_loop_instructions_alternative = None   # or another possible answer

    def execute_operation(self, opname, valueboxes, result_type, descr=None):
        inputargs, operations = self._get_single_operation_list(opname,
                                                                result_type,
                                                                valueboxes,
                                                                descr)
        looptoken = JitCellToken()
        self.cpu.compile_loop(inputargs, operations, looptoken)
        args = []
        for box in inputargs:
            if isinstance(box, BoxInt):
                args.append(box.getint())
            elif isinstance(box, BoxPtr):
                args.append(box.getref_base())
            elif isinstance(box, BoxFloat):
                args.append(box.getfloatstorage())
            else:
                raise NotImplementedError(box)
        deadframe = self.cpu.execute_token(looptoken, *args)
        if self.cpu.get_latest_descr(deadframe) is operations[-1].getdescr():
            self.guard_failed = False
        else:
            self.guard_failed = True
        if result_type == 'int':
            return BoxInt(self.cpu.get_int_value(deadframe, 0))
        elif result_type == 'ref':
            return BoxPtr(self.cpu.get_ref_value(deadframe, 0))
        elif result_type == 'float':
            return BoxFloat(self.cpu.get_float_value(deadframe, 0))
        elif result_type == 'void':
            return None
        else:
            assert False

    def _get_single_operation_list(self, opnum, result_type, valueboxes,
                                   descr):
        if result_type == 'void':
            result = None
        elif result_type == 'int':
            result = BoxInt()
        elif result_type == 'ref':
            result = BoxPtr()
        elif result_type == 'float':
            result = BoxFloat()
        else:
            raise ValueError(result_type)
        if result is None:
            results = []
        else:
            results = [result]
        operations = [ResOperation(opnum, valueboxes, result),
                      ResOperation(rop.FINISH, results, None,
                                   descr=BasicFinalDescr(0))]
        if operations[0].is_guard():
            operations[0].setfailargs([])
            if not descr:
                descr = BasicFailDescr(1)
        if descr is not None:
            operations[0].setdescr(descr)
        inputargs = []
        for box in valueboxes:
            if isinstance(box, Box) and box not in inputargs:
                inputargs.append(box)
        return inputargs, operations

class BaseBackendTest(Runner):

    avoid_instances = False

    def setup_method(self, _):
        self.cpu = self.get_cpu()
        self.cpu.done_with_this_frame_descr_int = None
        self.cpu.done_with_this_frame_descr_ref = None
        self.cpu.done_with_this_frame_descr_float = None
        self.cpu.done_with_this_frame_descr_void = None

    def test_compile_linear_loop(self):
        i0 = BoxInt()
        i1 = BoxInt()
        operations = [
            ResOperation(rop.INT_ADD, [i0, ConstInt(1)], i1),
            ResOperation(rop.FINISH, [i1], None, descr=BasicFinalDescr(1))
            ]
        inputargs = [i0]
        looptoken = JitCellToken()
        self.cpu.compile_loop(inputargs, operations, looptoken)
        deadframe = self.cpu.execute_token(looptoken, 2)
        fail = self.cpu.get_latest_descr(deadframe)
        res = self.cpu.get_int_value(deadframe, 0)
        assert res == 3
        assert fail.identifier == 1

    def test_compile_linear_float_loop(self):
        if not self.cpu.supports_floats:
            py.test.skip("requires floats")
        i0 = BoxFloat()
        i1 = BoxFloat()
        operations = [
            ResOperation(rop.FLOAT_ADD, [i0, constfloat(2.3)], i1),
            ResOperation(rop.FINISH, [i1], None, descr=BasicFinalDescr(1))
            ]
        inputargs = [i0]
        looptoken = JitCellToken()
        self.cpu.compile_loop(inputargs, operations, looptoken)
        deadframe = self.cpu.execute_token(looptoken,
                                           longlong.getfloatstorage(2.8))
        fail = self.cpu.get_latest_descr(deadframe)
        res = self.cpu.get_float_value(deadframe, 0)
        assert longlong.getrealfloat(res) == 5.1
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == 1

    def test_compile_loop(self):
        i0 = BoxInt()
        i1 = BoxInt()
        i2 = BoxInt()
        looptoken = JitCellToken()
        targettoken = TargetToken()
        operations = [
            ResOperation(rop.LABEL, [i0], None, descr=targettoken),
            ResOperation(rop.INT_ADD, [i0, ConstInt(1)], i1),
            ResOperation(rop.INT_LE, [i1, ConstInt(9)], i2),
            ResOperation(rop.GUARD_TRUE, [i2], None, descr=BasicFailDescr(2)),
            ResOperation(rop.JUMP, [i1], None, descr=targettoken),
            ]
        inputargs = [i0]
        operations[3].setfailargs([i1])

        self.cpu.compile_loop(inputargs, operations, looptoken)
        deadframe = self.cpu.execute_token(looptoken, 2)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == 2
        res = self.cpu.get_int_value(deadframe, 0)
        assert res == 10

    def test_compile_with_holes_in_fail_args(self):
        i0 = BoxInt()
        i1 = BoxInt()
        i2 = BoxInt()
        i3 = BoxInt()
        looptoken = JitCellToken()
        targettoken = TargetToken()
        operations = [
            ResOperation(rop.INT_SUB, [i3, ConstInt(42)], i0),
            ResOperation(rop.LABEL, [i0], None, descr=targettoken),
            ResOperation(rop.INT_ADD, [i0, ConstInt(1)], i1),
            ResOperation(rop.INT_LE, [i1, ConstInt(9)], i2),
            ResOperation(rop.GUARD_TRUE, [i2], None, descr=BasicFailDescr(2)),
            ResOperation(rop.JUMP, [i1], None, descr=targettoken),
            ]
        inputargs = [i3]
        operations[4].setfailargs([None, None, i1, None])

        self.cpu.compile_loop(inputargs, operations, looptoken)
        deadframe = self.cpu.execute_token(looptoken, 44)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == 2
        res = self.cpu.get_int_value(deadframe, 2)
        assert res == 10

    def test_backends_dont_keep_loops_alive(self):
        import weakref, gc
        self.cpu.dont_keepalive_stuff = True
        i0 = BoxInt()
        i1 = BoxInt()
        i2 = BoxInt()
        looptoken = JitCellToken()
        targettoken = TargetToken()
        operations = [
            ResOperation(rop.LABEL, [i0], None, descr=targettoken),
            ResOperation(rop.INT_ADD, [i0, ConstInt(1)], i1),
            ResOperation(rop.INT_LE, [i1, ConstInt(9)], i2),
            ResOperation(rop.GUARD_TRUE, [i2], None, descr=BasicFailDescr()),
            ResOperation(rop.JUMP, [i1], None, descr=targettoken),
            ]
        inputargs = [i0]
        operations[3].setfailargs([i1])
        wr_i1 = weakref.ref(i1)
        wr_guard = weakref.ref(operations[2])
        self.cpu.compile_loop(inputargs, operations, looptoken)
        if hasattr(looptoken, '_x86_ops_offset'):
            del looptoken._x86_ops_offset # else it's kept alive
        del i0, i1, i2
        del inputargs
        del operations
        gc.collect()
        assert not wr_i1() and not wr_guard()

    def test_compile_bridge(self):
        self.cpu.tracker.total_compiled_loops = 0
        self.cpu.tracker.total_compiled_bridges = 0
        i0 = BoxInt()
        i1 = BoxInt()
        i2 = BoxInt()
        faildescr1 = BasicFailDescr(1)
        faildescr2 = BasicFailDescr(2)
        looptoken = JitCellToken()
        targettoken = TargetToken()
        operations = [
            ResOperation(rop.LABEL, [i0], None, descr=targettoken),
            ResOperation(rop.INT_ADD, [i0, ConstInt(1)], i1),
            ResOperation(rop.INT_LE, [i1, ConstInt(9)], i2),
            ResOperation(rop.GUARD_TRUE, [i2], None, descr=faildescr1),
            ResOperation(rop.JUMP, [i1], None, descr=targettoken),
            ]
        inputargs = [i0]
        operations[3].setfailargs([i1])
        self.cpu.compile_loop(inputargs, operations, looptoken)

        i1b = BoxInt()
        i3 = BoxInt()
        bridge = [
            ResOperation(rop.INT_LE, [i1b, ConstInt(19)], i3),
            ResOperation(rop.GUARD_TRUE, [i3], None, descr=faildescr2),
            ResOperation(rop.JUMP, [i1b], None, descr=targettoken),
        ]
        bridge[1].setfailargs([i1b])

        self.cpu.compile_bridge(faildescr1, [i1b], bridge, looptoken)

        deadframe = self.cpu.execute_token(looptoken, 2)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == 2
        res = self.cpu.get_int_value(deadframe, 0)
        assert res == 20

        assert self.cpu.tracker.total_compiled_loops == 1
        assert self.cpu.tracker.total_compiled_bridges == 1
        return looptoken

    def test_compile_bridge_with_holes(self):
        i0 = BoxInt()
        i1 = BoxInt()
        i2 = BoxInt()
        i3 = BoxInt()
        faildescr1 = BasicFailDescr(1)
        faildescr2 = BasicFailDescr(2)
        looptoken = JitCellToken()
        targettoken = TargetToken()
        operations = [
            ResOperation(rop.INT_SUB, [i3, ConstInt(42)], i0),
            ResOperation(rop.LABEL, [i0], None, descr=targettoken),
            ResOperation(rop.INT_ADD, [i0, ConstInt(1)], i1),
            ResOperation(rop.INT_LE, [i1, ConstInt(9)], i2),
            ResOperation(rop.GUARD_TRUE, [i2], None, descr=faildescr1),
            ResOperation(rop.JUMP, [i1], None, descr=targettoken),
            ]
        inputargs = [i3]
        operations[4].setfailargs([None, i1, None])
        self.cpu.compile_loop(inputargs, operations, looptoken)

        i1b = BoxInt()
        i3 = BoxInt()
        bridge = [
            ResOperation(rop.INT_LE, [i1b, ConstInt(19)], i3),
            ResOperation(rop.GUARD_TRUE, [i3], None, descr=faildescr2),
            ResOperation(rop.JUMP, [i1b], None, descr=targettoken),
        ]
        bridge[1].setfailargs([i1b])

        self.cpu.compile_bridge(faildescr1, [i1b], bridge, looptoken)

        deadframe = self.cpu.execute_token(looptoken, 2)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == 2
        res = self.cpu.get_int_value(deadframe, 0)
        assert res == 20

    def test_compile_big_bridge_out_of_small_loop(self):
        i0 = BoxInt()
        faildescr1 = BasicFailDescr(1)
        looptoken = JitCellToken()
        operations = [
            ResOperation(rop.GUARD_FALSE, [i0], None, descr=faildescr1),
            ResOperation(rop.FINISH, [], None, descr=BasicFinalDescr(2)),
            ]
        inputargs = [i0]
        operations[0].setfailargs([i0])
        self.cpu.compile_loop(inputargs, operations, looptoken)

        i1list = [BoxInt() for i in range(150)]
        bridge = []
        iprev = i0
        for i1 in i1list:
            bridge.append(ResOperation(rop.INT_ADD, [iprev, ConstInt(1)], i1))
            iprev = i1
        bridge.append(ResOperation(rop.GUARD_FALSE, [i0], None,
                                   descr=BasicFailDescr(3)))
        bridge.append(ResOperation(rop.FINISH, [], None,
                                   descr=BasicFinalDescr(4)))
        bridge[-2].setfailargs(i1list)

        self.cpu.compile_bridge(faildescr1, [i0], bridge, looptoken)

        deadframe = self.cpu.execute_token(looptoken, 1)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == 3
        for i in range(len(i1list)):
            res = self.cpu.get_int_value(deadframe, i)
            assert res == 2 + i

    def test_finish(self):
        from rpython.jit.backend.llsupport.llmodel import final_descr_rd_locs

        i0 = BoxInt()
        class UntouchableFailDescr(AbstractFailDescr):
            final_descr = True
            rd_locs = final_descr_rd_locs

            def __setattr__(self, name, value):
                if (name == 'index' or name == '_carry_around_for_tests'
                        or name == '_TYPE' or name == '_cpu'):
                    return AbstractFailDescr.__setattr__(self, name, value)
                py.test.fail("finish descrs should not be touched")
        faildescr = UntouchableFailDescr() # to check that is not touched
        looptoken = JitCellToken()
        operations = [
            ResOperation(rop.FINISH, [i0], None, descr=faildescr)
            ]
        self.cpu.compile_loop([i0], operations, looptoken)
        deadframe = self.cpu.execute_token(looptoken, 99)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail is faildescr
        res = self.cpu.get_int_value(deadframe, 0)
        assert res == 99

        looptoken = JitCellToken()
        operations = [
            ResOperation(rop.FINISH, [ConstInt(42)], None, descr=faildescr)
            ]
        self.cpu.compile_loop([], operations, looptoken)
        deadframe = self.cpu.execute_token(looptoken)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail is faildescr
        res = self.cpu.get_int_value(deadframe, 0)
        assert res == 42

        looptoken = JitCellToken()
        operations = [
            ResOperation(rop.FINISH, [], None, descr=faildescr)
            ]
        self.cpu.compile_loop([], operations, looptoken)
        deadframe = self.cpu.execute_token(looptoken)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail is faildescr

        if self.cpu.supports_floats:
            looptoken = JitCellToken()
            f0 = BoxFloat()
            operations = [
                ResOperation(rop.FINISH, [f0], None, descr=faildescr)
                ]
            self.cpu.compile_loop([f0], operations, looptoken)
            value = longlong.getfloatstorage(-61.25)
            deadframe = self.cpu.execute_token(looptoken, value)
            fail = self.cpu.get_latest_descr(deadframe)
            assert fail is faildescr
            res = self.cpu.get_float_value(deadframe, 0)
            assert longlong.getrealfloat(res) == -61.25

            looptoken = JitCellToken()
            operations = [
                ResOperation(rop.FINISH, [constfloat(42.5)], None, descr=faildescr)
                ]
            self.cpu.compile_loop([], operations, looptoken)
            deadframe = self.cpu.execute_token(looptoken)
            fail = self.cpu.get_latest_descr(deadframe)
            assert fail is faildescr
            res = self.cpu.get_float_value(deadframe, 0)
            assert longlong.getrealfloat(res) == 42.5

    def test_execute_operations_in_env(self):
        cpu = self.cpu
        x = BoxInt(123)
        y = BoxInt(456)
        z = BoxInt(579)
        t = BoxInt(455)
        u = BoxInt(0)    # False
        looptoken = JitCellToken()
        targettoken = TargetToken()
        operations = [
            ResOperation(rop.LABEL, [y, x], None, descr=targettoken),
            ResOperation(rop.INT_ADD, [x, y], z),
            ResOperation(rop.INT_SUB, [y, ConstInt(1)], t),
            ResOperation(rop.INT_EQ, [t, ConstInt(0)], u),
            ResOperation(rop.GUARD_FALSE, [u], None,
                         descr=BasicFailDescr()),
            ResOperation(rop.JUMP, [t, z], None, descr=targettoken),
            ]
        operations[-2].setfailargs([t, z])
        cpu.compile_loop([x, y], operations, looptoken)
        deadframe = self.cpu.execute_token(looptoken, 0, 10)
        assert self.cpu.get_int_value(deadframe, 0) == 0
        assert self.cpu.get_int_value(deadframe, 1) == 55

    def test_int_operations(self):
        from rpython.jit.metainterp.test.test_executor import get_int_tests
        for opnum, boxargs, retvalue in get_int_tests():
            print opnum
            res = self.execute_operation(opnum, boxargs, 'int')
            assert res.value == retvalue

    def test_float_operations(self):
        from rpython.jit.metainterp.test.test_executor import get_float_tests
        for opnum, boxargs, rettype, retvalue in get_float_tests(self.cpu):
            res = self.execute_operation(opnum, boxargs, rettype)
            if isinstance(res, BoxFloat):
                assert res.getfloat() == retvalue
            else:
                assert res.value == retvalue

    def test_ovf_operations(self, reversed=False):
        minint = -sys.maxint-1
        boom = 'boom'
        for opnum, testcases in [
            (rop.INT_ADD_OVF, [(10, -2, 8),
                               (-1, minint, boom),
                               (sys.maxint//2, sys.maxint//2+2, boom)]),
            (rop.INT_SUB_OVF, [(-20, -23, 3),
                               (-2, sys.maxint, boom),
                               (sys.maxint//2, -(sys.maxint//2+2), boom)]),
            (rop.INT_MUL_OVF, [(minint/2, 2, minint),
                               (-2, -(minint/2), minint),
                               (minint/2, -2, boom)]),
            ]:
            v1 = BoxInt(testcases[0][0])
            v2 = BoxInt(testcases[0][1])
            v_res = BoxInt()
            #
            if not reversed:
                ops = [
                    ResOperation(opnum, [v1, v2], v_res),
                    ResOperation(rop.GUARD_NO_OVERFLOW, [], None,
                                 descr=BasicFailDescr(1)),
                    ResOperation(rop.FINISH, [v_res], None,
                                 descr=BasicFinalDescr(2)),
                    ]
                ops[1].setfailargs([])
            else:
                v_exc = self.cpu.ts.BoxRef()
                ops = [
                    ResOperation(opnum, [v1, v2], v_res),
                    ResOperation(rop.GUARD_OVERFLOW, [], None,
                                 descr=BasicFailDescr(1)),
                    ResOperation(rop.FINISH, [], None, descr=BasicFinalDescr(2)),
                    ]
                ops[1].setfailargs([v_res])
            #
            looptoken = JitCellToken()
            self.cpu.compile_loop([v1, v2], ops, looptoken)
            for x, y, z in testcases:
                deadframe = self.cpu.execute_token(looptoken, x, y)
                fail = self.cpu.get_latest_descr(deadframe)
                if (z == boom) ^ reversed:
                    assert fail.identifier == 1
                else:
                    assert fail.identifier == 2
                if z != boom:
                    assert self.cpu.get_int_value(deadframe, 0) == z
                excvalue = self.cpu.grab_exc_value(deadframe)
                assert not excvalue

    def test_ovf_operations_reversed(self):
        self.test_ovf_operations(reversed=True)

    def test_bh_call(self):
        cpu = self.cpu
        #
        def func(c):
            return chr(ord(c) + 1)
        FPTR = self.Ptr(self.FuncType([lltype.Char], lltype.Char))
        func_ptr = llhelper(FPTR, func)
        calldescr = cpu.calldescrof(deref(FPTR), (lltype.Char,), lltype.Char,
                                    EffectInfo.MOST_GENERAL)
        x = cpu.bh_call_i(self.get_funcbox(cpu, func_ptr).value,
                          [ord('A')], None, None, calldescr)
        assert x == ord('B')
        if cpu.supports_floats:
            def func(f, i):
                assert isinstance(f, float)
                assert is_valid_int(i)
                return f - float(i)
            FPTR = self.Ptr(self.FuncType([lltype.Float, lltype.Signed],
                                          lltype.Float))
            func_ptr = llhelper(FPTR, func)
            FTP = deref(FPTR)
            calldescr = cpu.calldescrof(FTP, FTP.ARGS, FTP.RESULT,
                                        EffectInfo.MOST_GENERAL)
            x = cpu.bh_call_f(self.get_funcbox(cpu, func_ptr).value,
                              [42], None, [longlong.getfloatstorage(3.5)],
                              calldescr)
            assert longlong.getrealfloat(x) == 3.5 - 42

    def test_call(self):
        from rpython.rlib.jit_libffi import types

        def func_int(a, b):
            return a + b
        def func_char(c, c1):
            return chr(ord(c) + ord(c1))

        functions = [
            (func_int, lltype.Signed, types.sint, 655360),
            (func_int, rffi.SHORT, types.sint16, 1213),
            (func_char, lltype.Char, types.uchar, 12)
            ]

        cpu = self.cpu
        for func, TP, ffi_type, num in functions:
            #
            FPTR = self.Ptr(self.FuncType([TP, TP], TP))
            func_ptr = llhelper(FPTR, func)
            FUNC = deref(FPTR)
            funcbox = self.get_funcbox(cpu, func_ptr)
            # first, try it with the "normal" calldescr
            calldescr = cpu.calldescrof(FUNC, FUNC.ARGS, FUNC.RESULT,
                                        EffectInfo.MOST_GENERAL)
            res = self.execute_operation(rop.CALL,
                                         [funcbox, BoxInt(num), BoxInt(num)],
                                         'int', descr=calldescr)
            assert res.value == 2 * num
            # then, try it with the dynamic calldescr
            dyn_calldescr = cpu._calldescr_dynamic_for_tests(
                [ffi_type, ffi_type], ffi_type)
            res = self.execute_operation(rop.CALL,
                                         [funcbox, BoxInt(num), BoxInt(num)],
                                         'int', descr=dyn_calldescr)
            assert res.value == 2 * num

            # last, try it with one constant argument
            calldescr = cpu.calldescrof(FUNC, FUNC.ARGS, FUNC.RESULT, EffectInfo.MOST_GENERAL)
            res = self.execute_operation(rop.CALL,
                                         [funcbox, ConstInt(num), BoxInt(num)],
                                         'int', descr=calldescr)
            assert res.value == 2 * num

        if cpu.supports_floats:
            def func(f0, f1, f2, f3, f4, f5, f6, i0, f7, i1, f8, f9):
                return f0 + f1 + f2 + f3 + f4 + f5 + f6 + float(i0 + i1) + f7 + f8 + f9
            F = lltype.Float
            I = lltype.Signed
            FUNC = self.FuncType([F] * 7 + [I] + [F] + [I] + [F]* 2, F)
            FPTR = self.Ptr(FUNC)
            func_ptr = llhelper(FPTR, func)
            calldescr = cpu.calldescrof(FUNC, FUNC.ARGS, FUNC.RESULT,
                                        EffectInfo.MOST_GENERAL)
            funcbox = self.get_funcbox(cpu, func_ptr)
            args = ([boxfloat(.1) for i in range(7)] +
                    [BoxInt(1), boxfloat(.2), BoxInt(2), boxfloat(.3),
                     boxfloat(.4)])
            res = self.execute_operation(rop.CALL,
                                         [funcbox] + args,
                                         'float', descr=calldescr)
            assert abs(res.getfloat() - 4.6) < 0.0001

    def test_call_many_arguments(self):
        # Test calling a function with a large number of arguments (more than
        # 6, which will force passing some arguments on the stack on 64-bit)

        def func(*args):
            assert len(args) == 16
            # Try to sum up args in a way that would probably detect a
            # transposed argument
            return sum(arg * (2**i) for i, arg in enumerate(args))

        FUNC = self.FuncType([lltype.Signed]*16, lltype.Signed)
        FPTR = self.Ptr(FUNC)
        calldescr = self.cpu.calldescrof(FUNC, FUNC.ARGS, FUNC.RESULT,
                                         EffectInfo.MOST_GENERAL)
        func_ptr = llhelper(FPTR, func)
        args = range(16)
        funcbox = self.get_funcbox(self.cpu, func_ptr)
        res = self.execute_operation(rop.CALL, [funcbox] + map(BoxInt, args), 'int', descr=calldescr)
        assert res.value == func(*args)

    def test_call_box_func(self):
        def a(a1, a2):
            return a1 + a2
        def b(b1, b2):
            return b1 * b2

        arg1 = 40
        arg2 = 2
        for f in [a, b]:
            TP = lltype.Signed
            FPTR = self.Ptr(self.FuncType([TP, TP], TP))
            func_ptr = llhelper(FPTR, f)
            FUNC = deref(FPTR)
            funcconst = self.get_funcbox(self.cpu, func_ptr)
            funcbox = funcconst.clonebox()
            calldescr = self.cpu.calldescrof(FUNC, FUNC.ARGS, FUNC.RESULT,
                                        EffectInfo.MOST_GENERAL)
            res = self.execute_operation(rop.CALL,
                                         [funcbox, BoxInt(arg1), BoxInt(arg2)],
                                         'int', descr=calldescr)
            assert res.getint() == f(arg1, arg2)

    def test_call_stack_alignment(self):
        # test stack alignment issues, notably for Mac OS/X.
        # also test the ordering of the arguments.

        def func_ints(*ints):
            s = str(ints) + '\n'
            os.write(1, s)   # don't remove -- crash if the stack is misaligned
            return sum([(10+i)*(5+j) for i, j in enumerate(ints)])

        for nb_args in range(0, 35):
            cpu = self.cpu
            TP = lltype.Signed
            #
            FPTR = self.Ptr(self.FuncType([TP] * nb_args, TP))
            func_ptr = llhelper(FPTR, func_ints)
            FUNC = deref(FPTR)
            calldescr = cpu.calldescrof(FUNC, FUNC.ARGS, FUNC.RESULT,
                                        EffectInfo.MOST_GENERAL)
            funcbox = self.get_funcbox(cpu, func_ptr)
            args = [280-24*i for i in range(nb_args)]
            res = self.execute_operation(rop.CALL,
                                         [funcbox] + map(BoxInt, args),
                                         'int', descr=calldescr)
            assert res.value == func_ints(*args)

    def test_call_with_const_floats(self):
        if not self.cpu.supports_floats:
            py.test.skip("requires floats")
        def func(f1, f2):
            return f1 + f2

        FUNC = self.FuncType([lltype.Float, lltype.Float], lltype.Float)
        FPTR = self.Ptr(FUNC)
        calldescr = self.cpu.calldescrof(FUNC, FUNC.ARGS, FUNC.RESULT,
                                         EffectInfo.MOST_GENERAL)
        func_ptr = llhelper(FPTR, func)
        funcbox = self.get_funcbox(self.cpu, func_ptr)
        res = self.execute_operation(rop.CALL, [funcbox, constfloat(1.5),
                                                constfloat(2.5)], 'float',
                                     descr=calldescr)
        assert res.getfloat() == 4.0


    def test_field_basic(self):
        t_box, T_box = self.alloc_instance(self.T)
        fielddescr = self.cpu.fielddescrof(self.S, 'value')
        assert not fielddescr.is_pointer_field()
        #
        res = self.execute_operation(rop.SETFIELD_GC, [t_box, BoxInt(39082)],
                                     'void', descr=fielddescr)
        assert res is None
        res = self.execute_operation(rop.GETFIELD_GC, [t_box],
                                     'int', descr=fielddescr)
        assert res.value == 39082
        #
        fielddescr1 = self.cpu.fielddescrof(self.S, 'chr1')
        fielddescr2 = self.cpu.fielddescrof(self.S, 'chr2')
        shortdescr = self.cpu.fielddescrof(self.S, 'short')
        self.execute_operation(rop.SETFIELD_GC, [t_box, BoxInt(250)],
                               'void', descr=fielddescr2)
        self.execute_operation(rop.SETFIELD_GC, [t_box, BoxInt(133)],
                               'void', descr=fielddescr1)
        self.execute_operation(rop.SETFIELD_GC, [t_box, BoxInt(1331)],
                               'void', descr=shortdescr)
        res = self.execute_operation(rop.GETFIELD_GC, [t_box],
                                     'int', descr=fielddescr2)
        assert res.value == 250
        res = self.execute_operation(rop.GETFIELD_GC, [t_box],
                                     'int', descr=fielddescr1)
        assert res.value == 133
        res = self.execute_operation(rop.GETFIELD_GC, [t_box],
                                     'int', descr=shortdescr)
        assert res.value == 1331

        #
        u_box, U_box = self.alloc_instance(self.U)
        fielddescr2 = self.cpu.fielddescrof(self.S, 'next')
        assert fielddescr2.is_pointer_field()
        res = self.execute_operation(rop.SETFIELD_GC, [t_box, u_box],
                                     'void', descr=fielddescr2)
        assert res is None
        res = self.execute_operation(rop.GETFIELD_GC, [t_box],
                                     'ref', descr=fielddescr2)
        assert res.value == u_box.value
        #
        null_const = self.null_instance().constbox()
        res = self.execute_operation(rop.SETFIELD_GC, [t_box, null_const],
                                     'void', descr=fielddescr2)
        assert res is None
        res = self.execute_operation(rop.GETFIELD_GC, [t_box],
                                     'ref', descr=fielddescr2)
        assert res.value == null_const.value
        if self.cpu.supports_floats:
            floatdescr = self.cpu.fielddescrof(self.S, 'float')
            self.execute_operation(rop.SETFIELD_GC, [t_box, boxfloat(3.4)],
                                   'void', descr=floatdescr)
            res = self.execute_operation(rop.GETFIELD_GC, [t_box],
                                         'float', descr=floatdescr)
            assert res.getfloat() == 3.4
            #
            self.execute_operation(rop.SETFIELD_GC, [t_box, constfloat(-3.6)],
                                   'void', descr=floatdescr)
            res = self.execute_operation(rop.GETFIELD_GC, [t_box],
                                         'float', descr=floatdescr)
            assert res.getfloat() == -3.6


    def test_passing_guards(self):
        t_box, T_box = self.alloc_instance(self.T)
        nullbox = self.null_instance()
        all = [(rop.GUARD_TRUE, [BoxInt(1)]),
               (rop.GUARD_FALSE, [BoxInt(0)]),
               (rop.GUARD_VALUE, [BoxInt(42), ConstInt(42)]),
               ]
        if not self.avoid_instances:
            all.extend([
               (rop.GUARD_NONNULL, [t_box]),
               (rop.GUARD_ISNULL, [nullbox])
               ])
        if self.cpu.supports_floats:
            all.append((rop.GUARD_VALUE, [boxfloat(3.5), constfloat(3.5)]))
        for (opname, args) in all:
            assert self.execute_operation(opname, args, 'void') == None
            assert not self.guard_failed


    def test_passing_guard_class(self):
        t_box, T_box = self.alloc_instance(self.T)
        #null_box = ConstPtr(lltype.cast_opaque_ptr(llmemory.GCREF, lltype.nullptr(T)))
        self.execute_operation(rop.GUARD_CLASS, [t_box, T_box], 'void')
        assert not self.guard_failed
        self.execute_operation(rop.GUARD_NONNULL_CLASS, [t_box, T_box], 'void')
        assert not self.guard_failed

    def test_failing_guards(self):
        t_box, T_box = self.alloc_instance(self.T)
        nullbox = self.null_instance()
        all = [(rop.GUARD_TRUE, [BoxInt(0)]),
               (rop.GUARD_FALSE, [BoxInt(1)]),
               (rop.GUARD_VALUE, [BoxInt(42), ConstInt(41)]),
               ]
        if not self.avoid_instances:
            all.extend([
               (rop.GUARD_NONNULL, [nullbox]),
               (rop.GUARD_ISNULL, [t_box])])
        if self.cpu.supports_floats:
            all.append((rop.GUARD_VALUE, [boxfloat(-1.0), constfloat(1.0)]))
        for opname, args in all:
            assert self.execute_operation(opname, args, 'void') == None
            assert self.guard_failed

    def test_failing_guard_class(self):
        t_box, T_box = self.alloc_instance(self.T)
        u_box, U_box = self.alloc_instance(self.U)
        null_box = self.null_instance()
        for opname, args in [(rop.GUARD_CLASS, [t_box, U_box]),
                             (rop.GUARD_CLASS, [u_box, T_box]),
                             (rop.GUARD_NONNULL_CLASS, [t_box, U_box]),
                             (rop.GUARD_NONNULL_CLASS, [u_box, T_box]),
                             (rop.GUARD_NONNULL_CLASS, [null_box, T_box]),
                             ]:
            assert self.execute_operation(opname, args, 'void') == None
            assert self.guard_failed

    def test_ooops(self):
        u1_box, U_box = self.alloc_instance(self.U)
        u2_box, U_box = self.alloc_instance(self.U)
        r = self.execute_operation(rop.PTR_EQ, [u1_box,
                                                u1_box.clonebox()], 'int')
        assert r.value == 1
        r = self.execute_operation(rop.PTR_NE, [u2_box,
                                                u2_box.clonebox()], 'int')
        assert r.value == 0
        r = self.execute_operation(rop.PTR_EQ, [u1_box, u2_box], 'int')
        assert r.value == 0
        r = self.execute_operation(rop.PTR_NE, [u2_box, u1_box], 'int')
        assert r.value == 1
        #
        null_box = self.null_instance()
        r = self.execute_operation(rop.PTR_EQ, [null_box,
                                                null_box.clonebox()], 'int')
        assert r.value == 1
        r = self.execute_operation(rop.PTR_EQ, [u1_box, null_box], 'int')
        assert r.value == 0
        r = self.execute_operation(rop.PTR_EQ, [null_box, u2_box], 'int')
        assert r.value == 0
        r = self.execute_operation(rop.PTR_NE, [null_box,
                                                null_box.clonebox()], 'int')
        assert r.value == 0
        r = self.execute_operation(rop.PTR_NE, [u2_box, null_box], 'int')
        assert r.value == 1
        r = self.execute_operation(rop.PTR_NE, [null_box, u1_box], 'int')
        assert r.value == 1

        # These operations are supposed to be the same as PTR_EQ/PTR_NE
        # just checking that the operations are defined in the backend.
        r = self.execute_operation(rop.INSTANCE_PTR_EQ, [u1_box, u2_box], 'int')
        assert r.value == 0
        r = self.execute_operation(rop.INSTANCE_PTR_NE, [u2_box, u1_box], 'int')
        assert r.value == 1

    def test_array_basic(self):
        a_box, A = self.alloc_array_of(rffi.SHORT, 342)
        arraydescr = self.cpu.arraydescrof(A)
        assert not arraydescr.is_array_of_pointers()
        #
        r = self.execute_operation(rop.ARRAYLEN_GC, [a_box],
                                   'int', descr=arraydescr)
        assert r.value == 342
        r = self.execute_operation(rop.SETARRAYITEM_GC, [a_box, BoxInt(310),
                                                         BoxInt(744)],
                                   'void', descr=arraydescr)
        assert r is None
        r = self.execute_operation(rop.GETARRAYITEM_GC, [a_box, BoxInt(310)],
                                   'int', descr=arraydescr)
        assert r.value == 744

        a_box, A = self.alloc_array_of(lltype.Signed, 342)
        arraydescr = self.cpu.arraydescrof(A)
        assert not arraydescr.is_array_of_pointers()
        #
        r = self.execute_operation(rop.ARRAYLEN_GC, [a_box],
                                   'int', descr=arraydescr)
        assert r.value == 342
        r = self.execute_operation(rop.SETARRAYITEM_GC, [a_box, BoxInt(310),
                                                         BoxInt(7441)],
                                   'void', descr=arraydescr)
        assert r is None
        r = self.execute_operation(rop.GETARRAYITEM_GC, [a_box, BoxInt(310)],
                                   'int', descr=arraydescr)
        assert r.value == 7441
        #
        a_box, A = self.alloc_array_of(lltype.Char, 11)
        arraydescr = self.cpu.arraydescrof(A)
        assert not arraydescr.is_array_of_pointers()
        r = self.execute_operation(rop.ARRAYLEN_GC, [a_box],
                                   'int', descr=arraydescr)
        assert r.value == 11
        r = self.execute_operation(rop.SETARRAYITEM_GC, [a_box, BoxInt(4),
                                                         BoxInt(150)],
                                   'void', descr=arraydescr)
        assert r is None
        r = self.execute_operation(rop.SETARRAYITEM_GC, [a_box, BoxInt(3),
                                                         BoxInt(160)],
                                   'void', descr=arraydescr)
        assert r is None
        r = self.execute_operation(rop.GETARRAYITEM_GC, [a_box, BoxInt(4)],
                                   'int', descr=arraydescr)
        assert r.value == 150
        r = self.execute_operation(rop.GETARRAYITEM_GC, [a_box, BoxInt(3)],
                                   'int', descr=arraydescr)
        assert r.value == 160

        #
        if isinstance(A, lltype.GcArray):
            A = lltype.Ptr(A)
        b_box, B = self.alloc_array_of(A, 3)
        arraydescr = self.cpu.arraydescrof(B)
        assert arraydescr.is_array_of_pointers()
        r = self.execute_operation(rop.ARRAYLEN_GC, [b_box],
                                   'int', descr=arraydescr)
        assert r.value == 3
        r = self.execute_operation(rop.SETARRAYITEM_GC, [b_box, BoxInt(1),
                                                         a_box],
                                   'void', descr=arraydescr)
        assert r is None
        r = self.execute_operation(rop.GETARRAYITEM_GC, [b_box, BoxInt(1)],
                                   'ref', descr=arraydescr)
        assert r.value == a_box.value
        #
        # Unsigned should work the same as Signed
        a_box, A = self.alloc_array_of(lltype.Unsigned, 342)
        arraydescr = self.cpu.arraydescrof(A)
        assert not arraydescr.is_array_of_pointers()
        r = self.execute_operation(rop.ARRAYLEN_GC, [a_box],
                                   'int', descr=arraydescr)
        assert r.value == 342
        r = self.execute_operation(rop.SETARRAYITEM_GC, [a_box, BoxInt(310),
                                                         BoxInt(7441)],
                                   'void', descr=arraydescr)
        assert r is None
        r = self.execute_operation(rop.GETARRAYITEM_GC, [a_box, BoxInt(310)],
                                   'int', descr=arraydescr)
        assert r.value == 7441
        #
        # Bool should work the same as Char
        a_box, A = self.alloc_array_of(lltype.Bool, 311)
        arraydescr = self.cpu.arraydescrof(A)
        assert not arraydescr.is_array_of_pointers()
        r = self.execute_operation(rop.ARRAYLEN_GC, [a_box],
                                   'int', descr=arraydescr)
        assert r.value == 311
        r = self.execute_operation(rop.SETARRAYITEM_GC, [a_box, BoxInt(304),
                                                         BoxInt(1)],
                                   'void', descr=arraydescr)
        assert r is None
        r = self.execute_operation(rop.SETARRAYITEM_GC, [a_box, BoxInt(303),
                                                         BoxInt(0)],
                                   'void', descr=arraydescr)
        assert r is None
        r = self.execute_operation(rop.SETARRAYITEM_GC, [a_box, BoxInt(302),
                                                         BoxInt(1)],
                                   'void', descr=arraydescr)
        assert r is None
        r = self.execute_operation(rop.GETARRAYITEM_GC, [a_box, BoxInt(304)],
                                   'int', descr=arraydescr)
        assert r.value == 1
        r = self.execute_operation(rop.GETARRAYITEM_GC, [a_box, BoxInt(303)],
                                   'int', descr=arraydescr)
        assert r.value == 0
        r = self.execute_operation(rop.GETARRAYITEM_GC, [a_box, BoxInt(302)],
                                   'int', descr=arraydescr)
        assert r.value == 1

        if self.cpu.supports_floats:
            a_box, A = self.alloc_array_of(lltype.Float, 31)
            arraydescr = self.cpu.arraydescrof(A)
            self.execute_operation(rop.SETARRAYITEM_GC, [a_box, BoxInt(1),
                                                         boxfloat(3.5)],
                                   'void', descr=arraydescr)
            self.execute_operation(rop.SETARRAYITEM_GC, [a_box, BoxInt(2),
                                                         constfloat(4.5)],
                                   'void', descr=arraydescr)
            r = self.execute_operation(rop.GETARRAYITEM_GC, [a_box, BoxInt(1)],
                                       'float', descr=arraydescr)
            assert r.getfloat() == 3.5
            r = self.execute_operation(rop.GETARRAYITEM_GC, [a_box, BoxInt(2)],
                                       'float', descr=arraydescr)
            assert r.getfloat() == 4.5

        # For platforms where sizeof(INT) != sizeof(Signed) (ie, x86-64)
        a_box, A = self.alloc_array_of(rffi.INT, 342)
        arraydescr = self.cpu.arraydescrof(A)
        assert not arraydescr.is_array_of_pointers()
        r = self.execute_operation(rop.ARRAYLEN_GC, [a_box],
                                   'int', descr=arraydescr)
        assert r.value == 342
        r = self.execute_operation(rop.SETARRAYITEM_GC, [a_box, BoxInt(310),
                                                         BoxInt(7441)],
                                   'void', descr=arraydescr)
        assert r is None
        r = self.execute_operation(rop.GETARRAYITEM_GC, [a_box, BoxInt(310)],
                                   'int', descr=arraydescr)
        assert r.value == 7441

    def test_array_of_structs(self):
        TP = lltype.GcStruct('x')
        ITEM = lltype.Struct('x',
                             ('vs', lltype.Signed),
                             ('vu', lltype.Unsigned),
                             ('vsc', rffi.SIGNEDCHAR),
                             ('vuc', rffi.UCHAR),
                             ('vss', rffi.SHORT),
                             ('vus', rffi.USHORT),
                             ('vsi', rffi.INT),
                             ('vui', rffi.UINT),
                             ('k', lltype.Float),
                             ('p', lltype.Ptr(TP)))
        a_box, A = self.alloc_array_of(ITEM, 15)
        s_box, S = self.alloc_instance(TP)
        vsdescr = self.cpu.interiorfielddescrof(A, 'vs')
        kdescr = self.cpu.interiorfielddescrof(A, 'k')
        pdescr = self.cpu.interiorfielddescrof(A, 'p')
        self.execute_operation(rop.SETINTERIORFIELD_GC, [a_box, BoxInt(3),
                                                         boxfloat(1.5)],
                               'void', descr=kdescr)
        f = self.cpu.bh_getinteriorfield_gc_f(a_box.getref_base(), 3, kdescr)
        assert longlong.getrealfloat(f) == 1.5
        self.cpu.bh_setinteriorfield_gc_f(a_box.getref_base(), 3, longlong.getfloatstorage(2.5), kdescr)
        r = self.execute_operation(rop.GETINTERIORFIELD_GC, [a_box, BoxInt(3)],
                                   'float', descr=kdescr)
        assert r.getfloat() == 2.5
        #
        NUMBER_FIELDS = [('vs', lltype.Signed),
                         ('vu', lltype.Unsigned),
                         ('vsc', rffi.SIGNEDCHAR),
                         ('vuc', rffi.UCHAR),
                         ('vss', rffi.SHORT),
                         ('vus', rffi.USHORT),
                         ('vsi', rffi.INT),
                         ('vui', rffi.UINT)]
        for name, TYPE in NUMBER_FIELDS[::-1]:
            vdescr = self.cpu.interiorfielddescrof(A, name)
            self.execute_operation(rop.SETINTERIORFIELD_GC, [a_box, BoxInt(3),
                                                             BoxInt(-15)],
                                   'void', descr=vdescr)
        for name, TYPE in NUMBER_FIELDS:
            vdescr = self.cpu.interiorfielddescrof(A, name)
            i = self.cpu.bh_getinteriorfield_gc_i(a_box.getref_base(), 3,
                                                  vdescr)
            assert i == rffi.cast(lltype.Signed, rffi.cast(TYPE, -15))
        for name, TYPE in NUMBER_FIELDS[::-1]:
            vdescr = self.cpu.interiorfielddescrof(A, name)
            self.cpu.bh_setinteriorfield_gc_i(a_box.getref_base(), 3,
                                              -25, vdescr)
        for name, TYPE in NUMBER_FIELDS:
            vdescr = self.cpu.interiorfielddescrof(A, name)
            r = self.execute_operation(rop.GETINTERIORFIELD_GC,
                                       [a_box, BoxInt(3)],
                                       'int', descr=vdescr)
            assert r.getint() == rffi.cast(lltype.Signed, rffi.cast(TYPE, -25))
        #
        self.execute_operation(rop.SETINTERIORFIELD_GC, [a_box, BoxInt(4),
                                                         s_box],
                               'void', descr=pdescr)
        r = self.cpu.bh_getinteriorfield_gc_r(a_box.getref_base(), 4, pdescr)
        assert r == s_box.getref_base()
        self.cpu.bh_setinteriorfield_gc_r(a_box.getref_base(), 3,
                                          s_box.getref_base(), pdescr)
        r = self.execute_operation(rop.GETINTERIORFIELD_GC, [a_box, BoxInt(3)],
                                   'ref', descr=pdescr)
        assert r.getref_base() == s_box.getref_base()
        #
        # test a corner case that used to fail on x86
        i4 = BoxInt(4)
        self.execute_operation(rop.SETINTERIORFIELD_GC, [a_box, i4, i4],
                               'void', descr=vsdescr)
        r = self.cpu.bh_getinteriorfield_gc_i(a_box.getref_base(), 4, vsdescr)
        assert r == 4

    def test_array_of_structs_all_sizes(self):
        # x86 has special support that can be used for sizes
        #   1, 2, 3, 4, 5, 6, 8, 9, 10, 12, 16, 18, 20, 24, 32, 36, 40, 64, 72
        for length in range(1, 75):
            ITEM = lltype.FixedSizeArray(lltype.Char, length)
            a_box, A = self.alloc_array_of(ITEM, 5)
            a = a_box.getref(lltype.Ptr(A))
            middle = length // 2
            a[3][middle] = chr(65 + length)
            fdescr = self.cpu.interiorfielddescrof(A, 'item%d' % middle)
            r = self.execute_operation(rop.GETINTERIORFIELD_GC,
                                       [a_box, BoxInt(3)],
                                       'int', descr=fdescr)
            r = r.getint()
            assert r == 65 + length
            self.execute_operation(rop.SETINTERIORFIELD_GC,
                                   [a_box, BoxInt(2), BoxInt(r + 1)],
                                   'void', descr=fdescr)
            r1 = self.cpu.bh_getinteriorfield_gc_i(a_box.getref_base(), 2,
                                                  fdescr)
            assert r1 == r + 1

    def test_string_basic(self):
        s_box = self.alloc_string("hello\xfe")
        r = self.execute_operation(rop.STRLEN, [s_box], 'int')
        assert r.value == 6
        r = self.execute_operation(rop.STRGETITEM, [s_box, BoxInt(5)], 'int')
        assert r.value == 254
        r = self.execute_operation(rop.STRSETITEM, [s_box, BoxInt(4),
                                                    BoxInt(153)], 'void')
        assert r is None
        r = self.execute_operation(rop.STRGETITEM, [s_box, BoxInt(5)], 'int')
        assert r.value == 254
        r = self.execute_operation(rop.STRGETITEM, [s_box, BoxInt(4)], 'int')
        assert r.value == 153

    def test_copystrcontent(self):
        s_box = self.alloc_string("abcdef")
        for s_box in [s_box, s_box.constbox()]:
            for srcstart_box in [BoxInt(2), ConstInt(2)]:
                for dststart_box in [BoxInt(3), ConstInt(3)]:
                    for length_box in [BoxInt(4), ConstInt(4)]:
                        for r_box_is_const in [False, True]:
                            r_box = self.alloc_string("!???????!")
                            if r_box_is_const:
                                r_box = r_box.constbox()
                                self.execute_operation(rop.COPYSTRCONTENT,
                                                       [s_box, r_box,
                                                        srcstart_box,
                                                        dststart_box,
                                                        length_box], 'void')
                                assert self.look_string(r_box) == "!??cdef?!"

    def test_copyunicodecontent(self):
        s_box = self.alloc_unicode(u"abcdef")
        for s_box in [s_box, s_box.constbox()]:
            for srcstart_box in [BoxInt(2), ConstInt(2)]:
                for dststart_box in [BoxInt(3), ConstInt(3)]:
                    for length_box in [BoxInt(4), ConstInt(4)]:
                        for r_box_is_const in [False, True]:
                            r_box = self.alloc_unicode(u"!???????!")
                            if r_box_is_const:
                                r_box = r_box.constbox()
                                self.execute_operation(rop.COPYUNICODECONTENT,
                                                       [s_box, r_box,
                                                        srcstart_box,
                                                        dststart_box,
                                                        length_box], 'void')
                                assert self.look_unicode(r_box) == u"!??cdef?!"

    def test_do_unicode_basic(self):
        u = self.cpu.bh_newunicode(5)
        self.cpu.bh_unicodesetitem(u, 4, 123)
        r = self.cpu.bh_unicodegetitem(u, 4)
        assert r == 123

    def test_unicode_basic(self):
        u_box = self.alloc_unicode(u"hello\u1234")
        r = self.execute_operation(rop.UNICODELEN, [u_box], 'int')
        assert r.value == 6
        r = self.execute_operation(rop.UNICODEGETITEM, [u_box, BoxInt(5)],
                                   'int')
        assert r.value == 0x1234
        r = self.execute_operation(rop.UNICODESETITEM, [u_box, BoxInt(4),
                                                        BoxInt(31313)], 'void')
        assert r is None
        r = self.execute_operation(rop.UNICODEGETITEM, [u_box, BoxInt(5)],
                                   'int')
        assert r.value == 0x1234
        r = self.execute_operation(rop.UNICODEGETITEM, [u_box, BoxInt(4)],
                                   'int')
        assert r.value == 31313

    def test_same_as(self):
        r = self.execute_operation(rop.SAME_AS, [ConstInt(5)], 'int')
        assert r.value == 5
        r = self.execute_operation(rop.SAME_AS, [BoxInt(5)], 'int')
        assert r.value == 5
        u_box = self.alloc_unicode(u"hello\u1234")
        r = self.execute_operation(rop.SAME_AS, [u_box.constbox()], 'ref')
        assert r.value == u_box.value
        r = self.execute_operation(rop.SAME_AS, [u_box], 'ref')
        assert r.value == u_box.value

        if self.cpu.supports_floats:
            r = self.execute_operation(rop.SAME_AS, [constfloat(5.5)], 'float')
            assert r.getfloat() == 5.5
            r = self.execute_operation(rop.SAME_AS, [boxfloat(5.5)], 'float')
            assert r.getfloat() == 5.5

    def test_virtual_ref(self):
        pass   # VIRTUAL_REF must not reach the backend nowadays

    def test_virtual_ref_finish(self):
        pass   # VIRTUAL_REF_FINISH must not reach the backend nowadays

    def test_arguments_to_execute_token(self):
        # this test checks that execute_token() can be called with any
        # variant of ints and floats as arguments
        if self.cpu.supports_floats:
            numkinds = 2
        else:
            numkinds = 1
        seed = random.randrange(0, 10000)
        print 'Seed is', seed    # or choose it by changing the previous line
        r = random.Random()
        r.seed(seed)
        for nb_args in range(50):
            print 'Passing %d arguments to execute_token...' % nb_args
            #
            inputargs = []
            values = []
            for k in range(nb_args):
                kind = r.randrange(0, numkinds)
                if kind == 0:
                    inputargs.append(BoxInt())
                    values.append(r.randrange(-100000, 100000))
                else:
                    inputargs.append(BoxFloat())
                    values.append(longlong.getfloatstorage(r.random()))
            #
            looptoken = JitCellToken()
            guarddescr = BasicFailDescr(42)
            faildescr = BasicFinalDescr(43)
            operations = []
            retboxes = []
            retvalues = []
            #
            ks = range(nb_args)
            random.shuffle(ks)
            for k in ks:
                if isinstance(inputargs[k], BoxInt):
                    newbox = BoxInt()
                    x = r.randrange(-100000, 100000)
                    operations.append(
                        ResOperation(rop.INT_ADD, [inputargs[k],
                                                   ConstInt(x)], newbox)
                        )
                    y = values[k] + x
                else:
                    newbox = BoxFloat()
                    x = r.random()
                    operations.append(
                        ResOperation(rop.FLOAT_ADD, [inputargs[k],
                                                     constfloat(x)], newbox)
                        )
                    y = longlong.getrealfloat(values[k]) + x
                    y = longlong.getfloatstorage(y)
                kk = r.randrange(0, len(retboxes)+1)
                retboxes.insert(kk, newbox)
                retvalues.insert(kk, y)
            #
            zero = BoxInt()
            operations.extend([
                ResOperation(rop.SAME_AS, [ConstInt(0)], zero),
                ResOperation(rop.GUARD_TRUE, [zero], None, descr=guarddescr),
                ResOperation(rop.FINISH, [], None, descr=faildescr)
                ])
            operations[-2].setfailargs(retboxes)
            print inputargs
            for op in operations:
                print op
            self.cpu.compile_loop(inputargs, operations, looptoken)
            #
            deadframe = self.cpu.execute_token(looptoken, *values)
            fail = self.cpu.get_latest_descr(deadframe)
            assert fail.identifier == 42
            #
            for k in range(len(retvalues)):
                if isinstance(retboxes[k], BoxInt):
                    got = self.cpu.get_int_value(deadframe, k)
                else:
                    got = self.cpu.get_float_value(deadframe, k)
                assert got == retvalues[k]

    def test_jump(self):
        # this test generates small loops where the JUMP passes many
        # arguments of various types, shuffling them around.
        if self.cpu.supports_floats:
            numkinds = 3
        else:
            numkinds = 2
        seed = random.randrange(0, 10000)
        print 'Seed is', seed    # or choose it by changing the previous line
        r = random.Random()
        r.seed(seed)
        for nb_args in range(50):
            print 'Passing %d arguments around...' % nb_args
            #
            inputargs = []
            for k in range(nb_args):
                kind = r.randrange(0, numkinds)
                if kind == 0:
                    inputargs.append(BoxInt())
                elif kind == 1:
                    inputargs.append(BoxPtr())
                else:
                    inputargs.append(BoxFloat())
            jumpargs = []
            remixing = []
            for srcbox in inputargs:
                n = r.randrange(0, len(inputargs))
                otherbox = inputargs[n]
                if otherbox.type == srcbox.type:
                    remixing.append((srcbox, otherbox))
                else:
                    otherbox = srcbox
                jumpargs.append(otherbox)
            #
            index_counter = r.randrange(0, len(inputargs)+1)
            i0 = BoxInt()
            i1 = BoxInt()
            i2 = BoxInt()
            inputargs.insert(index_counter, i0)
            jumpargs.insert(index_counter, i1)
            #
            looptoken = JitCellToken()
            targettoken = TargetToken()
            faildescr = BasicFailDescr(15)
            operations = [
                ResOperation(rop.LABEL, inputargs, None, descr=targettoken),
                ResOperation(rop.INT_SUB, [i0, ConstInt(1)], i1),
                ResOperation(rop.INT_GE, [i1, ConstInt(0)], i2),
                ResOperation(rop.GUARD_TRUE, [i2], None),
                ResOperation(rop.JUMP, jumpargs, None, descr=targettoken),
                ]
            operations[3].setfailargs(inputargs[:])
            operations[3].setdescr(faildescr)
            #
            self.cpu.compile_loop(inputargs, operations, looptoken)
            #
            values = []
            S = lltype.GcStruct('S')
            for box in inputargs:
                if isinstance(box, BoxInt):
                    values.append(r.randrange(-10000, 10000))
                elif isinstance(box, BoxPtr):
                    p = lltype.malloc(S)
                    values.append(lltype.cast_opaque_ptr(llmemory.GCREF, p))
                elif isinstance(box, BoxFloat):
                    values.append(longlong.getfloatstorage(r.random()))
                else:
                    assert 0
            values[index_counter] = 11
            #
            deadframe = self.cpu.execute_token(looptoken, *values)
            fail = self.cpu.get_latest_descr(deadframe)
            assert fail.identifier == 15
            #
            dstvalues = values[:]
            for _ in range(11):
                expected = dstvalues[:]
                for tgtbox, srcbox in remixing:
                    v = dstvalues[inputargs.index(srcbox)]
                    expected[inputargs.index(tgtbox)] = v
                dstvalues = expected
            #
            assert dstvalues[index_counter] == 11
            dstvalues[index_counter] = 0
            for i, (box, val) in enumerate(zip(inputargs, dstvalues)):
                if isinstance(box, BoxInt):
                    got = self.cpu.get_int_value(deadframe, i)
                elif isinstance(box, BoxPtr):
                    got = self.cpu.get_ref_value(deadframe, i)
                elif isinstance(box, BoxFloat):
                    got = self.cpu.get_float_value(deadframe, i)
                else:
                    assert 0
                assert type(got) == type(val)
                assert got == val

    def test_compile_bridge_float(self):
        if not self.cpu.supports_floats:
            py.test.skip("requires floats")
        fboxes = [BoxFloat() for i in range(12)]
        i2 = BoxInt()
        targettoken = TargetToken()
        faildescr1 = BasicFailDescr(1)
        faildescr2 = BasicFailDescr(2)
        faildescr3 = BasicFinalDescr(3)
        operations = [
            ResOperation(rop.LABEL, fboxes, None, descr=targettoken),
            ResOperation(rop.FLOAT_LE, [fboxes[0], constfloat(9.2)], i2),
            ResOperation(rop.GUARD_TRUE, [i2], None, descr=faildescr1),
            ResOperation(rop.GUARD_FALSE, [i2], None, descr=faildescr2),
            ResOperation(rop.FINISH, [], None, descr=faildescr3),
            ]
        operations[-3].setfailargs(fboxes)
        operations[-2].setfailargs(fboxes)
        looptoken = JitCellToken()
        self.cpu.compile_loop(fboxes, operations, looptoken)

        fboxes2 = [BoxFloat() for i in range(12)]
        f3 = BoxFloat()
        bridge = [
            ResOperation(rop.FLOAT_SUB, [fboxes2[0], constfloat(1.0)], f3),
            ResOperation(rop.JUMP, [f3]+fboxes2[1:], None, descr=targettoken),
        ]

        self.cpu.compile_bridge(faildescr1, fboxes2, bridge, looptoken)

        args = []
        for i in range(len(fboxes)):
            x = 13.5 + 6.73 * i
            args.append(longlong.getfloatstorage(x))
        deadframe = self.cpu.execute_token(looptoken, *args)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == 2
        res = self.cpu.get_float_value(deadframe, 0)
        assert longlong.getrealfloat(res) == 8.5
        for i in range(1, len(fboxes)):
            got = longlong.getrealfloat(self.cpu.get_float_value(
                deadframe, i))
            assert got == 13.5 + 6.73 * i

    def test_compile_bridge_spilled_float(self):
        if not self.cpu.supports_floats:
            py.test.skip("requires floats")
        fboxes = [BoxFloat() for i in range(3)]
        faildescr1 = BasicFailDescr(100)
        faildescr2 = BasicFinalDescr(102)
        loopops = """
        [i0,f1, f2]
        f3 = float_add(f1, f2)
        force_spill(f3)
        force_spill(f1)
        force_spill(f2)
        guard_false(i0) [f1, f2, f3]
        finish()"""
        loop = parse(loopops)
        looptoken = JitCellToken()
        self.cpu.compile_loop(loop.inputargs, loop.operations, looptoken)
        args = [1]
        args.append(longlong.getfloatstorage(132.25))
        args.append(longlong.getfloatstorage(0.75))
        deadframe = self.cpu.execute_token(looptoken, *args)  #xxx check
        fail = self.cpu.get_latest_descr(deadframe)
        assert loop.operations[-2].getdescr() == fail
        f1 = self.cpu.get_float_value(deadframe, 0)
        f2 = self.cpu.get_float_value(deadframe, 1)
        f3 = self.cpu.get_float_value(deadframe, 2)
        assert longlong.getrealfloat(f1) == 132.25
        assert longlong.getrealfloat(f2) == 0.75
        assert longlong.getrealfloat(f3) == 133.0

        zero = BoxInt()
        bridgeops = [
            ResOperation(rop.SAME_AS, [ConstInt(0)], zero),
            ResOperation(rop.GUARD_TRUE, [zero], None, descr=faildescr1),
            ResOperation(rop.FINISH, [], None, descr=faildescr2),
            ]
        bridgeops[-2].setfailargs(fboxes[:])
        self.cpu.compile_bridge(loop.operations[-2].getdescr(), fboxes,
                                                        bridgeops, looptoken)
        args = [1,
                longlong.getfloatstorage(132.25),
                longlong.getfloatstorage(0.75)]
        deadframe = self.cpu.execute_token(looptoken, *args)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == 100
        f1 = self.cpu.get_float_value(deadframe, 0)
        f2 = self.cpu.get_float_value(deadframe, 1)
        f3 = self.cpu.get_float_value(deadframe, 2)
        assert longlong.getrealfloat(f1) == 132.25
        assert longlong.getrealfloat(f2) == 0.75
        assert longlong.getrealfloat(f3) == 133.0

    def test_integers_and_guards2(self):
        for opname, compare in [
            (rop.INT_IS_TRUE, lambda x: bool(x)),
            (rop.INT_IS_ZERO, lambda x: not bool(x))]:
            for opguard, guard_case in [
                (rop.GUARD_FALSE, False),
                (rop.GUARD_TRUE,  True),
                ]:
                box = BoxInt()
                res = BoxInt()
                faildescr1 = BasicFailDescr(1)
                faildescr2 = BasicFinalDescr(2)
                inputargs = [box]
                operations = [
                    ResOperation(opname, [box], res),
                    ResOperation(opguard, [res], None, descr=faildescr1),
                    ResOperation(rop.FINISH, [], None, descr=faildescr2),
                    ]
                operations[1].setfailargs([])
                looptoken = JitCellToken()
                self.cpu.compile_loop(inputargs, operations, looptoken)
                #
                for value in [-42, 0, 1, 10]:
                    deadframe = self.cpu.execute_token(looptoken, value)
                    fail = self.cpu.get_latest_descr(deadframe)
                    #
                    expected = compare(value)
                    expected ^= guard_case
                    assert fail.identifier == 2 - expected

    def test_integers_and_guards(self):
        for opname, compare in [
            (rop.INT_LT, lambda x, y: x < y),
            (rop.INT_LE, lambda x, y: x <= y),
            (rop.INT_EQ, lambda x, y: x == y),
            (rop.INT_NE, lambda x, y: x != y),
            (rop.INT_GT, lambda x, y: x > y),
            (rop.INT_GE, lambda x, y: x >= y),
            ]:
            for opguard, guard_case in [
                (rop.GUARD_FALSE, False),
                (rop.GUARD_TRUE,  True),
                ]:
                for combinaison in ["bb", "bc", "cb"]:
                    #
                    if combinaison[0] == 'b':
                        ibox1 = BoxInt()
                    else:
                        ibox1 = ConstInt(-42)
                    if combinaison[1] == 'b':
                        ibox2 = BoxInt()
                    else:
                        ibox2 = ConstInt(-42)
                    b1 = BoxInt()
                    faildescr1 = BasicFailDescr(1)
                    faildescr2 = BasicFinalDescr(2)
                    inputargs = [ib for ib in [ibox1, ibox2]
                                    if isinstance(ib, BoxInt)]
                    operations = [
                        ResOperation(opname, [ibox1, ibox2], b1),
                        ResOperation(opguard, [b1], None, descr=faildescr1),
                        ResOperation(rop.FINISH, [], None, descr=faildescr2),
                        ]
                    operations[-2].setfailargs([])
                    looptoken = JitCellToken()
                    self.cpu.compile_loop(inputargs, operations, looptoken)
                    #
                    for test1 in [-65, -42, -11, 0, 1, 10]:
                        if test1 == -42 or combinaison[0] == 'b':
                            for test2 in [-65, -42, -11, 0, 1, 10]:
                                if test2 == -42 or combinaison[1] == 'b':
                                    args = []
                                    if combinaison[0] == 'b':
                                        args.append(test1)
                                    if combinaison[1] == 'b':
                                        args.append(test2)
                                    deadframe = self.cpu.execute_token(
                                        looptoken, *args)
                                    fail = self.cpu.get_latest_descr(deadframe)
                                    #
                                    expected = compare(test1, test2)
                                    expected ^= guard_case
                                    assert fail.identifier == 2 - expected

    def test_integers_and_guards_uint(self):
        for opname, compare in [
            (rop.UINT_LE, lambda x, y: (x) <= (y)),
            (rop.UINT_GT, lambda x, y: (x) >  (y)),
            (rop.UINT_LT, lambda x, y: (x) <  (y)),
            (rop.UINT_GE, lambda x, y: (x) >= (y)),
            ]:
            for opguard, guard_case in [
                (rop.GUARD_FALSE, False),
                (rop.GUARD_TRUE,  True),
                ]:
                for combinaison in ["bb", "bc", "cb"]:
                    #
                    if combinaison[0] == 'b':
                        ibox1 = BoxInt()
                    else:
                        ibox1 = ConstInt(42)
                    if combinaison[1] == 'b':
                        ibox2 = BoxInt()
                    else:
                        ibox2 = ConstInt(42)
                    b1 = BoxInt()
                    faildescr1 = BasicFailDescr(1)
                    faildescr2 = BasicFinalDescr(2)
                    inputargs = [ib for ib in [ibox1, ibox2]
                                    if isinstance(ib, BoxInt)]
                    operations = [
                        ResOperation(opname, [ibox1, ibox2], b1),
                        ResOperation(opguard, [b1], None, descr=faildescr1),
                        ResOperation(rop.FINISH, [], None, descr=faildescr2),
                        ]
                    operations[-2].setfailargs([])
                    looptoken = JitCellToken()
                    self.cpu.compile_loop(inputargs, operations, looptoken)
                    #
                    for test1 in [65, 42, 11, 0, 1]:
                        if test1 == 42 or combinaison[0] == 'b':
                            for test2 in [65, 42, 11, 0, 1]:
                                if test2 == 42 or combinaison[1] == 'b':
                                    args = []
                                    if combinaison[0] == 'b':
                                        args.append(test1)
                                    if combinaison[1] == 'b':
                                        args.append(test2)
                                    deadframe = self.cpu.execute_token(
                                        looptoken, *args)
                                    fail = self.cpu.get_latest_descr(deadframe)
                                    #
                                    expected = compare(test1, test2)
                                    expected ^= guard_case
                                    assert fail.identifier == 2 - expected

    def test_floats_and_guards(self):
        if not self.cpu.supports_floats:
            py.test.skip("requires floats")
        for opname, compare in [
            (rop.FLOAT_LT, lambda x, y: x < y),
            (rop.FLOAT_LE, lambda x, y: x <= y),
            (rop.FLOAT_EQ, lambda x, y: x == y),
            (rop.FLOAT_NE, lambda x, y: x != y),
            (rop.FLOAT_GT, lambda x, y: x > y),
            (rop.FLOAT_GE, lambda x, y: x >= y),
            ]:
            for opguard, guard_case in [
                (rop.GUARD_FALSE, False),
                (rop.GUARD_TRUE,  True),
                ]:
                for combinaison in ["bb", "bc", "cb"]:
                    #
                    if combinaison[0] == 'b':
                        fbox1 = BoxFloat()
                    else:
                        fbox1 = constfloat(-4.5)
                    if combinaison[1] == 'b':
                        fbox2 = BoxFloat()
                    else:
                        fbox2 = constfloat(-4.5)
                    b1 = BoxInt()
                    faildescr1 = BasicFailDescr(1)
                    faildescr2 = BasicFinalDescr(2)
                    inputargs = [fb for fb in [fbox1, fbox2]
                                    if isinstance(fb, BoxFloat)]
                    operations = [
                        ResOperation(opname, [fbox1, fbox2], b1),
                        ResOperation(opguard, [b1], None, descr=faildescr1),
                        ResOperation(rop.FINISH, [], None, descr=faildescr2),
                        ]
                    operations[-2].setfailargs([])
                    looptoken = JitCellToken()
                    self.cpu.compile_loop(inputargs, operations, looptoken)
                    #
                    nan = 1e200 * 1e200
                    nan /= nan
                    for test1 in [-6.5, -4.5, -2.5, nan]:
                        if test1 == -4.5 or combinaison[0] == 'b':
                            for test2 in [-6.5, -4.5, -2.5, nan]:
                                if test2 == -4.5 or combinaison[1] == 'b':
                                    args = []
                                    if combinaison[0] == 'b':
                                        args.append(
                                            longlong.getfloatstorage(test1))
                                    if combinaison[1] == 'b':
                                        args.append(
                                            longlong.getfloatstorage(test2))
                                    deadframe = self.cpu.execute_token(
                                        looptoken, *args)
                                    fail = self.cpu.get_latest_descr(deadframe)
                                    #
                                    expected = compare(test1, test2)
                                    expected ^= guard_case
                                    assert fail.identifier == 2 - expected

    def test_unused_result_int(self):
        # test pure operations on integers whose result is not used
        from rpython.jit.metainterp.test.test_executor import get_int_tests
        int_tests = list(get_int_tests())
        int_tests = [(opnum, boxargs, 'int', retvalue)
                     for opnum, boxargs, retvalue in int_tests]
        self._test_unused_result(int_tests)

    def test_unused_result_float(self):
        # same as test_unused_result_int, for float operations
        from rpython.jit.metainterp.test.test_executor import get_float_tests
        float_tests = list(get_float_tests(self.cpu))
        self._test_unused_result(float_tests)

    def _test_unused_result(self, tests):
        while len(tests) > 50:     # only up to 50 tests at once
            self._test_unused_result(tests[:50])
            tests = tests[50:]
        inputargs = []
        operations = []
        for opnum, boxargs, rettype, retvalue in tests:
            inputargs += [box for box in boxargs if isinstance(box, Box)]
            if rettype == 'int':
                boxres = BoxInt()
            elif rettype == 'float':
                boxres = BoxFloat()
            else:
                assert 0
            operations.append(ResOperation(opnum, boxargs, boxres))
        # Unique-ify inputargs
        inputargs = list(set(inputargs))
        faildescr = BasicFinalDescr(1)
        operations.append(ResOperation(rop.FINISH, [], None,
                                       descr=faildescr))
        looptoken = JitCellToken()
        #
        self.cpu.compile_loop(inputargs, operations, looptoken)
        #
        args = []
        for box in inputargs:
            if isinstance(box, BoxInt):
                args.append(box.getint())
            elif isinstance(box, BoxFloat):
                args.append(box.getfloatstorage())
            else:
                assert 0
        #
        deadframe = self.cpu.execute_token(looptoken, *args)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == 1

    def test_nan_and_infinity(self):
        if not self.cpu.supports_floats:
            py.test.skip("requires floats")

        from rpython.rlib.rfloat import INFINITY, NAN, isinf, isnan
        from rpython.jit.metainterp.resoperation import opname

        fzer = boxfloat(0.0)
        fone = boxfloat(1.0)
        fmqr = boxfloat(-0.25)
        finf = boxfloat(INFINITY)
        fmnf = boxfloat(-INFINITY)
        fnan = boxfloat(NAN)

        all_cases_unary =  [(a,)   for a in [fzer,fone,fmqr,finf,fmnf,fnan]]
        all_cases_binary = [(a, b) for a in [fzer,fone,fmqr,finf,fmnf,fnan]
                                   for b in [fzer,fone,fmqr,finf,fmnf,fnan]]
        no_zero_divison  = [(a, b) for a in [fzer,fone,fmqr,finf,fmnf,fnan]
                                   for b in [     fone,fmqr,finf,fmnf,fnan]]

        def nan_and_infinity(opnum, realoperation, testcases):
            for testcase in testcases:
                realvalues = [b.getfloat() for b in testcase]
                expected = realoperation(*realvalues)
                if isinstance(expected, float):
                    expectedtype = 'float'
                else:
                    expectedtype = 'int'
                got = self.execute_operation(opnum, list(testcase),
                                             expectedtype)
                if isnan(expected):
                    ok = isnan(got.getfloat())
                elif isinf(expected):
                    ok = isinf(got.getfloat())
                elif isinstance(got, BoxFloat):
                    ok = (got.getfloat() == expected)
                else:
                    ok = got.value == expected
                if not ok:
                    raise AssertionError("%s(%s): got %r, expected %r" % (
                        opname[opnum], ', '.join(map(repr, realvalues)),
                        got.getfloat(), expected))
                # if we expect a boolean, also check the combination with
                # a GUARD_TRUE or GUARD_FALSE
                if isinstance(expected, bool):
                    for guard_opnum, expected_id in [(rop.GUARD_TRUE, 1),
                                                     (rop.GUARD_FALSE, 0)]:
                        box = BoxInt()
                        operations = [
                            ResOperation(opnum, list(testcase), box),
                            ResOperation(guard_opnum, [box], None,
                                         descr=BasicFailDescr(4)),
                            ResOperation(rop.FINISH, [], None,
                                         descr=BasicFinalDescr(5))]
                        operations[1].setfailargs([])
                        looptoken = JitCellToken()
                        # Use "set" to unique-ify inputargs
                        unique_testcase_list = list(set(testcase))
                        self.cpu.compile_loop(unique_testcase_list, operations,
                                              looptoken)
                        args = [box.getfloatstorage()
                                for box in unique_testcase_list]
                        deadframe = self.cpu.execute_token(looptoken, *args)
                        fail = self.cpu.get_latest_descr(deadframe)
                        if fail.identifier != 5 - (expected_id^expected):
                            if fail.identifier == 4:
                                msg = "was taken"
                            else:
                                msg = "was not taken"
                            raise AssertionError(
                                "%s(%s)/%s took the wrong path: "
                                "the failure path of the guard %s" % (
                                    opname[opnum],
                                    ', '.join(map(repr, realvalues)),
                                    opname[guard_opnum], msg))

        yield nan_and_infinity, rop.FLOAT_ADD, operator.add, all_cases_binary
        yield nan_and_infinity, rop.FLOAT_SUB, operator.sub, all_cases_binary
        yield nan_and_infinity, rop.FLOAT_MUL, operator.mul, all_cases_binary
        yield nan_and_infinity, rop.FLOAT_TRUEDIV, \
                                           operator.truediv, no_zero_divison
        yield nan_and_infinity, rop.FLOAT_NEG, operator.neg, all_cases_unary
        yield nan_and_infinity, rop.FLOAT_ABS, abs,          all_cases_unary
        yield nan_and_infinity, rop.FLOAT_LT,  operator.lt,  all_cases_binary
        yield nan_and_infinity, rop.FLOAT_LE,  operator.le,  all_cases_binary
        yield nan_and_infinity, rop.FLOAT_EQ,  operator.eq,  all_cases_binary
        yield nan_and_infinity, rop.FLOAT_NE,  operator.ne,  all_cases_binary
        yield nan_and_infinity, rop.FLOAT_GT,  operator.gt,  all_cases_binary
        yield nan_and_infinity, rop.FLOAT_GE,  operator.ge,  all_cases_binary

    def test_noops(self):
        c_box = self.alloc_string("hi there").constbox()
        c_nest = ConstInt(0)
        c_id = ConstInt(0)
        self.execute_operation(rop.DEBUG_MERGE_POINT, [c_box, c_nest, c_id, c_nest], 'void')
        self.execute_operation(rop.JIT_DEBUG, [c_box, c_nest, c_nest,
                                               c_nest, c_nest], 'void')

    def test_read_timestamp(self):
        if IS_32_BIT and not self.cpu.supports_longlong:
            py.test.skip("read_timestamp returns a longlong")
        if sys.platform == 'win32':
            # windows quite often is very inexact (like the old Intel 8259 PIC),
            # so we stretch the time a little bit.
            # On my virtual Parallels machine in a 2GHz Core i7 Mac Mini,
            # the test starts working at delay == 21670 and stops at 20600000.
            # We take the geometric mean value.
            from math import log, exp
            delay_min = 21670
            delay_max = 20600000
            delay = int(exp((log(delay_min)+log(delay_max))/2))
            def wait_a_bit():
                for i in xrange(delay): pass
        else:
            def wait_a_bit():
                pass

        from rpython.jit.codewriter.effectinfo import EffectInfo
        from rpython.rlib import rtimer

        effectinfo = EffectInfo([], [], [], [], [], [],
                                EffectInfo.EF_CANNOT_RAISE,
                                EffectInfo.OS_MATH_READ_TIMESTAMP)
        FPTR = self.Ptr(self.FuncType([], lltype.SignedLongLong))
        func_ptr = llhelper(FPTR, rtimer.read_timestamp)
        FUNC = deref(FPTR)
        funcbox = self.get_funcbox(self.cpu, func_ptr)

        calldescr = self.cpu.calldescrof(FUNC, FUNC.ARGS, FUNC.RESULT, effectinfo)
        if longlong.is_64_bit:
            got1 = self.execute_operation(rop.CALL, [funcbox], 'int', calldescr)
            wait_a_bit()
            got2 = self.execute_operation(rop.CALL, [funcbox], 'int', calldescr)
            res1 = got1.getint()
            res2 = got2.getint()
        else:
            got1 = self.execute_operation(rop.CALL, [funcbox],'float',calldescr)
            wait_a_bit()
            got2 = self.execute_operation(rop.CALL, [funcbox],'float',calldescr)
            res1 = got1.getlonglong()
            res2 = got2.getlonglong()
        assert res1 < res2 < res1 + 2**32


class LLtypeBackendTest(BaseBackendTest):

    type_system = 'lltype'
    Ptr = lltype.Ptr
    FuncType = lltype.FuncType
    malloc = staticmethod(lltype.malloc)
    nullptr = staticmethod(lltype.nullptr)

    @classmethod
    def get_funcbox(cls, cpu, func_ptr):
        addr = llmemory.cast_ptr_to_adr(func_ptr)
        return ConstInt(heaptracker.adr2int(addr))


    MY_VTABLE = rclass.OBJECT_VTABLE    # for tests only

    S = lltype.GcForwardReference()
    S.become(lltype.GcStruct('S', ('parent', rclass.OBJECT),
                                  ('value', lltype.Signed),
                                  ('chr1', lltype.Char),
                                  ('chr2', lltype.Char),
                                  ('short', rffi.SHORT),
                                  ('next', lltype.Ptr(S)),
                                  ('float', lltype.Float)))
    T = lltype.GcStruct('T', ('parent', S),
                             ('next', lltype.Ptr(S)))
    U = lltype.GcStruct('U', ('parent', T),
                             ('next', lltype.Ptr(S)))


    def alloc_instance(self, T):
        vtable_for_T = lltype.malloc(self.MY_VTABLE, immortal=True)
        vtable_for_T_addr = llmemory.cast_ptr_to_adr(vtable_for_T)
        cpu = self.cpu
        if not hasattr(cpu, '_cache_gcstruct2vtable'):
            cpu._cache_gcstruct2vtable = {}
        cpu._cache_gcstruct2vtable.update({T: vtable_for_T})
        t = lltype.malloc(T)
        if T == self.T:
            t.parent.parent.typeptr = vtable_for_T
        elif T == self.U:
            t.parent.parent.parent.typeptr = vtable_for_T
        t_box = BoxPtr(lltype.cast_opaque_ptr(llmemory.GCREF, t))
        T_box = ConstInt(heaptracker.adr2int(vtable_for_T_addr))
        return t_box, T_box

    def null_instance(self):
        return BoxPtr(lltype.nullptr(llmemory.GCREF.TO))

    def alloc_array_of(self, ITEM, length):
        A = lltype.GcArray(ITEM)
        a = lltype.malloc(A, length)
        a_box = BoxPtr(lltype.cast_opaque_ptr(llmemory.GCREF, a))
        return a_box, A

    def alloc_string(self, string):
        s = rstr.mallocstr(len(string))
        for i in range(len(string)):
            s.chars[i] = string[i]
        s_box = BoxPtr(lltype.cast_opaque_ptr(llmemory.GCREF, s))
        return s_box

    def look_string(self, string_box):
        s = string_box.getref(lltype.Ptr(rstr.STR))
        return ''.join(s.chars)

    def alloc_unicode(self, unicode):
        u = rstr.mallocunicode(len(unicode))
        for i in range(len(unicode)):
            u.chars[i] = unicode[i]
        u_box = BoxPtr(lltype.cast_opaque_ptr(llmemory.GCREF, u))
        return u_box

    def look_unicode(self, unicode_box):
        u = unicode_box.getref(lltype.Ptr(rstr.UNICODE))
        return u''.join(u.chars)


    def test_cast_int_to_ptr(self):
        res = self.execute_operation(rop.CAST_INT_TO_PTR,
                                     [BoxInt(-17)],  'ref').value
        assert lltype.cast_ptr_to_int(res) == -17

    def test_cast_ptr_to_int(self):
        x = lltype.cast_int_to_ptr(llmemory.GCREF, -19)
        res = self.execute_operation(rop.CAST_PTR_TO_INT,
                                     [BoxPtr(x)], 'int').value
        assert res == -19

    def test_cast_int_to_float(self):
        if not self.cpu.supports_floats:
            py.test.skip("requires floats")
        for x in [-10, -1, 0, 3, 42, sys.maxint-1]:
            res = self.execute_operation(rop.CAST_INT_TO_FLOAT,
                                         [BoxInt(x)],  'float').value
            assert longlong.getrealfloat(res) == float(x)
            # --- the front-end never generates CAST_INT_TO_FLOAT(Const)
            #res = self.execute_operation(rop.CAST_INT_TO_FLOAT,
            #                             [ConstInt(x)],  'float').value
            #assert longlong.getrealfloat(res) == float(x)

    def test_cast_float_to_int(self):
        if not self.cpu.supports_floats:
            py.test.skip("requires floats")
        for x in [-24.23, -5.3, 0.0, 3.1234, 11.1, 0.1]:
            v = longlong.getfloatstorage(x)
            res = self.execute_operation(rop.CAST_FLOAT_TO_INT,
                                         [BoxFloat(v)],  'int').value
            assert res == int(x)
            # --- the front-end never generates CAST_FLOAT_TO_INT(Const)
            #res = self.execute_operation(rop.CAST_FLOAT_TO_INT,
            #                             [ConstFloat(v)],  'int').value
            #assert res == int(x)

    def test_convert_float_bytes(self):
        if not self.cpu.supports_floats:
            py.test.skip("requires floats")
        if IS_32_BIT and not self.cpu.supports_longlong:
            py.test.skip("longlong test")
        t = 'int' if longlong.is_64_bit else 'float'
        res = self.execute_operation(rop.CONVERT_FLOAT_BYTES_TO_LONGLONG,
                                     [boxfloat(2.5)], t).value
        assert res == longlong2float.float2longlong(2.5)

        bytes = longlong2float.float2longlong(2.5)
        res = self.execute_operation(rop.CONVERT_LONGLONG_BYTES_TO_FLOAT,
                                     [boxlonglong(res)], 'float').value
        assert longlong.getrealfloat(res) == 2.5

    def test_ooops_non_gc(self):
        x = lltype.malloc(lltype.Struct('x'), flavor='raw')
        v = heaptracker.adr2int(llmemory.cast_ptr_to_adr(x))
        r = self.execute_operation(rop.PTR_EQ, [BoxInt(v), BoxInt(v)], 'int')
        assert r.value == 1
        r = self.execute_operation(rop.PTR_NE, [BoxInt(v), BoxInt(v)], 'int')
        assert r.value == 0
        lltype.free(x, flavor='raw')

    def test_new_plain_struct(self):
        cpu = self.cpu
        S = lltype.GcStruct('S', ('x', lltype.Char), ('y', lltype.Char))
        sizedescr = cpu.sizeof(S)
        r1 = self.execute_operation(rop.NEW, [], 'ref', descr=sizedescr)
        r2 = self.execute_operation(rop.NEW, [], 'ref', descr=sizedescr)
        assert r1.value != r2.value
        xdescr = cpu.fielddescrof(S, 'x')
        ydescr = cpu.fielddescrof(S, 'y')
        self.execute_operation(rop.SETFIELD_GC, [r1, BoxInt(150)],
                               'void', descr=ydescr)
        self.execute_operation(rop.SETFIELD_GC, [r1, BoxInt(190)],
                               'void', descr=xdescr)
        s = lltype.cast_opaque_ptr(lltype.Ptr(S), r1.value)
        assert s.x == chr(190)
        assert s.y == chr(150)

    def test_new_with_vtable(self):
        cpu = self.cpu
        t_box, T_box = self.alloc_instance(self.T)
        vtable = llmemory.cast_adr_to_ptr(
            llmemory.cast_int_to_adr(T_box.value), heaptracker.VTABLETYPE)
        heaptracker.register_known_gctype(cpu, vtable, self.T)
        r1 = self.execute_operation(rop.NEW_WITH_VTABLE, [T_box], 'ref')
        r2 = self.execute_operation(rop.NEW_WITH_VTABLE, [T_box], 'ref')
        assert r1.value != r2.value
        descr1 = cpu.fielddescrof(self.S, 'chr1')
        descr2 = cpu.fielddescrof(self.S, 'chr2')
        descrshort = cpu.fielddescrof(self.S, 'short')
        self.execute_operation(rop.SETFIELD_GC, [r1, BoxInt(150)],
                               'void', descr=descr2)
        self.execute_operation(rop.SETFIELD_GC, [r1, BoxInt(190)],
                               'void', descr=descr1)
        self.execute_operation(rop.SETFIELD_GC, [r1, BoxInt(1313)],
                               'void', descr=descrshort)
        s = lltype.cast_opaque_ptr(lltype.Ptr(self.T), r1.value)
        assert s.parent.chr1 == chr(190)
        assert s.parent.chr2 == chr(150)
        r = self.cpu.bh_getfield_gc_i(r1.value, descrshort)
        assert r == 1313
        self.cpu.bh_setfield_gc_i(r1.value, 1333, descrshort)
        r = self.cpu.bh_getfield_gc_i(r1.value, descrshort)
        assert r == 1333
        r = self.execute_operation(rop.GETFIELD_GC, [r1], 'int',
                                   descr=descrshort)
        assert r.value == 1333
        t = lltype.cast_opaque_ptr(lltype.Ptr(self.T), t_box.value)
        assert s.parent.parent.typeptr == t.parent.parent.typeptr

    def test_new_array(self):
        A = lltype.GcArray(lltype.Signed)
        arraydescr = self.cpu.arraydescrof(A)
        r1 = self.execute_operation(rop.NEW_ARRAY, [BoxInt(342)],
                                    'ref', descr=arraydescr)
        r2 = self.execute_operation(rop.NEW_ARRAY, [BoxInt(342)],
                                    'ref', descr=arraydescr)
        assert r1.value != r2.value
        a = lltype.cast_opaque_ptr(lltype.Ptr(A), r1.value)
        assert len(a) == 342

    def test_new_array_clear(self):
        A = lltype.GcArray(lltype.Signed)
        arraydescr = self.cpu.arraydescrof(A)
        r1 = self.execute_operation(rop.NEW_ARRAY_CLEAR, [BoxInt(342)],
                                    'ref', descr=arraydescr)
        a = lltype.cast_opaque_ptr(lltype.Ptr(A), r1.value)
        assert a[0] == 0
        assert len(a) == 342

    def test_new_string(self):
        r1 = self.execute_operation(rop.NEWSTR, [BoxInt(342)], 'ref')
        r2 = self.execute_operation(rop.NEWSTR, [BoxInt(342)], 'ref')
        assert r1.value != r2.value
        a = lltype.cast_opaque_ptr(lltype.Ptr(rstr.STR), r1.value)
        assert len(a.chars) == 342

    def test_new_unicode(self):
        r1 = self.execute_operation(rop.NEWUNICODE, [BoxInt(342)], 'ref')
        r2 = self.execute_operation(rop.NEWUNICODE, [BoxInt(342)], 'ref')
        assert r1.value != r2.value
        a = lltype.cast_opaque_ptr(lltype.Ptr(rstr.UNICODE), r1.value)
        assert len(a.chars) == 342

    def test_exceptions(self):
        exc_tp = None
        exc_ptr = None
        def func(i):
            if i:
                raise LLException(exc_tp, exc_ptr)

        ops = '''
        [i0]
        i1 = same_as(1)
        call(ConstClass(fptr), i0, descr=calldescr)
        p0 = guard_exception(ConstClass(xtp)) [i1]
        finish(p0)
        '''
        FPTR = lltype.Ptr(lltype.FuncType([lltype.Signed], lltype.Void))
        fptr = llhelper(FPTR, func)
        calldescr = self.cpu.calldescrof(FPTR.TO, FPTR.TO.ARGS, FPTR.TO.RESULT,
                                         EffectInfo.MOST_GENERAL)

        xtp = lltype.malloc(rclass.OBJECT_VTABLE, immortal=True)
        xtp.subclassrange_min = 1
        xtp.subclassrange_max = 3
        X = lltype.GcStruct('X', ('parent', rclass.OBJECT),
                            hints={'vtable':  xtp._obj})
        xptr = lltype.cast_opaque_ptr(llmemory.GCREF, lltype.malloc(X))


        exc_tp = xtp
        exc_ptr = xptr
        loop = parse(ops, self.cpu, namespace=locals())
        looptoken = JitCellToken()
        self.cpu.compile_loop(loop.inputargs, loop.operations, looptoken)
        deadframe = self.cpu.execute_token(looptoken, 1)
        assert self.cpu.get_ref_value(deadframe, 0) == xptr
        excvalue = self.cpu.grab_exc_value(deadframe)
        assert not excvalue
        deadframe = self.cpu.execute_token(looptoken, 0)
        assert self.cpu.get_int_value(deadframe, 0) == 1
        excvalue = self.cpu.grab_exc_value(deadframe)
        assert not excvalue

        ytp = lltype.malloc(rclass.OBJECT_VTABLE, immortal=True)
        ytp.subclassrange_min = 2
        ytp.subclassrange_max = 2
        assert rclass.ll_issubclass(ytp, xtp)
        Y = lltype.GcStruct('Y', ('parent', rclass.OBJECT),
                            hints={'vtable':  ytp._obj})
        yptr = lltype.cast_opaque_ptr(llmemory.GCREF, lltype.malloc(Y))

        # guard_exception uses an exact match
        exc_tp = ytp
        exc_ptr = yptr
        loop = parse(ops, self.cpu, namespace=locals())
        looptoken = JitCellToken()
        self.cpu.compile_loop(loop.inputargs, loop.operations, looptoken)
        deadframe = self.cpu.execute_token(looptoken, 1)
        assert self.cpu.get_int_value(deadframe, 0) == 1
        excvalue = self.cpu.grab_exc_value(deadframe)
        assert excvalue == yptr

        exc_tp = xtp
        exc_ptr = xptr
        ops = '''
        [i0]
        i1 = same_as(1)
        call(ConstClass(fptr), i0, descr=calldescr)
        guard_no_exception() [i1]
        finish(0)
        '''
        loop = parse(ops, self.cpu, namespace=locals())
        looptoken = JitCellToken()
        self.cpu.compile_loop(loop.inputargs, loop.operations, looptoken)
        deadframe = self.cpu.execute_token(looptoken, 1)
        assert self.cpu.get_int_value(deadframe, 0) == 1
        excvalue = self.cpu.grab_exc_value(deadframe)
        assert excvalue == xptr
        deadframe = self.cpu.execute_token(looptoken, 0)
        assert self.cpu.get_int_value(deadframe, 0) == 0
        excvalue = self.cpu.grab_exc_value(deadframe)
        assert not excvalue

    def test_cond_call_gc_wb(self):
        def func_void(a):
            record.append(rffi.cast(lltype.Signed, a))
        record = []
        #
        S = lltype.GcStruct('S', ('tid', lltype.Signed))
        FUNC = self.FuncType([lltype.Ptr(S)], lltype.Void)
        func_ptr = llhelper(lltype.Ptr(FUNC), func_void)
        funcbox = self.get_funcbox(self.cpu, func_ptr)
        class WriteBarrierDescr(AbstractDescr):
            jit_wb_if_flag = 4096
            jit_wb_if_flag_byteofs = struct.pack("i", 4096).index('\x10')
            jit_wb_if_flag_singlebyte = 0x10
            def get_write_barrier_fn(self, cpu):
                return funcbox.getint()
        #
        for cond in [False, True]:
            value = random.randrange(-sys.maxint, sys.maxint)
            if cond:
                value |= 4096
            else:
                value &= ~4096
            s = lltype.malloc(S)
            s.tid = value
            sgcref = lltype.cast_opaque_ptr(llmemory.GCREF, s)
            del record[:]
            self.execute_operation(rop.COND_CALL_GC_WB,
                                   [BoxPtr(sgcref)],
                                   'void', descr=WriteBarrierDescr())
            if cond:
                assert record == [rffi.cast(lltype.Signed, sgcref)]
            else:
                assert record == []

    def test_cond_call_gc_wb_array(self):
        def func_void(a):
            record.append(rffi.cast(lltype.Signed, a))
        record = []
        #
        S = lltype.GcStruct('S', ('tid', lltype.Signed))
        FUNC = self.FuncType([lltype.Ptr(S)], lltype.Void)
        func_ptr = llhelper(lltype.Ptr(FUNC), func_void)
        funcbox = self.get_funcbox(self.cpu, func_ptr)
        class WriteBarrierDescr(AbstractDescr):
            jit_wb_if_flag = 4096
            jit_wb_if_flag_byteofs = struct.pack("i", 4096).index('\x10')
            jit_wb_if_flag_singlebyte = 0x10
            jit_wb_cards_set = 0       # <= without card marking
            def get_write_barrier_fn(self, cpu):
                return funcbox.getint()
        #
        for cond in [False, True]:
            value = random.randrange(-sys.maxint, sys.maxint)
            if cond:
                value |= 4096
            else:
                value &= ~4096
            s = lltype.malloc(S)
            s.tid = value
            sgcref = lltype.cast_opaque_ptr(llmemory.GCREF, s)
            del record[:]
            self.execute_operation(rop.COND_CALL_GC_WB_ARRAY,
                       [BoxPtr(sgcref), ConstInt(123)],
                       'void', descr=WriteBarrierDescr())
            if cond:
                assert record == [rffi.cast(lltype.Signed, sgcref)]
            else:
                assert record == []

    def test_cond_call_gc_wb_array_card_marking_fast_path(self):
        def func_void(a):
            record.append(rffi.cast(lltype.Signed, a))
            if cond == 1:      # the write barrier sets the flag
                s.data.tid |= 32768
        record = []
        #
        S = lltype.Struct('S', ('tid', lltype.Signed))
        S_WITH_CARDS = lltype.Struct('S_WITH_CARDS',
                                     ('card0', lltype.Char),
                                     ('card1', lltype.Char),
                                     ('card2', lltype.Char),
                                     ('card3', lltype.Char),
                                     ('card4', lltype.Char),
                                     ('card5', lltype.Char),
                                     ('card6', lltype.Char),
                                     ('card7', lltype.Char),
                                     ('data',  S))
        FUNC = self.FuncType([lltype.Ptr(S)], lltype.Void)
        func_ptr = llhelper(lltype.Ptr(FUNC), func_void)
        funcbox = self.get_funcbox(self.cpu, func_ptr)
        class WriteBarrierDescr(AbstractDescr):
            jit_wb_if_flag = 4096
            jit_wb_if_flag_byteofs = struct.pack("i", 4096).index('\x10')
            jit_wb_if_flag_singlebyte = 0x10
            jit_wb_cards_set = 32768
            jit_wb_cards_set_byteofs = struct.pack("i", 32768).index('\x80')
            jit_wb_cards_set_singlebyte = -0x80
            jit_wb_card_page_shift = 7
            def get_write_barrier_from_array_fn(self, cpu):
                return funcbox.getint()
        #
        for BoxIndexCls in [BoxInt, ConstInt]*3:
            for cond in [-1, 0, 1, 2]:
                # cond=-1:GCFLAG_TRACK_YOUNG_PTRS, GCFLAG_CARDS_SET are not set
                # cond=0: GCFLAG_CARDS_SET is never set
                # cond=1: GCFLAG_CARDS_SET is not set, but the wb sets it
                # cond=2: GCFLAG_CARDS_SET is already set
                print
                print '_'*79
                print 'BoxIndexCls =', BoxIndexCls
                print 'testing cond =', cond
                print
                value = random.randrange(-sys.maxint, sys.maxint)
                if cond >= 0:
                    value |= 4096
                else:
                    value &= ~4096
                if cond == 2:
                    value |= 32768
                else:
                    value &= ~32768
                s = lltype.malloc(S_WITH_CARDS, immortal=True, zero=True)
                s.data.tid = value
                sgcref = rffi.cast(llmemory.GCREF, s.data)
                del record[:]
                box_index = BoxIndexCls((9<<7) + 17)
                self.execute_operation(rop.COND_CALL_GC_WB_ARRAY,
                           [BoxPtr(sgcref), box_index],
                           'void', descr=WriteBarrierDescr())
                if cond in [0, 1]:
                    assert record == [rffi.cast(lltype.Signed, s.data)]
                else:
                    assert record == []
                if cond in [1, 2]:
                    assert s.card6 == '\x02'
                else:
                    assert s.card6 == '\x00'
                assert s.card0 == '\x00'
                assert s.card1 == '\x00'
                assert s.card2 == '\x00'
                assert s.card3 == '\x00'
                assert s.card4 == '\x00'
                assert s.card5 == '\x00'
                assert s.card7 == '\x00'
                if cond == 1:
                    value |= 32768
                assert s.data.tid == value

    def test_cond_call(self):
        def func_void(*args):
            called.append(args)

        for i in range(5):
            called = []

            FUNC = self.FuncType([lltype.Signed] * i, lltype.Void)
            func_ptr = llhelper(lltype.Ptr(FUNC), func_void)
            calldescr = self.cpu.calldescrof(FUNC, FUNC.ARGS, FUNC.RESULT,
                                             EffectInfo.MOST_GENERAL)

            ops = '''
            [i0, i1, i2, i3, i4, i5, i6, f0, f1]
            cond_call(i1, ConstClass(func_ptr), %s)
            guard_false(i0, descr=faildescr) [i1, i2, i3, i4, i5, i6, f0, f1]
            ''' % ', '.join(['i%d' % (j + 2) for j in range(i)] + ["descr=calldescr"])
            loop = parse(ops, namespace={'faildescr': BasicFailDescr(),
                                         'func_ptr': func_ptr,
                                         'calldescr': calldescr})
            looptoken = JitCellToken()
            self.cpu.compile_loop(loop.inputargs, loop.operations, looptoken)
            f1 = longlong.getfloatstorage(1.2)
            f2 = longlong.getfloatstorage(3.4)
            frame = self.cpu.execute_token(looptoken, 1, 0, 1, 2, 3, 4, 5, f1, f2)
            assert not called
            for j in range(5):
                assert self.cpu.get_int_value(frame, j) == j
            assert longlong.getrealfloat(self.cpu.get_float_value(frame, 6)) == 1.2
            assert longlong.getrealfloat(self.cpu.get_float_value(frame, 7)) == 3.4
            frame = self.cpu.execute_token(looptoken, 1, 1, 1, 2, 3, 4, 5, f1, f2)
            assert called == [tuple(range(1, i + 1))]
            for j in range(4):
                assert self.cpu.get_int_value(frame, j + 1) == j + 1
            assert longlong.getrealfloat(self.cpu.get_float_value(frame, 6)) == 1.2
            assert longlong.getrealfloat(self.cpu.get_float_value(frame, 7)) == 3.4

    def test_force_operations_returning_void(self):
        values = []
        def maybe_force(token, flag):
            if flag:
                deadframe = self.cpu.force(token)
                values.append(self.cpu.get_latest_descr(deadframe))
                values.append(self.cpu.get_int_value(deadframe, 0))
                values.append(self.cpu.get_int_value(deadframe, 1))
                self.cpu.set_savedata_ref(deadframe, random_gcref)

        FUNC = self.FuncType([llmemory.GCREF, lltype.Signed], lltype.Void)
        func_ptr = llhelper(lltype.Ptr(FUNC), maybe_force)
        funcbox = self.get_funcbox(self.cpu, func_ptr).constbox()
        calldescr = self.cpu.calldescrof(FUNC, FUNC.ARGS, FUNC.RESULT,
                                         EffectInfo.MOST_GENERAL)
        cpu = self.cpu
        i0 = BoxInt()
        i1 = BoxInt()
        tok = BoxPtr()
        faildescr = BasicFailDescr(1)
        ops = [
        ResOperation(rop.FORCE_TOKEN, [], tok),
        ResOperation(rop.CALL_MAY_FORCE, [funcbox, tok, i1], None,
                     descr=calldescr),
        ResOperation(rop.GUARD_NOT_FORCED, [], None, descr=faildescr),
        ResOperation(rop.FINISH, [i0], None, descr=BasicFinalDescr(0))
        ]
        ops[2].setfailargs([i1, i0])
        looptoken = JitCellToken()
        self.cpu.compile_loop([i0, i1], ops, looptoken)
        deadframe = self.cpu.execute_token(looptoken, 20, 0)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == 0
        assert self.cpu.get_int_value(deadframe, 0) == 20
        assert values == []

        deadframe = self.cpu.execute_token(looptoken, 10, 1)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == 1
        assert self.cpu.get_int_value(deadframe, 0) == 1
        assert self.cpu.get_int_value(deadframe, 1) == 10
        assert values == [faildescr, 1, 10]
        assert self.cpu.get_savedata_ref(deadframe)   # not NULL
        assert self.cpu.get_savedata_ref(deadframe) == random_gcref

    def test_force_operations_returning_int(self):
        values = []
        def maybe_force(token, flag):
            if flag:
                deadframe = self.cpu.force(token)
                values.append(self.cpu.get_int_value(deadframe, 0))
                values.append(self.cpu.get_int_value(deadframe, 2))
                self.cpu.set_savedata_ref(deadframe, random_gcref)
            return 42

        FUNC = self.FuncType([llmemory.GCREF, lltype.Signed], lltype.Signed)
        func_ptr = llhelper(lltype.Ptr(FUNC), maybe_force)
        funcbox = self.get_funcbox(self.cpu, func_ptr).constbox()
        calldescr = self.cpu.calldescrof(FUNC, FUNC.ARGS, FUNC.RESULT,
                                         EffectInfo.MOST_GENERAL)
        cpu = self.cpu
        i0 = BoxInt()
        i1 = BoxInt()
        i2 = BoxInt()
        tok = BoxPtr()
        faildescr = BasicFailDescr(1)
        ops = [
        ResOperation(rop.FORCE_TOKEN, [], tok),
        ResOperation(rop.CALL_MAY_FORCE, [funcbox, tok, i1], i2,
                     descr=calldescr),
        ResOperation(rop.GUARD_NOT_FORCED, [], None, descr=faildescr),
        ResOperation(rop.FINISH, [i2], None, descr=BasicFinalDescr(0))
        ]
        ops[2].setfailargs([i1, i2, i0])
        looptoken = JitCellToken()
        self.cpu.compile_loop([i0, i1], ops, looptoken)
        deadframe = self.cpu.execute_token(looptoken, 20, 0)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == 0
        assert self.cpu.get_int_value(deadframe, 0) == 42
        assert values == []

        deadframe = self.cpu.execute_token(looptoken, 10, 1)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == 1
        assert self.cpu.get_int_value(deadframe, 0) == 1
        assert self.cpu.get_int_value(deadframe, 1) == 42
        assert self.cpu.get_int_value(deadframe, 2) == 10
        assert values == [1, 10]
        assert self.cpu.get_savedata_ref(deadframe) == random_gcref

    def test_force_operations_returning_float(self):
        if not self.cpu.supports_floats:
            py.test.skip("requires floats")
        values = []
        def maybe_force(token, flag):
            if flag:
                deadframe = self.cpu.force(token)
                values.append(self.cpu.get_int_value(deadframe, 0))
                values.append(self.cpu.get_int_value(deadframe, 2))
                self.cpu.set_savedata_ref(deadframe, random_gcref)
            return 42.5

        FUNC = self.FuncType([llmemory.GCREF, lltype.Signed], lltype.Float)
        func_ptr = llhelper(lltype.Ptr(FUNC), maybe_force)
        funcbox = self.get_funcbox(self.cpu, func_ptr).constbox()
        calldescr = self.cpu.calldescrof(FUNC, FUNC.ARGS, FUNC.RESULT,
                                         EffectInfo.MOST_GENERAL)
        cpu = self.cpu
        i0 = BoxInt()
        i1 = BoxInt()
        f2 = BoxFloat()
        tok = BoxPtr()
        faildescr = BasicFailDescr(1)
        ops = [
        ResOperation(rop.FORCE_TOKEN, [], tok),
        ResOperation(rop.CALL_MAY_FORCE, [funcbox, tok, i1], f2,
                     descr=calldescr),
        ResOperation(rop.GUARD_NOT_FORCED, [], None, descr=faildescr),
        ResOperation(rop.FINISH, [f2], None, descr=BasicFinalDescr(0))
        ]
        ops[2].setfailargs([i1, f2, i0])
        looptoken = JitCellToken()
        self.cpu.compile_loop([i0, i1], ops, looptoken)
        deadframe = self.cpu.execute_token(looptoken, 20, 0)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == 0
        x = self.cpu.get_float_value(deadframe, 0)
        assert longlong.getrealfloat(x) == 42.5
        assert values == []

        deadframe = self.cpu.execute_token(looptoken, 10, 1)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == 1
        assert self.cpu.get_int_value(deadframe, 0) == 1
        x = self.cpu.get_float_value(deadframe, 1)
        assert longlong.getrealfloat(x) == 42.5
        assert self.cpu.get_int_value(deadframe, 2) == 10
        assert values == [1, 10]
        assert self.cpu.get_savedata_ref(deadframe) == random_gcref

    def test_guard_not_forced_2(self):
        cpu = self.cpu
        i0 = BoxInt()
        i1 = BoxInt()
        tok = BoxPtr()
        faildescr = BasicFailDescr(1)
        ops = [
        ResOperation(rop.INT_ADD, [i0, ConstInt(10)], i1),
        ResOperation(rop.FORCE_TOKEN, [], tok),
        ResOperation(rop.GUARD_NOT_FORCED_2, [], None, descr=faildescr),
        ResOperation(rop.FINISH, [tok], None, descr=BasicFinalDescr(0))
        ]
        ops[-2].setfailargs([i1])
        looptoken = JitCellToken()
        self.cpu.compile_loop([i0], ops, looptoken)
        deadframe = self.cpu.execute_token(looptoken, 20)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == 0
        frame = self.cpu.get_ref_value(deadframe, 0)
        # actually, we should get the same pointer in 'frame' and 'deadframe'
        # but it is not the case on LLGraph
        if not getattr(self.cpu, 'is_llgraph', False):
            assert frame == deadframe
        deadframe2 = self.cpu.force(frame)
        assert self.cpu.get_int_value(deadframe2, 0) == 30

    def test_call_to_c_function(self):
        from rpython.rlib.libffi import CDLL, types, ArgChain, FUNCFLAG_CDECL
        from rpython.rtyper.lltypesystem.ll2ctypes import libc_name
        libc = CDLL(libc_name)
        c_tolower = libc.getpointer('tolower', [types.uchar], types.sint)
        argchain = ArgChain().arg(ord('A'))
        assert c_tolower.call(argchain, rffi.INT) == ord('a')

        cpu = self.cpu
        func_adr = llmemory.cast_ptr_to_adr(c_tolower.funcsym)
        funcbox = ConstInt(heaptracker.adr2int(func_adr))
        calldescr = cpu._calldescr_dynamic_for_tests([types.uchar], types.sint)
        i1 = BoxInt()
        i2 = BoxInt()
        tok = BoxInt()
        faildescr = BasicFailDescr(1)
        ops = [
        ResOperation(rop.CALL_RELEASE_GIL, [ConstInt(0), funcbox, i1], i2,
                     descr=calldescr),
        ResOperation(rop.GUARD_NOT_FORCED, [], None, descr=faildescr),
        ResOperation(rop.FINISH, [i2], None, descr=BasicFinalDescr(0))
        ]
        ops[1].setfailargs([i1, i2])
        looptoken = JitCellToken()
        self.cpu.compile_loop([i1], ops, looptoken)
        deadframe = self.cpu.execute_token(looptoken, ord('G'))
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == 0
        assert self.cpu.get_int_value(deadframe, 0) == ord('g')

    def test_call_to_c_function_with_callback(self):
        from rpython.rlib.libffi import CDLL, types, ArgChain, clibffi
        from rpython.rtyper.lltypesystem.ll2ctypes import libc_name
        libc = CDLL(libc_name)
        types_size_t = clibffi.cast_type_to_ffitype(rffi.SIZE_T)
        c_qsort = libc.getpointer('qsort', [types.pointer, types_size_t,
                                            types_size_t, types.pointer],
                                  types.void)
        class Glob(object):
            pass
        glob = Glob()
        class X(object):
            pass
        #
        def callback(p1, p2):
            glob.lst.append(X())
            return rffi.cast(rffi.INT, 1)
        CALLBACK = lltype.Ptr(lltype.FuncType([lltype.Signed,
                                               lltype.Signed], rffi.INT))
        fn = llhelper(CALLBACK, callback)
        S = lltype.Struct('S', ('x', rffi.INT), ('y', rffi.INT))
        raw = lltype.malloc(S, flavor='raw')
        argchain = ArgChain()
        argchain = argchain.arg(rffi.cast(lltype.Signed, raw))
        argchain = argchain.arg(rffi.cast(rffi.SIZE_T, 2))
        argchain = argchain.arg(rffi.cast(rffi.SIZE_T, 4))
        argchain = argchain.arg(rffi.cast(lltype.Signed, fn))
        glob.lst = []
        c_qsort.call(argchain, lltype.Void)
        assert len(glob.lst) > 0
        del glob.lst[:]

        cpu = self.cpu
        func_adr = llmemory.cast_ptr_to_adr(c_qsort.funcsym)
        funcbox = ConstInt(heaptracker.adr2int(func_adr))
        calldescr = cpu._calldescr_dynamic_for_tests(
            [types.pointer, types_size_t, types_size_t, types.pointer],
            types.void)
        i0 = BoxInt()
        i1 = BoxInt()
        i2 = BoxInt()
        i3 = BoxInt()
        tok = BoxInt()
        faildescr = BasicFailDescr(1)
        ops = [
        ResOperation(rop.CALL_RELEASE_GIL,
                     [ConstInt(0), funcbox, i0, i1, i2, i3], None,
                     descr=calldescr),
        ResOperation(rop.GUARD_NOT_FORCED, [], None, descr=faildescr),
        ResOperation(rop.FINISH, [], None, descr=BasicFinalDescr(0))
        ]
        ops[1].setfailargs([])
        looptoken = JitCellToken()
        self.cpu.compile_loop([i0, i1, i2, i3], ops, looptoken)
        args = [rffi.cast(lltype.Signed, raw),
                2,
                4,
                rffi.cast(lltype.Signed, fn)]
        assert glob.lst == []
        deadframe = self.cpu.execute_token(looptoken, *args)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == 0
        assert len(glob.lst) > 0
        lltype.free(raw, flavor='raw')

    def test_call_to_winapi_function(self):
        from rpython.rlib.clibffi import _WIN32
        if not _WIN32:
            py.test.skip("Windows test only")
        from rpython.rlib.libffi import WinDLL, types, ArgChain
        from rpython.rlib.rwin32 import DWORD
        libc = WinDLL('KERNEL32')
        c_GetCurrentDir = libc.getpointer('GetCurrentDirectoryA',
                                          [types.ulong, types.pointer],
                                          types.ulong)

        cwd = os.getcwd()
        buflen = len(cwd) + 10
        buffer = lltype.malloc(rffi.CCHARP.TO, buflen, flavor='raw')
        argchain = ArgChain().arg(rffi.cast(DWORD, buflen)).arg(buffer)
        res = c_GetCurrentDir.call(argchain, DWORD)
        assert rffi.cast(lltype.Signed, res) == len(cwd)
        assert rffi.charp2strn(buffer, buflen) == cwd
        lltype.free(buffer, flavor='raw')

        cpu = self.cpu
        func_adr = llmemory.cast_ptr_to_adr(c_GetCurrentDir.funcsym)
        funcbox = ConstInt(heaptracker.adr2int(func_adr))
        calldescr = cpu._calldescr_dynamic_for_tests(
            [types.ulong, types.pointer],
            types.ulong,
            abiname='FFI_STDCALL')
        i1 = BoxInt()
        i2 = BoxInt()
        faildescr = BasicFailDescr(1)
        # if the stdcall convention is ignored, then ESP is wrong after the
        # call: 8 bytes too much.  If we repeat the call often enough, crash.
        ops = []
        for i in range(50):
            i3 = BoxInt()
            ops += [
                ResOperation(rop.CALL_RELEASE_GIL,
                             [ConstInt(0), funcbox, i1, i2], i3,
                             descr=calldescr),
                ResOperation(rop.GUARD_NOT_FORCED, [], None, descr=faildescr),
                ]
            ops[-1].setfailargs([])
        ops += [
            ResOperation(rop.FINISH, [i3], None, descr=BasicFinalDescr(0))
        ]
        looptoken = JitCellToken()
        self.cpu.compile_loop([i1, i2], ops, looptoken)

        buffer = lltype.malloc(rffi.CCHARP.TO, buflen, flavor='raw')
        args = [buflen, rffi.cast(lltype.Signed, buffer)]
        deadframe = self.cpu.execute_token(looptoken, *args)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == 0
        assert self.cpu.get_int_value(deadframe, 0) == len(cwd)
        assert rffi.charp2strn(buffer, buflen) == cwd
        lltype.free(buffer, flavor='raw')

    def test_call_release_gil_return_types(self):
        from rpython.rlib.libffi import types
        from rpython.rlib.rarithmetic import r_uint, r_longlong, r_ulonglong
        from rpython.rlib.rarithmetic import r_singlefloat
        cpu = self.cpu

        for ffitype, result, TP in [
            (types.ulong,  r_uint(sys.maxint + 10), lltype.Unsigned),
            (types.slong,  -4321, lltype.Signed),
            (types.uint8,  200, rffi.UCHAR),
            (types.sint8,  -42, rffi.SIGNEDCHAR),
            (types.uint16, 50000, rffi.USHORT),
            (types.sint16, -20000, rffi.SHORT),
            (types.uint32, r_uint(3000000000), rffi.UINT),
            (types.sint32, -2000000000, rffi.INT),
            (types.uint64, r_ulonglong(9999999999999999999),
                                                   lltype.UnsignedLongLong),
            (types.sint64, r_longlong(-999999999999999999),
                                                   lltype.SignedLongLong),
            (types.double, 12.3475226, rffi.DOUBLE),
            (types.float,  r_singlefloat(-592.75), rffi.FLOAT),
            ]:
            if IS_32_BIT and TP in (lltype.SignedLongLong,
                                    lltype.UnsignedLongLong):
                if not cpu.supports_longlong:
                    continue
            if TP == rffi.DOUBLE:
                if not cpu.supports_floats:
                    continue
            if TP == rffi.FLOAT:
                if not cpu.supports_singlefloats:
                    continue
            #
            result = rffi.cast(TP, result)
            #
            def pseudo_c_function():
                return result
            #
            FPTR = self.Ptr(self.FuncType([], TP))
            func_ptr = llhelper(FPTR, pseudo_c_function)
            funcbox = self.get_funcbox(cpu, func_ptr)
            calldescr = cpu._calldescr_dynamic_for_tests([], ffitype)
            faildescr = BasicFailDescr(1)
            kind = types.getkind(ffitype)
            if kind in 'uis':
                b3 = BoxInt()
            elif kind in 'fUI':
                b3 = BoxFloat()
            else:
                assert 0, kind
            #
            ops = [
                ResOperation(rop.CALL_RELEASE_GIL, [ConstInt(0), funcbox], b3,
                             descr=calldescr),
                ResOperation(rop.GUARD_NOT_FORCED, [], None, descr=faildescr),
                ResOperation(rop.FINISH, [b3], None, descr=BasicFinalDescr(0))
                ]
            ops[1].setfailargs([])
            looptoken = JitCellToken()
            self.cpu.compile_loop([], ops, looptoken)

            deadframe = self.cpu.execute_token(looptoken)
            fail = self.cpu.get_latest_descr(deadframe)
            assert fail.identifier == 0
            if isinstance(b3, BoxInt):
                r = self.cpu.get_int_value(deadframe, 0)
                if isinstance(result, r_singlefloat):
                    assert -sys.maxint-1 <= r <= 0xFFFFFFFF
                    r, = struct.unpack("f", struct.pack("I", r & 0xFFFFFFFF))
                    result = float(result)
                else:
                    r = rffi.cast(TP, r)
                assert r == result
            elif isinstance(b3, BoxFloat):
                r = self.cpu.get_float_value(deadframe, 0)
                if isinstance(result, float):
                    r = longlong.getrealfloat(r)
                else:
                    r = rffi.cast(TP, r)
                assert r == result

    def test_call_release_gil_variable_function_and_arguments(self):
        from rpython.translator.tool.cbuild import ExternalCompilationInfo
        from rpython.rlib.libffi import types
        from rpython.rlib.rarithmetic import r_uint, r_longlong, r_ulonglong
        from rpython.rlib.rarithmetic import r_singlefloat
        from rpython.translator.c import primitive

        cpu = self.cpu
        rnd = random.Random(525)

        ALL_TYPES = [
            (types.ulong,  lltype.Unsigned),
            (types.slong,  lltype.Signed),
            (types.uint8,  rffi.UCHAR),
            (types.sint8,  rffi.SIGNEDCHAR),
            (types.uint16, rffi.USHORT),
            (types.sint16, rffi.SHORT),
            (types.uint32, rffi.UINT),
            (types.sint32, rffi.INT),
            ]
        if IS_32_BIT and cpu.supports_longlong:
            ALL_TYPES += [
                (types.uint64, lltype.UnsignedLongLong),
                (types.sint64, lltype.SignedLongLong),
                ] * 2
        if cpu.supports_floats:
            ALL_TYPES += [
                (types.double, rffi.DOUBLE),
                ] * 4
        if cpu.supports_singlefloats:
            ALL_TYPES += [
                (types.float,  rffi.FLOAT),
                ] * 4

        NB_TESTS = 100
        c_source = []
        all_tests = []

        def prepare_c_source():
            """Pick a random choice of argument types and length,
            and build a C function with these arguments.  The C
            function will simply copy them all into static global
            variables.  There are then additional functions to fetch
            them, one per argument, with a signature 'void(ARG *)'.
            """
            POSSIBLE_TYPES = [rnd.choice(ALL_TYPES)
                              for i in range(random.randrange(2, 5))]
            load_factor = rnd.random()
            keepalive_factor = rnd.random()
            #
            ffitypes = []
            ARGTYPES = []
            for i in range(rnd.randrange(4, 20)):
                ffitype, TP = rnd.choice(POSSIBLE_TYPES)
                ffitypes.append(ffitype)
                ARGTYPES.append(TP)
            fn_name = 'vartest%d' % k
            all_tests.append((ARGTYPES, ffitypes, fn_name))
            #
            fn_args = []
            for i, ARG in enumerate(ARGTYPES):
                arg_decl = primitive.cdecl(primitive.PrimitiveType[ARG],
                                           'x%d' % i)
                fn_args.append(arg_decl)
                var_name = 'argcopy_%s_x%d' % (fn_name, i)
                var_decl = primitive.cdecl(primitive.PrimitiveType[ARG],
                                           var_name)
                c_source.append('static %s;' % var_decl)
                getter_name = '%s_get%d' % (fn_name, i)
                c_source.append('RPY_EXPORTED void %s(%s) { *p = %s; }' % (
                    getter_name,
                    primitive.cdecl(primitive.PrimitiveType[ARG], '*p'),
                    var_name))
            c_source.append('')
            c_source.append('static void real%s(%s)' % (
                fn_name, ', '.join(fn_args)))
            c_source.append('{')
            for i in range(len(ARGTYPES)):
                c_source.append('    argcopy_%s_x%d = x%d;' % (fn_name, i, i))
            c_source.append('}')
            c_source.append('RPY_EXPORTED void *%s(void)' % fn_name)
            c_source.append('{')
            c_source.append('    return (void *)&real%s;' % fn_name)
            c_source.append('}')
            c_source.append('')

        for k in range(NB_TESTS):
            prepare_c_source()

        eci = ExternalCompilationInfo(
            separate_module_sources=['\n'.join(c_source)])

        for k in range(NB_TESTS):
            ARGTYPES, ffitypes, fn_name = all_tests[k]
            func_getter_ptr = rffi.llexternal(fn_name, [], lltype.Signed,
                                         compilation_info=eci, _nowrapper=True)
            load_factor = rnd.random()
            keepalive_factor = rnd.random()
            #
            func_raw = func_getter_ptr()
            calldescr = cpu._calldescr_dynamic_for_tests(ffitypes, types.void)
            faildescr = BasicFailDescr(1)
            #
            argboxes = [BoxInt()]   # for the function to call
            codes = ['X']
            for ffitype in ffitypes:
                kind = types.getkind(ffitype)
                codes.append(kind)
                if kind in 'uis':
                    b1 = BoxInt()
                elif kind in 'fUI':
                    b1 = BoxFloat()
                else:
                    assert 0, kind
                argboxes.append(b1)
            codes = ''.join(codes)     # useful for pdb
            print
            print codes
            #
            argvalues = [func_raw]
            for TP in ARGTYPES:
                r = (rnd.random() - 0.5) * 999999999999.9
                r = rffi.cast(TP, r)
                argvalues.append(r)
            #
            argvalues_normal = argvalues[:1]
            for ffitype, r in zip(ffitypes, argvalues[1:]):
                kind = types.getkind(ffitype)
                if kind in 'ui':
                    r = rffi.cast(lltype.Signed, r)
                elif kind in 's':
                    r, = struct.unpack("i", struct.pack("f", float(r)))
                elif kind in 'f':
                    r = longlong.getfloatstorage(r)
                elif kind in 'UI':   # 32-bit only
                    r = rffi.cast(lltype.SignedLongLong, r)
                else:
                    assert 0
                argvalues_normal.append(r)
            #
            ops = []
            loadcodes = []
            insideboxes = []
            for b1 in argboxes:
                load = rnd.random() < load_factor
                loadcodes.append(' ^'[load])
                if load:
                    b2 = b1.clonebox()
                    ops.insert(rnd.randrange(0, len(ops)+1),
                               ResOperation(rop.SAME_AS, [b1], b2))
                    b1 = b2
                insideboxes.append(b1)
            loadcodes = ''.join(loadcodes)
            print loadcodes
            ops += [
                ResOperation(rop.CALL_RELEASE_GIL,
                             [ConstInt(0)] + insideboxes, None,
                             descr=calldescr),
                ResOperation(rop.GUARD_NOT_FORCED, [], None, descr=faildescr),
                ResOperation(rop.FINISH, [], None, descr=BasicFinalDescr(0))
                ]
            ops[-2].setfailargs([])
            # keep alive a random subset of the insideboxes
            for b1 in insideboxes:
                if rnd.random() < keepalive_factor:
                    ops.insert(-1, ResOperation(rop.SAME_AS, [b1],
                                                b1.clonebox()))
            looptoken = JitCellToken()
            self.cpu.compile_loop(argboxes, ops, looptoken)
            #
            deadframe = self.cpu.execute_token(looptoken, *argvalues_normal)
            fail = self.cpu.get_latest_descr(deadframe)
            assert fail.identifier == 0
            expected = argvalues[1:]
            got = []
            for i, ARG in enumerate(ARGTYPES):
                PARG = rffi.CArrayPtr(ARG)
                getter_name = '%s_get%d' % (fn_name, i)
                getter_ptr = rffi.llexternal(getter_name, [PARG], lltype.Void,
                                             compilation_info=eci,
                                             _nowrapper=True)
                my_arg = lltype.malloc(PARG.TO, 1, zero=True, flavor='raw')
                getter_ptr(my_arg)
                got.append(my_arg[0])
                lltype.free(my_arg, flavor='raw')
            different_values = ['x%d: got %r, expected %r' % (i, a, b)
                                for i, (a, b) in enumerate(zip(got, expected))
                                if a != b]
            assert got == expected, '\n'.join(
                ['bad args, signature %r' % codes[1:]] + different_values)

    def test_call_release_gil_save_errno(self):
        from rpython.translator.tool.cbuild import ExternalCompilationInfo
        from rpython.rlib.libffi import types
        from rpython.jit.backend.llsupport import llerrno
        #
        if not isinstance(self.cpu, AbstractLLCPU):
            py.test.skip("not on LLGraph")
        eci = ExternalCompilationInfo(
            separate_module_sources=['''
                #include <errno.h>
                static long f1(long a, long b, long c, long d,
                               long e, long f, long g) {
                    errno = 42;
                    return (a + 10*b + 100*c + 1000*d +
                            10000*e + 100000*f + 1000000*g);
                }
                RPY_EXPORTED
                long test_call_release_gil_save_errno(void) {
                    return (long)&f1;
                }
            '''])
        fn_name = 'test_call_release_gil_save_errno'
        getter_ptr = rffi.llexternal(fn_name, [], lltype.Signed,
                                     compilation_info=eci, _nowrapper=True)
        func1_adr = getter_ptr()
        calldescr = self.cpu._calldescr_dynamic_for_tests([types.slong]*7,
                                                          types.slong)
        #
        for saveerr in [rffi.RFFI_ERR_NONE,
                        rffi.RFFI_SAVE_ERRNO,
                        rffi.RFFI_ERR_NONE | rffi.RFFI_ALT_ERRNO,
                        rffi.RFFI_SAVE_ERRNO | rffi.RFFI_ALT_ERRNO,
                        ]:
            faildescr = BasicFailDescr(1)
            inputargs = [BoxInt() for i in range(7)]
            i1 = BoxInt()
            ops = [
                ResOperation(rop.CALL_RELEASE_GIL,
                             [ConstInt(saveerr), ConstInt(func1_adr)]
                                 + inputargs, i1,
                             descr=calldescr),
                ResOperation(rop.GUARD_NOT_FORCED, [], None, descr=faildescr),
                ResOperation(rop.FINISH, [i1], None, descr=BasicFinalDescr(0))
            ]
            ops[-2].setfailargs([])
            looptoken = JitCellToken()
            self.cpu.compile_loop(inputargs, ops, looptoken)
            #
            llerrno.set_debug_saved_errno(self.cpu, 24)
            llerrno.set_debug_saved_alterrno(self.cpu, 25)
            deadframe = self.cpu.execute_token(looptoken, 9, 8, 7, 6, 5, 4, 3)
            original_result = self.cpu.get_int_value(deadframe, 0)
            result = llerrno.get_debug_saved_errno(self.cpu)
            altresult = llerrno.get_debug_saved_alterrno(self.cpu)
            print 'saveerr =', saveerr, ': got result =', result, \
                  'altresult =', altresult
            #
            expected = {
                rffi.RFFI_ERR_NONE: (24, 25),
                rffi.RFFI_SAVE_ERRNO: (42, 25),
                rffi.RFFI_ERR_NONE | rffi.RFFI_ALT_ERRNO: (24, 25),
                rffi.RFFI_SAVE_ERRNO | rffi.RFFI_ALT_ERRNO: (24, 42),
            }
            # expected (24, 25) as originally set, with possibly one
            # of the two changed to 42 by the assembler code
            assert (result, altresult) == expected[saveerr]
            assert original_result == 3456789

    def test_call_release_gil_readsaved_errno(self):
        from rpython.translator.tool.cbuild import ExternalCompilationInfo
        from rpython.rlib.libffi import types
        from rpython.jit.backend.llsupport import llerrno
        #
        if not isinstance(self.cpu, AbstractLLCPU):
            py.test.skip("not on LLGraph")
        eci = ExternalCompilationInfo(
            separate_module_sources=[r'''
                #include <stdio.h>
                #include <errno.h>
                static long f1(long a, long b, long c, long d,
                               long e, long f, long g) {
                    long r = errno;
                    printf("read saved errno: %ld\n", r);
                    r += 100 * (a + 10*b + 100*c + 1000*d +
                                10000*e + 100000*f + 1000000*g);
                    return r;
                }
                RPY_EXPORTED
                long test_call_release_gil_readsaved_errno(void) {
                    return (long)&f1;
                }
            '''])
        fn_name = 'test_call_release_gil_readsaved_errno'
        getter_ptr = rffi.llexternal(fn_name, [], lltype.Signed,
                                     compilation_info=eci, _nowrapper=True)
        func1_adr = getter_ptr()
        calldescr = self.cpu._calldescr_dynamic_for_tests([types.slong]*7,
                                                          types.slong)
        #
        for saveerr in [rffi.RFFI_READSAVED_ERRNO,
                        rffi.RFFI_ZERO_ERRNO_BEFORE,
                        rffi.RFFI_READSAVED_ERRNO   | rffi.RFFI_ALT_ERRNO,
                        rffi.RFFI_ZERO_ERRNO_BEFORE | rffi.RFFI_ALT_ERRNO,
                        ]:
            faildescr = BasicFailDescr(1)
            inputargs = [BoxInt() for i in range(7)]
            i1 = BoxInt()
            ops = [
                ResOperation(rop.CALL_RELEASE_GIL,
                             [ConstInt(saveerr), ConstInt(func1_adr)]
                                 + inputargs, i1,
                             descr=calldescr),
                ResOperation(rop.GUARD_NOT_FORCED, [], None, descr=faildescr),
                ResOperation(rop.FINISH, [i1], None, descr=BasicFinalDescr(0))
            ]
            ops[-2].setfailargs([])
            looptoken = JitCellToken()
            self.cpu.compile_loop(inputargs, ops, looptoken)
            #
            llerrno.set_debug_saved_errno(self.cpu, 24)
            llerrno.set_debug_saved_alterrno(self.cpu, 25)
            deadframe = self.cpu.execute_token(looptoken, 9, 8, 7, 6, 5, 4, 3)
            result = self.cpu.get_int_value(deadframe, 0)
            assert llerrno.get_debug_saved_errno(self.cpu) == 24
            assert llerrno.get_debug_saved_alterrno(self.cpu) == 25
            #
            if saveerr & rffi.RFFI_READSAVED_ERRNO:
                if saveerr & rffi.RFFI_ALT_ERRNO:
                    assert result == 25 + 345678900
                else:
                    assert result == 24 + 345678900
            else:
                assert result == 0  + 345678900

    def test_call_release_gil_save_lasterror(self):
        from rpython.translator.tool.cbuild import ExternalCompilationInfo
        from rpython.rlib.libffi import types
        from rpython.jit.backend.llsupport import llerrno
        #
        if not isinstance(self.cpu, AbstractLLCPU):
            py.test.skip("not on LLGraph")
        if sys.platform != 'win32':
            py.test.skip("Windows test only")
        eci = ExternalCompilationInfo(
            separate_module_sources=['''
                #include <windows.h>
                static long f1(long a, long b, long c, long d,
                               long e, long f, long g) {
                    SetLastError(42);
                    return (a + 10*b + 100*c + 1000*d +
                            10000*e + 100000*f + 1000000*g);
                }
                RPY_EXPORTED
                long test_call_release_gil_save_lasterror(void) {
                    return (long)&f1;
                }
            '''])
        fn_name = 'test_call_release_gil_save_lasterror'
        getter_ptr = rffi.llexternal(fn_name, [], lltype.Signed,
                                     compilation_info=eci, _nowrapper=True)
        func1_adr = getter_ptr()
        calldescr = self.cpu._calldescr_dynamic_for_tests([types.slong]*7,
                                                          types.slong)
        #
        for saveerr in [rffi.RFFI_SAVE_ERRNO,  # but not _LASTERROR
                        rffi.RFFI_SAVE_ERRNO | rffi.RFFI_ALT_ERRNO,
                        rffi.RFFI_SAVE_LASTERROR,
                        rffi.RFFI_SAVE_LASTERROR | rffi.RFFI_ALT_ERRNO,
                        ]:
            faildescr = BasicFailDescr(1)
            inputargs = [BoxInt() for i in range(7)]
            i1 = BoxInt()
            ops = [
                ResOperation(rop.CALL_RELEASE_GIL,
                             [ConstInt(saveerr), ConstInt(func1_adr)]
                                 + inputargs, i1,
                             descr=calldescr),
                ResOperation(rop.GUARD_NOT_FORCED, [], None, descr=faildescr),
                ResOperation(rop.FINISH, [i1], None, descr=BasicFinalDescr(0))
            ]
            ops[-2].setfailargs([])
            looptoken = JitCellToken()
            self.cpu.compile_loop(inputargs, ops, looptoken)
            #
            llerrno.set_debug_saved_lasterror(self.cpu, 24)
            llerrno.set_debug_saved_altlasterror(self.cpu, 25)
            deadframe = self.cpu.execute_token(looptoken, 9, 8, 7, 6, 5, 4, 3)
            original_result = self.cpu.get_int_value(deadframe, 0)
            result = llerrno.get_debug_saved_lasterror(self.cpu)
            altresult = llerrno.get_debug_saved_altlasterror(self.cpu)
            print 'saveerr =', saveerr, ': got result =', result,
            print 'and altresult =', altresult
            #
            if saveerr & rffi.RFFI_SAVE_LASTERROR:
                # one from the C code, the other not touched
                if saveerr & rffi.RFFI_ALT_ERRNO:
                    assert (result, altresult) == (24, 42)
                else:
                    assert (result, altresult) == (42, 25)
            else:
                assert (result, altresult) == (24, 25)      # not touched
            assert original_result == 3456789

    def test_call_release_gil_readsaved_lasterror(self):
        from rpython.translator.tool.cbuild import ExternalCompilationInfo
        from rpython.rlib.libffi import types
        from rpython.jit.backend.llsupport import llerrno
        #
        if not isinstance(self.cpu, AbstractLLCPU):
            py.test.skip("not on LLGraph")
        if sys.platform != 'win32':
            py.test.skip("Windows test only")
        eci = ExternalCompilationInfo(
            separate_module_sources=[r'''
                #include <windows.h>
                static long f1(long a, long b, long c, long d,
                               long e, long f, long g) {
                    long r = GetLastError();
                    printf("GetLastError() result: %ld\n", r);
                    printf("%ld %ld %ld %ld %ld %ld %ld\n", a,b,c,d,e,f,g);
                    r += 100 * (a + 10*b + 100*c + 1000*d +
                                10000*e + 100000*f + 1000000*g);
                    return r;
                }
                RPY_EXPORTED
                long test_call_release_gil_readsaved_lasterror(void) {
                    return (long)&f1;
                }
            '''])
        fn_name = 'test_call_release_gil_readsaved_lasterror'
        getter_ptr = rffi.llexternal(fn_name, [], lltype.Signed,
                                     compilation_info=eci, _nowrapper=True)
        func1_adr = getter_ptr()
        calldescr = self.cpu._calldescr_dynamic_for_tests([types.slong]*7,
                                                          types.slong)
        #
        for saveerr in [rffi.RFFI_READSAVED_LASTERROR,
                        rffi.RFFI_READSAVED_LASTERROR | rffi.RFFI_ALT_ERRNO,
                       ]:
            faildescr = BasicFailDescr(1)
            inputargs = [BoxInt() for i in range(7)]
            i1 = BoxInt()
            ops = [
                ResOperation(rop.CALL_RELEASE_GIL,
                             [ConstInt(saveerr), ConstInt(func1_adr)]
                                 + inputargs, i1,
                             descr=calldescr),
                ResOperation(rop.GUARD_NOT_FORCED, [], None, descr=faildescr),
                ResOperation(rop.FINISH, [i1], None, descr=BasicFinalDescr(0))
            ]
            ops[-2].setfailargs([])
            looptoken = JitCellToken()
            self.cpu.compile_loop(inputargs, ops, looptoken)
            #
            llerrno.set_debug_saved_lasterror(self.cpu, 24)
            llerrno.set_debug_saved_altlasterror(self.cpu, 25)
            deadframe = self.cpu.execute_token(looptoken, 9, 8, 7, 6, 5, 4, 3)
            result = self.cpu.get_int_value(deadframe, 0)
            assert llerrno.get_debug_saved_lasterror(self.cpu) == 24
            assert llerrno.get_debug_saved_altlasterror(self.cpu) == 25
            #
            if saveerr & rffi.RFFI_ALT_ERRNO:
                expected_lasterror = 25
            else:
                expected_lasterror = 24
            assert result == expected_lasterror + 345678900

    def test_call_release_gil_err_all(self):
        from rpython.translator.tool.cbuild import ExternalCompilationInfo
        from rpython.rlib.libffi import types
        from rpython.jit.backend.llsupport import llerrno
        #
        if not isinstance(self.cpu, AbstractLLCPU):
            py.test.skip("not on LLGraph")
        if sys.platform != 'win32':
            eci = ExternalCompilationInfo(
                separate_module_sources=[r'''
                    #include <errno.h>
                    static long f1(long a, long b, long c, long d,
                                   long e, long f, long g) {
                        long r = errno;
                        errno = 42;
                        r += 100 * (a + 10*b + 100*c + 1000*d +
                                    10000*e + 100000*f + 1000000*g);
                        return r;
                    }
                    RPY_EXPORTED
                    long test_call_release_gil_err_all(void) {
                        return (long)&f1;
                    }
                '''])
        else:
            eci = ExternalCompilationInfo(
                separate_module_sources=[r'''
                    #include <windows.h>
                    #include <errno.h>
                    static long f1(long a, long b, long c, long d,
                                   long e, long f, long g) {
                        long r = errno + 10 * GetLastError();
                        errno = 42;
                        SetLastError(43);
                        r += 100 * (a + 10*b + 100*c + 1000*d +
                                    10000*e + 100000*f + 1000000*g);
                        return r;
                    }
                    RPY_EXPORTED
                    long test_call_release_gil_err_all(void) {
                        return (long)&f1;
                    }
                '''])
        fn_name = 'test_call_release_gil_err_all'
        getter_ptr = rffi.llexternal(fn_name, [], lltype.Signed,
                                     compilation_info=eci, _nowrapper=True)
        func1_adr = getter_ptr()
        calldescr = self.cpu._calldescr_dynamic_for_tests([types.slong]*7,
                                                          types.slong)
        #
        for saveerr in [rffi.RFFI_ERR_ALL,
                        rffi.RFFI_ERR_ALL | rffi.RFFI_ALT_ERRNO,
                       ]:
            faildescr = BasicFailDescr(1)
            inputargs = [BoxInt() for i in range(7)]
            i1 = BoxInt()
            ops = [
                ResOperation(rop.CALL_RELEASE_GIL,
                             [ConstInt(saveerr), ConstInt(func1_adr)]
                                 + inputargs, i1,
                             descr=calldescr),
                ResOperation(rop.GUARD_NOT_FORCED, [], None, descr=faildescr),
                ResOperation(rop.FINISH, [i1], None, descr=BasicFinalDescr(0))
            ]
            ops[-2].setfailargs([])
            looptoken = JitCellToken()
            self.cpu.compile_loop(inputargs, ops, looptoken)
            #
            llerrno.set_debug_saved_errno(self.cpu, 8)
            llerrno.set_debug_saved_alterrno(self.cpu, 5)
            llerrno.set_debug_saved_lasterror(self.cpu, 9)
            llerrno.set_debug_saved_altlasterror(self.cpu, 4)
            deadframe = self.cpu.execute_token(looptoken, 1, 2, 3, 4, 5, 6, 7)
            result = self.cpu.get_int_value(deadframe, 0)
            got_errno = llerrno.get_debug_saved_errno(self.cpu)
            got_alter = llerrno.get_debug_saved_alterrno(self.cpu)
            if saveerr & rffi.RFFI_ALT_ERRNO:
                assert (got_errno, got_alter) == (8, 42)
            else:
                assert (got_errno, got_alter) == (42, 5)
            if sys.platform != 'win32':
                if saveerr & rffi.RFFI_ALT_ERRNO:
                    assert result == 765432105
                else:
                    assert result == 765432108
            else:
                if saveerr & rffi.RFFI_ALT_ERRNO:
                    assert result == 765432145
                else:
                    assert result == 765432198
                got_lasterror = llerrno.get_debug_saved_lasterror(self.cpu)
                got_altlaster = llerrno.get_debug_saved_altlasterror(self.cpu)
                if saveerr & rffi.RFFI_ALT_ERRNO:
                    assert (got_lasterror, got_altlaster) == (9, 43)
                else:
                    assert (got_lasterror, got_altlaster) == (43, 4)

    def test_guard_not_invalidated(self):
        cpu = self.cpu
        i0 = BoxInt()
        i1 = BoxInt()
        faildescr = BasicFailDescr(1)
        ops = [
            ResOperation(rop.GUARD_NOT_INVALIDATED, [], None, descr=faildescr),
            ResOperation(rop.FINISH, [i0], None, descr=BasicFinalDescr(0))
        ]
        ops[0].setfailargs([i1])
        looptoken = JitCellToken()
        self.cpu.compile_loop([i0, i1], ops, looptoken)

        deadframe = self.cpu.execute_token(looptoken, -42, 9)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == 0
        assert self.cpu.get_int_value(deadframe, 0) == -42
        print 'step 1 ok'
        print '-'*79

        # mark as failing
        self.cpu.invalidate_loop(looptoken)

        deadframe = self.cpu.execute_token(looptoken, -42, 9)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail is faildescr
        assert self.cpu.get_int_value(deadframe, 0) == 9
        print 'step 2 ok'
        print '-'*79

        # attach a bridge
        i2 = BoxInt()
        faildescr2 = BasicFailDescr(2)
        ops = [
            ResOperation(rop.GUARD_NOT_INVALIDATED, [],None, descr=faildescr2),
            ResOperation(rop.FINISH, [i2], None, descr=BasicFinalDescr(3))
        ]
        ops[0].setfailargs([])
        self.cpu.compile_bridge(faildescr, [i2], ops, looptoken)

        deadframe = self.cpu.execute_token(looptoken, -42, 9)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == 3
        assert self.cpu.get_int_value(deadframe, 0) == 9
        print 'step 3 ok'
        print '-'*79

        # mark as failing again
        self.cpu.invalidate_loop(looptoken)

        deadframe = self.cpu.execute_token(looptoken, -42, 9)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail is faildescr2
        print 'step 4 ok'
        print '-'*79

    def test_guard_not_invalidated_and_label(self):
        # test that the guard_not_invalidated reserves enough room before
        # the label.  If it doesn't, then in this example after we invalidate
        # the guard, jumping to the label will hit the invalidation code too
        cpu = self.cpu
        i0 = BoxInt()
        faildescr = BasicFailDescr(1)
        labeldescr = TargetToken()
        ops = [
            ResOperation(rop.GUARD_NOT_INVALIDATED, [], None, descr=faildescr),
            ResOperation(rop.LABEL, [i0], None, descr=labeldescr),
            ResOperation(rop.FINISH, [i0], None, descr=BasicFinalDescr(3)),
        ]
        ops[0].setfailargs([])
        looptoken = JitCellToken()
        self.cpu.compile_loop([i0], ops, looptoken)
        # mark as failing
        self.cpu.invalidate_loop(looptoken)
        # attach a bridge
        i2 = BoxInt()
        ops2 = [
            ResOperation(rop.JUMP, [ConstInt(333)], None, descr=labeldescr),
        ]
        self.cpu.compile_bridge(faildescr, [], ops2, looptoken)
        # run: must not be caught in an infinite loop
        deadframe = self.cpu.execute_token(looptoken, 16)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == 3
        assert self.cpu.get_int_value(deadframe, 0) == 333

    # pure do_ / descr features

    def test_do_operations(self):
        cpu = self.cpu
        #
        A = lltype.GcArray(lltype.Char)
        descr_A = cpu.arraydescrof(A)
        a = lltype.malloc(A, 5)
        x = cpu.bh_arraylen_gc(lltype.cast_opaque_ptr(llmemory.GCREF, a),
                               descr_A)
        assert x == 5
        #
        a[2] = 'Y'
        x = cpu.bh_getarrayitem_gc_i(
            lltype.cast_opaque_ptr(llmemory.GCREF, a), 2, descr_A)
        assert x == ord('Y')
        #
        B = lltype.GcArray(lltype.Ptr(A))
        descr_B = cpu.arraydescrof(B)
        b = lltype.malloc(B, 4)
        b[3] = a
        x = cpu.bh_getarrayitem_gc_r(
            lltype.cast_opaque_ptr(llmemory.GCREF, b), 3, descr_B)
        assert lltype.cast_opaque_ptr(lltype.Ptr(A), x) == a
        if self.cpu.supports_floats:
            C = lltype.GcArray(lltype.Float)
            c = lltype.malloc(C, 6)
            c[3] = 3.5
            descr_C = cpu.arraydescrof(C)
            x = cpu.bh_getarrayitem_gc_f(
                lltype.cast_opaque_ptr(llmemory.GCREF, c), 3, descr_C)
            assert longlong.getrealfloat(x) == 3.5
            cpu.bh_setarrayitem_gc_f(
                lltype.cast_opaque_ptr(llmemory.GCREF, c), 4,
                longlong.getfloatstorage(4.5), descr_C)
            assert c[4] == 4.5
        s = rstr.mallocstr(6)
        x = cpu.bh_strlen(lltype.cast_opaque_ptr(llmemory.GCREF, s))
        assert x == 6
        #
        s.chars[3] = 'X'
        x = cpu.bh_strgetitem(lltype.cast_opaque_ptr(llmemory.GCREF, s), 3)
        assert x == ord('X')
        #
        S = lltype.GcStruct('S', ('x', lltype.Char), ('y', lltype.Ptr(A)),
                            ('z', lltype.Float))
        descrfld_x = cpu.fielddescrof(S, 'x')
        s = lltype.malloc(S)
        s.x = 'Z'
        x = cpu.bh_getfield_gc_i(lltype.cast_opaque_ptr(llmemory.GCREF, s),
                                 descrfld_x)
        assert x == ord('Z')
        #
        cpu.bh_setfield_gc_i(lltype.cast_opaque_ptr(llmemory.GCREF, s),
                             ord('4'), descrfld_x)
        assert s.x == '4'
        #
        descrfld_y = cpu.fielddescrof(S, 'y')
        s.y = a
        x = cpu.bh_getfield_gc_r(lltype.cast_opaque_ptr(llmemory.GCREF, s),
                                 descrfld_y)
        assert lltype.cast_opaque_ptr(lltype.Ptr(A), x) == a
        #
        s.y = lltype.nullptr(A)
        cpu.bh_setfield_gc_r(lltype.cast_opaque_ptr(llmemory.GCREF, s),
                             x, descrfld_y)
        assert s.y == a
        #
        RS = lltype.Struct('S', ('x', lltype.Char))  #, ('y', lltype.Ptr(A)))
        descrfld_rx = cpu.fielddescrof(RS, 'x')
        rs = lltype.malloc(RS, immortal=True)
        rs.x = '?'
        x = cpu.bh_getfield_raw_i(
            heaptracker.adr2int(llmemory.cast_ptr_to_adr(rs)),
            descrfld_rx)
        assert x == ord('?')
        #
        cpu.bh_setfield_raw_i(
            heaptracker.adr2int(llmemory.cast_ptr_to_adr(rs)),
            ord('!'), descrfld_rx)
        assert rs.x == '!'
        #

        if self.cpu.supports_floats:
            descrfld_z = cpu.fielddescrof(S, 'z')
            cpu.bh_setfield_gc_f(
                lltype.cast_opaque_ptr(llmemory.GCREF, s),
                longlong.getfloatstorage(3.5), descrfld_z)
            assert s.z == 3.5
            s.z = 3.2
            x = cpu.bh_getfield_gc_f(
                lltype.cast_opaque_ptr(llmemory.GCREF, s),
                descrfld_z)
            assert longlong.getrealfloat(x) == 3.2
        ### we don't support in the JIT for now GC pointers
        ### stored inside non-GC structs.
        #descrfld_ry = cpu.fielddescrof(RS, 'y')
        #rs.y = a
        #x = cpu.do_getfield_raw(
        #    BoxInt(cpu.cast_adr_to_int(llmemory.cast_ptr_to_adr(rs))),
        #    descrfld_ry)
        #assert isinstance(x, BoxPtr)
        #assert x.getref(lltype.Ptr(A)) == a
        #
        #rs.y = lltype.nullptr(A)
        #cpu.do_setfield_raw(
        #    BoxInt(cpu.cast_adr_to_int(llmemory.cast_ptr_to_adr(rs))), x,
        #    descrfld_ry)
        #assert rs.y == a
        #
        descrsize = cpu.sizeof(S)
        x = cpu.bh_new(descrsize)
        lltype.cast_opaque_ptr(lltype.Ptr(S), x)    # type check
        #
        descrsize2 = cpu.sizeof(rclass.OBJECT)
        vtable2 = lltype.malloc(rclass.OBJECT_VTABLE, immortal=True)
        vtable2_int = heaptracker.adr2int(llmemory.cast_ptr_to_adr(vtable2))
        heaptracker.register_known_gctype(cpu, vtable2, rclass.OBJECT)
        x = cpu.bh_new_with_vtable(vtable2_int, descrsize2)
        lltype.cast_opaque_ptr(lltype.Ptr(rclass.OBJECT), x)    # type check
        # well...
        #assert x.getref(rclass.OBJECTPTR).typeptr == vtable2
        #
        arraydescr = cpu.arraydescrof(A)
        x = cpu.bh_new_array(7, arraydescr)
        array = lltype.cast_opaque_ptr(lltype.Ptr(A), x)
        assert len(array) == 7
        #
        cpu.bh_setarrayitem_gc_i(x, 5, ord('*'), descr_A)
        assert array[5] == '*'
        #
        cpu.bh_setarrayitem_gc_r(
            lltype.cast_opaque_ptr(llmemory.GCREF, b), 1, x, descr_B)
        assert b[1] == array
        #
        x = cpu.bh_newstr(5)
        str = lltype.cast_opaque_ptr(lltype.Ptr(rstr.STR), x)
        assert len(str.chars) == 5
        #
        cpu.bh_strsetitem(x, 4, ord('/'))
        assert str.chars[4] == '/'

    def test_sorting_of_fields(self):
        S = lltype.GcStruct('S', ('parent', rclass.OBJECT),
                                  ('value', lltype.Signed),
                                  ('chr1', lltype.Char),
                                  ('chr2', lltype.Char))
        chr1 = self.cpu.fielddescrof(S, 'chr1').sort_key()
        value = self.cpu.fielddescrof(S, 'value').sort_key()
        chr2 = self.cpu.fielddescrof(S, 'chr2').sort_key()
        assert len(set([value, chr1, chr2])) == 3

    def test_guards_nongc(self):
        x = lltype.malloc(lltype.Struct('x'), flavor='raw')
        v = heaptracker.adr2int(llmemory.cast_ptr_to_adr(x))
        vbox = BoxInt(v)
        ops = [
            (rop.GUARD_NONNULL, vbox, False),
            (rop.GUARD_ISNULL, vbox, True),
            (rop.GUARD_NONNULL, BoxInt(0), True),
            (rop.GUARD_ISNULL, BoxInt(0), False),
            ]
        for opname, arg, res in ops:
            self.execute_operation(opname, [arg], 'void')
            assert self.guard_failed == res

        lltype.free(x, flavor='raw')

    def test_assembler_call(self):
        called = []
        def assembler_helper(deadframe, virtualizable):
            assert self.cpu.get_int_value(deadframe, 0) == 97
            called.append(self.cpu.get_latest_descr(deadframe))
            return 4 + 9

        FUNCPTR = lltype.Ptr(lltype.FuncType([llmemory.GCREF,
                                              llmemory.GCREF],
                                             lltype.Signed))
        class FakeJitDriverSD:
            index_of_virtualizable = -1
            _assembler_helper_ptr = llhelper(FUNCPTR, assembler_helper)
            assembler_helper_adr = llmemory.cast_ptr_to_adr(
                _assembler_helper_ptr)

        ops = '''
        [i0, i1, i2, i3, i4, i5, i6, i7, i8, i9]
        i10 = int_add(i0, i1)
        i11 = int_add(i10, i2)
        i12 = int_add(i11, i3)
        i13 = int_add(i12, i4)
        i14 = int_add(i13, i5)
        i15 = int_add(i14, i6)
        i16 = int_add(i15, i7)
        i17 = int_add(i16, i8)
        i18 = int_add(i17, i9)
        finish(i18)'''
        loop = parse(ops)
        looptoken = JitCellToken()
        looptoken.outermost_jitdriver_sd = FakeJitDriverSD()
        finish_descr = loop.operations[-1].getdescr()
        self.cpu.done_with_this_frame_descr_int = BasicFinalDescr()
        self.cpu.compile_loop(loop.inputargs, loop.operations, looptoken)
        ARGS = [lltype.Signed] * 10
        RES = lltype.Signed
        FakeJitDriverSD.portal_calldescr = self.cpu.calldescrof(
            lltype.Ptr(lltype.FuncType(ARGS, RES)), ARGS, RES,
            EffectInfo.MOST_GENERAL)
        args = [i+1 for i in range(10)]
        deadframe = self.cpu.execute_token(looptoken, *args)
        assert self.cpu.get_int_value(deadframe, 0) == 55
        ops = '''
        [i0, i1, i2, i3, i4, i5, i6, i7, i8, i9]
        i10 = int_add(i0, 42)
        i11 = call_assembler(i10, i1, i2, i3, i4, i5, i6, i7, i8, i9, descr=looptoken)
        guard_not_forced()[]
        finish(i11)
        '''
        loop = parse(ops, namespace=locals())
        othertoken = JitCellToken()
        self.cpu.compile_loop(loop.inputargs, loop.operations, othertoken)
        args = [i+1 for i in range(10)]
        deadframe = self.cpu.execute_token(othertoken, *args)
        assert self.cpu.get_int_value(deadframe, 0) == 13
        assert called == [finish_descr]

        # test the fast path, which should not call assembler_helper()
        del called[:]
        self.cpu.done_with_this_frame_descr_int = finish_descr
        othertoken = JitCellToken()
        self.cpu.compile_loop(loop.inputargs, loop.operations, othertoken)
        args = [i+1 for i in range(10)]
        deadframe = self.cpu.execute_token(othertoken, *args)
        assert self.cpu.get_int_value(deadframe, 0) == 97
        assert not called

    def test_assembler_call_propagate_exc(self):
        if not isinstance(self.cpu, AbstractLLCPU):
            py.test.skip("llgraph can't fake exceptions well enough, give up")

        excdescr = BasicFailDescr(666)
        self.cpu.propagate_exception_descr = excdescr
        self.cpu.setup_once()    # xxx redo it, because we added
                                 # propagate_exception

        def assembler_helper(deadframe, virtualizable):
            assert self.cpu.get_latest_descr(deadframe) is excdescr
            # let's assume we handled that
            return 3

        FUNCPTR = lltype.Ptr(lltype.FuncType([llmemory.GCREF,
                                              llmemory.GCREF],
                                             lltype.Signed))
        class FakeJitDriverSD:
            index_of_virtualizable = -1
            _assembler_helper_ptr = llhelper(FUNCPTR, assembler_helper)
            assembler_helper_adr = llmemory.cast_ptr_to_adr(
                _assembler_helper_ptr)

        ops = '''
        [i0]
        p0 = newunicode(i0)
        finish(p0)'''
        loop = parse(ops)
        looptoken = JitCellToken()
        looptoken.outermost_jitdriver_sd = FakeJitDriverSD()
        self.cpu.compile_loop(loop.inputargs, loop.operations, looptoken)
        ARGS = [lltype.Signed] * 10
        RES = lltype.Signed
        FakeJitDriverSD.portal_calldescr = self.cpu.calldescrof(
            lltype.Ptr(lltype.FuncType(ARGS, RES)), ARGS, RES,
            EffectInfo.MOST_GENERAL)
        ops = '''
        [i0]
        i11 = call_assembler(i0, descr=looptoken)
        guard_not_forced()[]
        finish(i11)
        '''
        loop = parse(ops, namespace=locals())
        othertoken = JitCellToken()
        self.cpu.compile_loop(loop.inputargs, loop.operations, othertoken)
        deadframe = self.cpu.execute_token(othertoken, sys.maxint - 1)
        assert self.cpu.get_int_value(deadframe, 0) == 3

    def test_assembler_call_float(self):
        if not self.cpu.supports_floats:
            py.test.skip("requires floats")
        called = []
        def assembler_helper(deadframe, virtualizable):
            x = self.cpu.get_float_value(deadframe, 0)
            assert longlong.getrealfloat(x) == 1.2 + 3.2
            called.append(self.cpu.get_latest_descr(deadframe))
            print '!' * 30 + 'assembler_helper'
            return 13.5

        FUNCPTR = lltype.Ptr(lltype.FuncType([llmemory.GCREF,
                                              llmemory.GCREF],
                                             lltype.Float))
        class FakeJitDriverSD:
            index_of_virtualizable = -1
            _assembler_helper_ptr = llhelper(FUNCPTR, assembler_helper)
            assembler_helper_adr = llmemory.cast_ptr_to_adr(
                _assembler_helper_ptr)

        ARGS = [lltype.Float, lltype.Float]
        RES = lltype.Float
        FakeJitDriverSD.portal_calldescr = self.cpu.calldescrof(
            lltype.Ptr(lltype.FuncType(ARGS, RES)), ARGS, RES,
            EffectInfo.MOST_GENERAL)
        ops = '''
        [f0, f1]
        f2 = float_add(f0, f1)
        finish(f2)'''
        loop = parse(ops)
        finish_descr = loop.operations[-1].getdescr()
        looptoken = JitCellToken()
        looptoken.outermost_jitdriver_sd = FakeJitDriverSD()
        self.cpu.done_with_this_frame_descr_float = BasicFinalDescr()
        self.cpu.compile_loop(loop.inputargs, loop.operations, looptoken)
        args = [longlong.getfloatstorage(1.2),
                longlong.getfloatstorage(2.3)]
        deadframe = self.cpu.execute_token(looptoken, *args)
        x = self.cpu.get_float_value(deadframe, 0)
        assert longlong.getrealfloat(x) == 1.2 + 2.3
        ops = '''
        [f4, f5]
        f3 = call_assembler(f4, f5, descr=looptoken)
        guard_not_forced()[]
        finish(f3)
        '''
        loop = parse(ops, namespace=locals())
        othertoken = JitCellToken()
        self.cpu.compile_loop(loop.inputargs, loop.operations, othertoken)
        args = [longlong.getfloatstorage(1.2),
                longlong.getfloatstorage(3.2)]
        deadframe = self.cpu.execute_token(othertoken, *args)
        x = self.cpu.get_float_value(deadframe, 0)
        assert longlong.getrealfloat(x) == 13.5
        assert called == [finish_descr]

        # test the fast path, which should not call assembler_helper()
        del called[:]
        self.cpu.done_with_this_frame_descr_float = finish_descr
        othertoken = JitCellToken()
        self.cpu.compile_loop(loop.inputargs, loop.operations, othertoken)
        args = [longlong.getfloatstorage(1.2),
                longlong.getfloatstorage(4.2)]
        deadframe = self.cpu.execute_token(othertoken, *args)
        x = self.cpu.get_float_value(deadframe, 0)
        assert longlong.getrealfloat(x) == 1.2 + 4.2
        assert not called

    def test_raw_malloced_getarrayitem(self):
        ARRAY = rffi.CArray(lltype.Signed)
        descr = self.cpu.arraydescrof(ARRAY)
        a = lltype.malloc(ARRAY, 10, flavor='raw')
        a[7] = -4242
        addr = llmemory.cast_ptr_to_adr(a)
        abox = BoxInt(heaptracker.adr2int(addr))
        r1 = self.execute_operation(rop.GETARRAYITEM_RAW, [abox, BoxInt(7)],
                                    'int', descr=descr)
        assert r1.getint() == -4242
        lltype.free(a, flavor='raw')

    def test_raw_malloced_setarrayitem(self):
        ARRAY = rffi.CArray(lltype.Signed)
        descr = self.cpu.arraydescrof(ARRAY)
        a = lltype.malloc(ARRAY, 10, flavor='raw')
        addr = llmemory.cast_ptr_to_adr(a)
        abox = BoxInt(heaptracker.adr2int(addr))
        self.execute_operation(rop.SETARRAYITEM_RAW, [abox, BoxInt(5),
                                                      BoxInt(12345)],
                               'void', descr=descr)
        assert a[5] == 12345
        lltype.free(a, flavor='raw')

    def test_redirect_call_assembler(self):
        if not self.cpu.supports_floats:
            py.test.skip("requires floats")
        called = []
        def assembler_helper(deadframe, virtualizable):
            x = self.cpu.get_float_value(deadframe, 0)
            assert longlong.getrealfloat(x) == 1.25 + 3.25
            called.append(self.cpu.get_latest_descr(deadframe))
            return 13.5

        FUNCPTR = lltype.Ptr(lltype.FuncType([llmemory.GCREF, llmemory.GCREF],
                                             lltype.Float))
        class FakeJitDriverSD:
            index_of_virtualizable = -1
            _assembler_helper_ptr = llhelper(FUNCPTR, assembler_helper)
            assembler_helper_adr = llmemory.cast_ptr_to_adr(
                _assembler_helper_ptr)

        ARGS = [lltype.Float, lltype.Float]
        RES = lltype.Float
        FakeJitDriverSD.portal_calldescr = self.cpu.calldescrof(
            lltype.Ptr(lltype.FuncType(ARGS, RES)), ARGS, RES,
            EffectInfo.MOST_GENERAL)
        ops = '''
        [f0, f1]
        f2 = float_add(f0, f1)
        finish(f2)'''
        loop = parse(ops)
        looptoken = JitCellToken()
        looptoken.outermost_jitdriver_sd = FakeJitDriverSD()
        self.cpu.done_with_this_frame_descr_float = BasicFinalDescr()
        self.cpu.compile_loop(loop.inputargs, loop.operations, looptoken)
        finish_descr = loop.operations[-1].getdescr()
        args = [longlong.getfloatstorage(1.25),
                longlong.getfloatstorage(2.35)]
        deadframe = self.cpu.execute_token(looptoken, *args)
        x = self.cpu.get_float_value(deadframe, 0)
        assert longlong.getrealfloat(x) == 1.25 + 2.35
        assert not called

        ops = '''
        [f4, f5]
        f3 = call_assembler(f4, f5, descr=looptoken)
        guard_not_forced()[]
        finish(f3)
        '''
        loop = parse(ops, namespace=locals())
        othertoken = JitCellToken()
        self.cpu.compile_loop(loop.inputargs, loop.operations, othertoken)

        # normal call_assembler: goes to looptoken
        args = [longlong.getfloatstorage(1.25),
                longlong.getfloatstorage(3.25)]
        deadframe = self.cpu.execute_token(othertoken, *args)
        x = self.cpu.get_float_value(deadframe, 0)
        assert longlong.getrealfloat(x) == 13.5
        assert called == [finish_descr]
        del called[:]

        # compile a replacement
        ops = '''
        [f0, f1]
        f2 = float_sub(f0, f1)
        finish(f2)'''
        loop2 = parse(ops)
        looptoken2 = JitCellToken()
        looptoken2.outermost_jitdriver_sd = FakeJitDriverSD()
        self.cpu.compile_loop(loop2.inputargs, loop2.operations, looptoken2)
        finish_descr2 = loop2.operations[-1].getdescr()

        # install it
        self.cpu.redirect_call_assembler(looptoken, looptoken2)

        # now, our call_assembler should go to looptoken2
        args = [longlong.getfloatstorage(6.0),
                longlong.getfloatstorage(1.5)]         # 6.0-1.5 == 1.25+3.25
        deadframe = self.cpu.execute_token(othertoken, *args)
        x = self.cpu.get_float_value(deadframe, 0)
        assert longlong.getrealfloat(x) == 13.5
        assert called == [finish_descr2]

    def test_short_result_of_getfield_direct(self):
        # Test that a getfield that returns a CHAR, SHORT or INT, signed
        # or unsigned, properly gets zero-extended or sign-extended.
        # Direct bh_xxx test.
        cpu = self.cpu
        for RESTYPE in [rffi.SIGNEDCHAR, rffi.UCHAR,
                        rffi.SHORT, rffi.USHORT,
                        rffi.INT, rffi.UINT,
                        rffi.LONG, rffi.ULONG]:
            S = lltype.GcStruct('S', ('x', RESTYPE))
            descrfld_x = cpu.fielddescrof(S, 'x')
            s = lltype.malloc(S)
            value = intmask(0xFFEEDDCCBBAA9988)
            expected = rffi.cast(lltype.Signed, rffi.cast(RESTYPE, value))
            s.x = rffi.cast(RESTYPE, value)
            x = cpu.bh_getfield_gc_i(lltype.cast_opaque_ptr(llmemory.GCREF, s),
                                     descrfld_x)
            assert x == expected, (
                "%r: got %r, expected %r" % (RESTYPE, x, expected))

    def test_short_result_of_getfield_compiled(self):
        # Test that a getfield that returns a CHAR, SHORT or INT, signed
        # or unsigned, properly gets zero-extended or sign-extended.
        # Machine code compilation test.
        cpu = self.cpu
        for RESTYPE in [rffi.SIGNEDCHAR, rffi.UCHAR,
                        rffi.SHORT, rffi.USHORT,
                        rffi.INT, rffi.UINT,
                        rffi.LONG, rffi.ULONG]:
            S = lltype.GcStruct('S', ('x', RESTYPE))
            descrfld_x = cpu.fielddescrof(S, 'x')
            s = lltype.malloc(S)
            value = intmask(0xFFEEDDCCBBAA9988)
            expected = rffi.cast(lltype.Signed, rffi.cast(RESTYPE, value))
            s.x = rffi.cast(RESTYPE, value)
            s_gcref = lltype.cast_opaque_ptr(llmemory.GCREF, s)
            res = self.execute_operation(rop.GETFIELD_GC, [BoxPtr(s_gcref)],
                                         'int', descr=descrfld_x)
            assert res.value == expected, (
                "%r: got %r, expected %r" % (RESTYPE, res.value, expected))

    def test_short_result_of_getarrayitem_direct(self):
        # Test that a getarrayitem that returns a CHAR, SHORT or INT, signed
        # or unsigned, properly gets zero-extended or sign-extended.
        # Direct bh_xxx test.
        cpu = self.cpu
        for RESTYPE in [rffi.SIGNEDCHAR, rffi.UCHAR,
                        rffi.SHORT, rffi.USHORT,
                        rffi.INT, rffi.UINT,
                        rffi.LONG, rffi.ULONG]:
            A = lltype.GcArray(RESTYPE)
            descrarray = cpu.arraydescrof(A)
            a = lltype.malloc(A, 5)
            value = intmask(0xFFEEDDCCBBAA9988)
            expected = rffi.cast(lltype.Signed, rffi.cast(RESTYPE, value))
            a[3] = rffi.cast(RESTYPE, value)
            x = cpu.bh_getarrayitem_gc_i(
                lltype.cast_opaque_ptr(llmemory.GCREF, a), 3, descrarray)
            assert x == expected, (
                "%r: got %r, expected %r" % (RESTYPE, x, expected))

    def test_short_result_of_getarrayitem_compiled(self):
        # Test that a getarrayitem that returns a CHAR, SHORT or INT, signed
        # or unsigned, properly gets zero-extended or sign-extended.
        # Machine code compilation test.
        cpu = self.cpu
        for RESTYPE in [rffi.SIGNEDCHAR, rffi.UCHAR,
                        rffi.SHORT, rffi.USHORT,
                        rffi.INT, rffi.UINT,
                        rffi.LONG, rffi.ULONG]:
            A = lltype.GcArray(RESTYPE)
            descrarray = cpu.arraydescrof(A)
            a = lltype.malloc(A, 5)
            value = intmask(0xFFEEDDCCBBAA9988)
            expected = rffi.cast(lltype.Signed, rffi.cast(RESTYPE, value))
            a[3] = rffi.cast(RESTYPE, value)
            a_gcref = lltype.cast_opaque_ptr(llmemory.GCREF, a)
            res = self.execute_operation(rop.GETARRAYITEM_GC,
                                         [BoxPtr(a_gcref), BoxInt(3)],
                                         'int', descr=descrarray)
            assert res.value == expected, (
                "%r: got %r, expected %r" % (RESTYPE, res.value, expected))

    def test_short_result_of_getarrayitem_raw_direct(self):
        # Test that a getarrayitem that returns a CHAR, SHORT or INT, signed
        # or unsigned, properly gets zero-extended or sign-extended.
        # Direct bh_xxx test.
        cpu = self.cpu
        for RESTYPE in [rffi.SIGNEDCHAR, rffi.UCHAR,
                        rffi.SHORT, rffi.USHORT,
                        rffi.INT, rffi.UINT,
                        rffi.LONG, rffi.ULONG]:
            A = rffi.CArray(RESTYPE)
            descrarray = cpu.arraydescrof(A)
            a = lltype.malloc(A, 5, flavor='raw')
            value = intmask(0xFFEEDDCCBBAA9988)
            expected = rffi.cast(lltype.Signed, rffi.cast(RESTYPE, value))
            a[3] = rffi.cast(RESTYPE, value)
            a_rawint = heaptracker.adr2int(llmemory.cast_ptr_to_adr(a))
            x = cpu.bh_getarrayitem_raw_i(a_rawint, 3, descrarray)
            assert x == expected, (
                "%r: got %r, expected %r" % (RESTYPE, x, expected))
            lltype.free(a, flavor='raw')

    def test_short_result_of_getarrayitem_raw_compiled(self):
        # Test that a getarrayitem that returns a CHAR, SHORT or INT, signed
        # or unsigned, properly gets zero-extended or sign-extended.
        # Machine code compilation test.
        cpu = self.cpu
        for RESTYPE in [rffi.SIGNEDCHAR, rffi.UCHAR,
                        rffi.SHORT, rffi.USHORT,
                        rffi.INT, rffi.UINT,
                        rffi.LONG, rffi.ULONG]:
            A = rffi.CArray(RESTYPE)
            descrarray = cpu.arraydescrof(A)
            a = lltype.malloc(A, 5, flavor='raw')
            value = intmask(0xFFEEDDCCBBAA9988)
            expected = rffi.cast(lltype.Signed, rffi.cast(RESTYPE, value))
            a[3] = rffi.cast(RESTYPE, value)
            a_rawint = heaptracker.adr2int(llmemory.cast_ptr_to_adr(a))
            res = self.execute_operation(rop.GETARRAYITEM_RAW,
                                         [BoxInt(a_rawint), BoxInt(3)],
                                         'int', descr=descrarray)
            assert res.value == expected, (
                "%r: got %r, expected %r" % (RESTYPE, res.value, expected))
            lltype.free(a, flavor='raw')

    def test_short_result_of_call_direct(self):
        # Test that calling a function that returns a CHAR, SHORT or INT,
        # signed or unsigned, properly gets zero-extended or sign-extended.
        from rpython.translator.tool.cbuild import ExternalCompilationInfo
        for RESTYPE in [rffi.SIGNEDCHAR, rffi.UCHAR,
                        rffi.SHORT, rffi.USHORT,
                        rffi.INT, rffi.UINT,
                        rffi.LONG, rffi.ULONG]:
            # Tested with a function that intentionally does not cast the
            # result to RESTYPE, but makes sure that we return the whole
            # value in eax or rax.
            eci = ExternalCompilationInfo(
                separate_module_sources=["""
                RPY_EXPORTED long fn_test_result_of_call(long x)
                {
                    return x + 1;
                }
                """])
            f = rffi.llexternal('fn_test_result_of_call', [lltype.Signed],
                                RESTYPE, compilation_info=eci, _nowrapper=True)
            value = intmask(0xFFEEDDCCBBAA9988)
            expected = rffi.cast(lltype.Signed, rffi.cast(RESTYPE, value + 1))
            assert intmask(f(value)) == expected
            #
            FUNC = self.FuncType([lltype.Signed], RESTYPE)
            FPTR = self.Ptr(FUNC)
            calldescr = self.cpu.calldescrof(FUNC, FUNC.ARGS, FUNC.RESULT,
                                             EffectInfo.MOST_GENERAL)
            x = self.cpu.bh_call_i(self.get_funcbox(self.cpu, f).value,
                                   [value], None, None, calldescr)
            assert x == expected, (
                "%r: got %r, expected %r" % (RESTYPE, x, expected))

    def test_short_result_of_call_compiled(self):
        # Test that calling a function that returns a CHAR, SHORT or INT,
        # signed or unsigned, properly gets zero-extended or sign-extended.
        from rpython.translator.tool.cbuild import ExternalCompilationInfo
        for RESTYPE in [rffi.SIGNEDCHAR, rffi.UCHAR,
                        rffi.SHORT, rffi.USHORT,
                        rffi.INT, rffi.UINT,
                        rffi.LONG, rffi.ULONG]:
            # Tested with a function that intentionally does not cast the
            # result to RESTYPE, but makes sure that we return the whole
            # value in eax or rax.
            eci = ExternalCompilationInfo(
                separate_module_sources=["""
                RPY_EXPORTED long fn_test_result_of_call(long x)
                {
                    return x + 1;
                }
                """])
            f = rffi.llexternal('fn_test_result_of_call', [lltype.Signed],
                                RESTYPE, compilation_info=eci, _nowrapper=True)
            value = intmask(0xFFEEDDCCBBAA9988)
            expected = rffi.cast(lltype.Signed, rffi.cast(RESTYPE, value + 1))
            assert intmask(f(value)) == expected
            #
            FUNC = self.FuncType([lltype.Signed], RESTYPE)
            FPTR = self.Ptr(FUNC)
            calldescr = self.cpu.calldescrof(FUNC, FUNC.ARGS, FUNC.RESULT,
                                             EffectInfo.MOST_GENERAL)
            funcbox = self.get_funcbox(self.cpu, f)
            res = self.execute_operation(rop.CALL, [funcbox, BoxInt(value)],
                                         'int', descr=calldescr)
            assert res.value == expected, (
                "%r: got %r, expected %r" % (RESTYPE, res.value, expected))

    def test_supports_longlong(self):
        if IS_64_BIT:
            assert not self.cpu.supports_longlong, (
                "supports_longlong should be False on 64-bit platforms")

    def test_longlong_result_of_call_direct(self):
        if not self.cpu.supports_longlong:
            py.test.skip("longlong test")
        from rpython.translator.tool.cbuild import ExternalCompilationInfo
        from rpython.rlib.rarithmetic import r_longlong
        eci = ExternalCompilationInfo(
            separate_module_sources=["""
            RPY_EXPORTED long long fn_test_result_of_call(long long x)
            {
                return x - 100000000000000;
            }
            """])
        f = rffi.llexternal('fn_test_result_of_call', [lltype.SignedLongLong],
                            lltype.SignedLongLong,
                            compilation_info=eci, _nowrapper=True)
        value = r_longlong(0x7ff05af3307a3fff)
        expected = r_longlong(0x7ff000001fffffff)
        assert f(value) == expected
        #
        FUNC = self.FuncType([lltype.SignedLongLong], lltype.SignedLongLong)
        FPTR = self.Ptr(FUNC)
        calldescr = self.cpu.calldescrof(FUNC, FUNC.ARGS, FUNC.RESULT,
                                         EffectInfo.MOST_GENERAL)
        x = self.cpu.bh_call_f(self.get_funcbox(self.cpu, f).value,
                               None, None, [value], calldescr)
        assert x == expected

    def test_longlong_result_of_call_compiled(self):
        if not self.cpu.supports_longlong:
            py.test.skip("test of longlong result")
        from rpython.translator.tool.cbuild import ExternalCompilationInfo
        from rpython.rlib.rarithmetic import r_longlong
        eci = ExternalCompilationInfo(
            separate_module_sources=["""
            RPY_EXPORTED long long fn_test_result_of_call(long long x)
            {
                return x - 100000000000000;
            }
            """])
        f = rffi.llexternal('fn_test_result_of_call', [lltype.SignedLongLong],
                            lltype.SignedLongLong,
                            compilation_info=eci, _nowrapper=True)
        value = r_longlong(0x7ff05af3307a3fff)
        expected = r_longlong(0x7ff000001fffffff)
        assert f(value) == expected
        #
        FUNC = self.FuncType([lltype.SignedLongLong], lltype.SignedLongLong)
        FPTR = self.Ptr(FUNC)
        calldescr = self.cpu.calldescrof(FUNC, FUNC.ARGS, FUNC.RESULT,
                                         EffectInfo.MOST_GENERAL)
        funcbox = self.get_funcbox(self.cpu, f)
        res = self.execute_operation(rop.CALL, [funcbox, BoxFloat(value)],
                                     'float', descr=calldescr)
        assert res.getfloatstorage() == expected

    def test_singlefloat_result_of_call_direct(self):
        if not self.cpu.supports_singlefloats:
            py.test.skip("singlefloat test")
        from rpython.translator.tool.cbuild import ExternalCompilationInfo
        from rpython.rlib.rarithmetic import r_singlefloat
        eci = ExternalCompilationInfo(
            separate_module_sources=["""
            RPY_EXPORTED float fn_test_result_of_call(float x)
            {
                return x / 2.0f;
            }
            """])
        f = rffi.llexternal('fn_test_result_of_call', [lltype.SingleFloat],
                            lltype.SingleFloat,
                            compilation_info=eci, _nowrapper=True)
        value = r_singlefloat(-42.5)
        expected = r_singlefloat(-21.25)
        assert f(value) == expected
        #
        FUNC = self.FuncType([lltype.SingleFloat], lltype.SingleFloat)
        FPTR = self.Ptr(FUNC)
        calldescr = self.cpu.calldescrof(FUNC, FUNC.ARGS, FUNC.RESULT,
                                         EffectInfo.MOST_GENERAL)
        ivalue = longlong.singlefloat2int(value)
        iexpected = longlong.singlefloat2int(expected)
        x = self.cpu.bh_call_i(self.get_funcbox(self.cpu, f).value,
                               [ivalue], None, None, calldescr)
        assert x == iexpected

    def test_singlefloat_result_of_call_compiled(self):
        if not self.cpu.supports_singlefloats:
            py.test.skip("test of singlefloat result")
        from rpython.translator.tool.cbuild import ExternalCompilationInfo
        from rpython.rlib.rarithmetic import r_singlefloat
        eci = ExternalCompilationInfo(
            separate_module_sources=["""
            RPY_EXPORTED float fn_test_result_of_call(float x)
            {
                return x / 2.0f;
            }
            """])
        f = rffi.llexternal('fn_test_result_of_call', [lltype.SingleFloat],
                            lltype.SingleFloat,
                            compilation_info=eci, _nowrapper=True)
        value = r_singlefloat(-42.5)
        expected = r_singlefloat(-21.25)
        assert f(value) == expected
        #
        FUNC = self.FuncType([lltype.SingleFloat], lltype.SingleFloat)
        FPTR = self.Ptr(FUNC)
        calldescr = self.cpu.calldescrof(FUNC, FUNC.ARGS, FUNC.RESULT,
                                         EffectInfo.MOST_GENERAL)
        funcbox = self.get_funcbox(self.cpu, f)
        ivalue = longlong.singlefloat2int(value)
        iexpected = longlong.singlefloat2int(expected)
        res = self.execute_operation(rop.CALL, [funcbox, BoxInt(ivalue)],
                                     'int', descr=calldescr)
        assert res.value == iexpected

    def test_free_loop_and_bridges(self):
        if not isinstance(self.cpu, AbstractLLCPU):
            py.test.skip("not a subclass of llmodel.AbstractLLCPU")
        if hasattr(self.cpu, 'setup_once'):
            self.cpu.setup_once()
        mem0 = self.cpu.asmmemmgr.total_mallocs
        looptoken = self.test_compile_bridge()
        mem1 = self.cpu.asmmemmgr.total_mallocs
        self.cpu.free_loop_and_bridges(looptoken.compiled_loop_token)
        mem2 = self.cpu.asmmemmgr.total_mallocs
        assert mem2 < mem1
        assert mem2 == mem0

    def test_memoryerror(self):
        excdescr = BasicFailDescr(666)
        self.cpu.propagate_exception_descr = excdescr
        self.cpu.setup_once()    # xxx redo it, because we added
                                 # propagate_exception
        i0 = BoxInt()
        p0 = BoxPtr()
        operations = [
            ResOperation(rop.NEWUNICODE, [i0], p0),
            ResOperation(rop.FINISH, [p0], None, descr=BasicFinalDescr(1))
            ]
        inputargs = [i0]
        looptoken = JitCellToken()
        self.cpu.compile_loop(inputargs, operations, looptoken)
        # overflowing value:
        unisize = self.cpu.gc_ll_descr.unicode_descr.itemsize
        assert unisize in (2, 4)
        deadframe = self.cpu.execute_token(looptoken, sys.maxint // unisize + 1)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == excdescr.identifier
        exc = self.cpu.grab_exc_value(deadframe)
        assert not exc

    def test_math_sqrt(self):
        if not self.cpu.supports_floats:
            py.test.skip("requires floats")

        def math_sqrt(a):
            assert False, 'should not be called'
        from rpython.jit.codewriter.effectinfo import EffectInfo

        effectinfo = EffectInfo([], [], [], [], [], [], EffectInfo.EF_CANNOT_RAISE, EffectInfo.OS_MATH_SQRT)
        FPTR = self.Ptr(self.FuncType([lltype.Float], lltype.Float))
        func_ptr = llhelper(FPTR, math_sqrt)
        FUNC = deref(FPTR)
        funcbox = self.get_funcbox(self.cpu, func_ptr)

        calldescr = self.cpu.calldescrof(FUNC, FUNC.ARGS, FUNC.RESULT, effectinfo)
        testcases = [(4.0, 2.0), (6.25, 2.5)]
        for arg, expected in testcases:
            res = self.execute_operation(rop.CALL,
                        [funcbox, boxfloat(arg)],
                         'float', descr=calldescr)
            assert res.getfloat() == expected

    def test_compile_loop_with_target(self):
        i0 = BoxInt()
        i1 = BoxInt()
        i2 = BoxInt()
        i3 = BoxInt()
        looptoken = JitCellToken()
        targettoken1 = TargetToken()
        targettoken2 = TargetToken()
        faildescr = BasicFailDescr(2)
        operations = [
            ResOperation(rop.LABEL, [i0], None, descr=targettoken1),
            ResOperation(rop.INT_ADD, [i0, ConstInt(1)], i1),
            ResOperation(rop.INT_LE, [i1, ConstInt(9)], i2),
            ResOperation(rop.GUARD_TRUE, [i2], None, descr=faildescr),
            ResOperation(rop.LABEL, [i1], None, descr=targettoken2),
            ResOperation(rop.INT_GE, [i1, ConstInt(0)], i3),
            ResOperation(rop.GUARD_TRUE, [i3], None, descr=BasicFailDescr(3)),
            ResOperation(rop.JUMP, [i1], None, descr=targettoken1),
            ]
        inputargs = [i0]
        operations[3].setfailargs([i1])
        operations[6].setfailargs([i1])

        self.cpu.compile_loop(inputargs, operations, looptoken)
        deadframe = self.cpu.execute_token(looptoken, 2)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == 2
        res = self.cpu.get_int_value(deadframe, 0)
        assert res == 10

        inputargs2 = [i0]
        operations2 = [
            ResOperation(rop.INT_SUB, [i0, ConstInt(20)], i2),
            ResOperation(rop.JUMP, [i2], None, descr=targettoken2),
            ]
        self.cpu.compile_bridge(faildescr, inputargs2, operations2, looptoken)

        deadframe = self.cpu.execute_token(looptoken, 2)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == 3
        res = self.cpu.get_int_value(deadframe, 0)
        assert res == -10

    def test_int_force_ge_zero(self):
        ops = """
        [i0]
        i1 = int_force_ge_zero(i0)    # but forced to be in a register
        finish(i1, descr=descr)
        """
        descr = BasicFinalDescr()
        loop = parse(ops, self.cpu, namespace=locals())
        looptoken = JitCellToken()
        self.cpu.compile_loop(loop.inputargs, loop.operations, looptoken)
        for inp, outp in [(2,2), (-3, 0)]:
            deadframe = self.cpu.execute_token(looptoken, inp)
            assert outp == self.cpu.get_int_value(deadframe, 0)

    def test_int_signext(self):
        numbytes_cases = [1, 2] if IS_32_BIT else [1, 2, 4]
        for spill in ["", "force_spill(i1)"]:
          for numbytes in numbytes_cases:
            print (spill, numbytes)
            ops = """
            [i0]
            i1 = int_sub(i0, 0)     # force in register
            %s
            i2 = int_signext(i1, %d)
            finish(i2, descr=descr)
            """ % (spill, numbytes)
            descr = BasicFinalDescr()
            loop = parse(ops, self.cpu, namespace=locals())
            looptoken = JitCellToken()
            self.cpu.compile_loop(loop.inputargs, loop.operations, looptoken)
            test_cases = [random.randrange(-sys.maxint-1, sys.maxint+1)
                          for _ in range(100)]
            for test_case in test_cases:
                deadframe = self.cpu.execute_token(looptoken, test_case)
                got = self.cpu.get_int_value(deadframe, 0)
                expected = heaptracker.int_signext(test_case, numbytes)
                assert got == expected

    def test_compile_asmlen(self):
        if not isinstance(self.cpu, AbstractLLCPU):
            py.test.skip("pointless test on non-asm")
        from rpython.jit.backend.tool.viewcode import machine_code_dump, ObjdumpNotFound
        import ctypes
        targettoken = TargetToken()
        ops = """
        [i2]
        i0 = same_as(i2)    # but forced to be in a register
        label(i0, descr=targettoken)
        i1 = int_add(i0, i0)
        guard_true(i1, descr=faildescr) [i1]
        jump(i1, descr=targettoken)
        """
        faildescr = BasicFailDescr(2)
        loop = parse(ops, self.cpu, namespace=locals())
        bridge_ops = """
        [i0]
        jump(i0, descr=targettoken)
        """
        bridge = parse(bridge_ops, self.cpu, namespace=locals())
        looptoken = JitCellToken()
        self.cpu.assembler.set_debug(False)
        info = self.cpu.compile_loop(loop.inputargs, loop.operations, looptoken)
        bridge_info = self.cpu.compile_bridge(faildescr, bridge.inputargs,
                                              bridge.operations,
                                              looptoken)
        self.cpu.assembler.set_debug(True) # always on untranslated
        assert info.asmlen != 0
        cpuname = autodetect()
        # XXX we have to check the precise assembler, otherwise
        # we don't quite know if borders are correct

        def checkops(mc, ops, alt_ops=None):
            if len(mc) != len(ops) and alt_ops is not None:
                ops = alt_ops
            assert len(mc) == len(ops)
            for i in range(len(mc)):
                if ops[i] == '*':
                    continue # ingore ops marked as '*', i.e. inline constants
                assert mc[i].split("\t")[2].startswith(ops[i])

        data = ctypes.string_at(info.asmaddr, info.asmlen)
        try:
            mc = list(machine_code_dump(data, info.asmaddr, cpuname))
            lines = [line for line in mc if line.count('\t') >= 2]
            checkops(lines, self.add_loop_instructions)
            data = ctypes.string_at(bridge_info.asmaddr, bridge_info.asmlen)
            mc = list(machine_code_dump(data, bridge_info.asmaddr, cpuname))
            lines = [line for line in mc if line.count('\t') >= 2]
            checkops(lines, self.bridge_loop_instructions,
                            self.bridge_loop_instructions_alternative)
        except ObjdumpNotFound:
            py.test.skip("requires (g)objdump")



    def test_compile_bridge_with_target(self):
        # This test creates a loopy piece of code in a bridge, and builds another
        # unrelated loop that ends in a jump directly to this loopy bit of code.
        # It catches a case in which we underestimate the needed frame_depth across
        # the cross-loop JUMP, because we estimate it based on the frame_depth stored
        # in the original loop.
        i0 = BoxInt()
        i1 = BoxInt()
        looptoken1 = JitCellToken()
        targettoken1 = TargetToken()
        faildescr1 = BasicFailDescr(2)
        inputargs = [i0]
        operations = [
            ResOperation(rop.INT_LE, [i0, ConstInt(1)], i1),
            ResOperation(rop.GUARD_TRUE, [i1], None, descr=faildescr1),
            ResOperation(rop.FINISH, [i0], None, descr=BasicFinalDescr(1234)),
            ]
        operations[1].setfailargs([i0])
        self.cpu.compile_loop(inputargs, operations, looptoken1)

        def func(a, b, c, d, e, f, g, h, i):
            assert a + 2 == b
            assert a + 4 == c
            assert a + 6 == d
            assert a + 8 == e
            assert a + 10 == f
            assert a + 12 == g
            assert a + 14 == h
            assert a + 16 == i
        FPTR = self.Ptr(self.FuncType([lltype.Signed]*9, lltype.Void))
        func_ptr = llhelper(FPTR, func)
        cpu = self.cpu
        calldescr = cpu.calldescrof(deref(FPTR), (lltype.Signed,)*9, lltype.Void,
                                    EffectInfo.MOST_GENERAL)
        funcbox = self.get_funcbox(cpu, func_ptr)

        i0 = BoxInt(); i1 = BoxInt(); i2 = BoxInt(); i3 = BoxInt(); i4 = BoxInt()
        i5 = BoxInt(); i6 = BoxInt(); i7 = BoxInt(); i8 = BoxInt(); i9 = BoxInt()
        i10 = BoxInt(); i11 = BoxInt(); i12 = BoxInt(); i13 = BoxInt(); i14 = BoxInt()
        i15 = BoxInt(); i16 = BoxInt(); i17 = BoxInt(); i18 = BoxInt(); i19 = BoxInt()
        i20 = BoxInt()
        inputargs = [i0]
        operations2 = [
            ResOperation(rop.LABEL, [i0], None, descr=targettoken1),
            ResOperation(rop.INT_ADD, [i0, ConstInt(1)], i1),
            ResOperation(rop.INT_ADD, [i1, ConstInt(1)], i2),
            ResOperation(rop.INT_ADD, [i2, ConstInt(1)], i3),
            ResOperation(rop.INT_ADD, [i3, ConstInt(1)], i4),
            ResOperation(rop.INT_ADD, [i4, ConstInt(1)], i5),
            ResOperation(rop.INT_ADD, [i5, ConstInt(1)], i6),
            ResOperation(rop.INT_ADD, [i6, ConstInt(1)], i7),
            ResOperation(rop.INT_ADD, [i7, ConstInt(1)], i8),
            ResOperation(rop.INT_ADD, [i8, ConstInt(1)], i9),
            ResOperation(rop.INT_ADD, [i9, ConstInt(1)], i10),
            ResOperation(rop.INT_ADD, [i10, ConstInt(1)], i11),
            ResOperation(rop.INT_ADD, [i11, ConstInt(1)], i12),
            ResOperation(rop.INT_ADD, [i12, ConstInt(1)], i13),
            ResOperation(rop.INT_ADD, [i13, ConstInt(1)], i14),
            ResOperation(rop.INT_ADD, [i14, ConstInt(1)], i15),
            ResOperation(rop.INT_ADD, [i15, ConstInt(1)], i16),
            ResOperation(rop.INT_ADD, [i16, ConstInt(1)], i17),
            ResOperation(rop.INT_ADD, [i17, ConstInt(1)], i18),
            ResOperation(rop.INT_ADD, [i18, ConstInt(1)], i19),
            ResOperation(rop.CALL, [funcbox, i2, i4, i6, i8, i10, i12, i14, i16, i18],
                         None, descr=calldescr),
            ResOperation(rop.CALL, [funcbox, i2, i4, i6, i8, i10, i12, i14, i16, i18],
                         None, descr=calldescr),
            ResOperation(rop.INT_LT, [i19, ConstInt(100)], i20),
            ResOperation(rop.GUARD_TRUE, [i20], None, descr=BasicFailDescr(42)),
            ResOperation(rop.JUMP, [i19], None, descr=targettoken1),
            ]
        operations2[-2].setfailargs([])
        self.cpu.compile_bridge(faildescr1, inputargs, operations2, looptoken1)

        looptoken2 = JitCellToken()
        inputargs = [BoxInt()]
        operations3 = [
            ResOperation(rop.JUMP, [ConstInt(0)], None, descr=targettoken1),
            ]
        self.cpu.compile_loop(inputargs, operations3, looptoken2)

        deadframe = self.cpu.execute_token(looptoken2, -9)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == 42

    def test_wrong_guard_nonnull_class(self):
        t_box, T_box = self.alloc_instance(self.T)
        null_box = self.null_instance()
        faildescr = BasicFailDescr(42)
        operations = [
            ResOperation(rop.GUARD_NONNULL_CLASS, [t_box, T_box], None,
                                                        descr=faildescr),
            ResOperation(rop.FINISH, [], None, descr=BasicFinalDescr(1))]
        operations[0].setfailargs([])
        looptoken = JitCellToken()
        inputargs = [t_box]
        self.cpu.compile_loop(inputargs, operations, looptoken)
        operations = [
            ResOperation(rop.FINISH, [], None, descr=BasicFinalDescr(99))
        ]
        self.cpu.compile_bridge(faildescr, [], operations, looptoken)
        deadframe = self.cpu.execute_token(looptoken, null_box.getref_base())
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == 99

    def test_raw_load_int(self):
        from rpython.rlib import rawstorage
        for T in [rffi.UCHAR, rffi.SIGNEDCHAR,
                  rffi.USHORT, rffi.SHORT,
                  rffi.UINT, rffi.INT,
                  rffi.ULONG, rffi.LONG]:
            ops = """
            [i0, i1]
            i2 = raw_load(i0, i1, descr=arraydescr)
            finish(i2)
            """
            arraydescr = self.cpu.arraydescrof(rffi.CArray(T))
            p = rawstorage.alloc_raw_storage(31)
            for i in range(31):
                p[i] = '\xDD'
            value = rffi.cast(T, -0x4243444546474849)
            rawstorage.raw_storage_setitem(p, 16, value)
            got = self.cpu.bh_raw_load_i(rffi.cast(lltype.Signed, p), 16,
                                         arraydescr)
            assert got == rffi.cast(lltype.Signed, value)
            #
            loop = parse(ops, self.cpu, namespace=locals())
            looptoken = JitCellToken()
            self.cpu.compile_loop(loop.inputargs, loop.operations, looptoken)
            deadframe = self.cpu.execute_token(looptoken,
                                               rffi.cast(lltype.Signed, p), 16)
            result = self.cpu.get_int_value(deadframe, 0)
            assert result == rffi.cast(lltype.Signed, value)
            rawstorage.free_raw_storage(p)

    def test_raw_load_float(self):
        if not self.cpu.supports_floats:
            py.test.skip("requires floats")
        from rpython.rlib import rawstorage
        for T in [rffi.DOUBLE]:
            ops = """
            [i0, i1]
            f2 = raw_load(i0, i1, descr=arraydescr)
            finish(f2)
            """
            arraydescr = self.cpu.arraydescrof(rffi.CArray(T))
            p = rawstorage.alloc_raw_storage(31)
            for i in range(31):
                p[i] = '\xDD'
            value = rffi.cast(T, 1.12e20)
            rawstorage.raw_storage_setitem(p, 16, value)
            got = self.cpu.bh_raw_load_f(rffi.cast(lltype.Signed, p), 16,
                                         arraydescr)
            got = longlong.getrealfloat(got)
            assert got == rffi.cast(lltype.Float, value)
            #
            loop = parse(ops, self.cpu, namespace=locals())
            looptoken = JitCellToken()
            self.cpu.compile_loop(loop.inputargs, loop.operations, looptoken)
            deadframe = self.cpu.execute_token(looptoken,
                                               rffi.cast(lltype.Signed, p), 16)
            result = self.cpu.get_float_value(deadframe, 0)
            result = longlong.getrealfloat(result)
            assert result == rffi.cast(lltype.Float, value)
            rawstorage.free_raw_storage(p)

    def test_raw_load_singlefloat(self):
        if not self.cpu.supports_singlefloats:
            py.test.skip("requires singlefloats")
        from rpython.rlib import rawstorage
        for T in [rffi.FLOAT]:
            ops = """
            [i0, i1]
            i2 = raw_load(i0, i1, descr=arraydescr)
            finish(i2)
            """
            arraydescr = self.cpu.arraydescrof(rffi.CArray(T))
            p = rawstorage.alloc_raw_storage(31)
            for i in range(31):
                p[i] = '\xDD'
            value = rffi.cast(T, 1.12e20)
            rawstorage.raw_storage_setitem(p, 16, value)
            got = self.cpu.bh_raw_load_i(rffi.cast(lltype.Signed, p), 16,
                                         arraydescr)
            assert got == longlong.singlefloat2int(value)
            #
            loop = parse(ops, self.cpu, namespace=locals())
            looptoken = JitCellToken()
            self.cpu.compile_loop(loop.inputargs, loop.operations, looptoken)
            deadframe = self.cpu.execute_token(looptoken,
                                               rffi.cast(lltype.Signed, p), 16)
            result = self.cpu.get_int_value(deadframe, 0)
            assert result == longlong.singlefloat2int(value)
            rawstorage.free_raw_storage(p)

    def test_raw_store_int(self):
        from rpython.rlib import rawstorage
        for T in [rffi.UCHAR, rffi.SIGNEDCHAR,
                  rffi.USHORT, rffi.SHORT,
                  rffi.UINT, rffi.INT,
                  rffi.ULONG, rffi.LONG]:
            arraydescr = self.cpu.arraydescrof(rffi.CArray(T))
            p = rawstorage.alloc_raw_storage(31)
            value = (-0x4243444546474849) & sys.maxint
            self.cpu.bh_raw_store_i(rffi.cast(lltype.Signed, p), 16, value,
                                    arraydescr)
            result = rawstorage.raw_storage_getitem(T, p, 16)
            assert result == rffi.cast(T, value)
            rawstorage.free_raw_storage(p)
            #
            ops = """
            [i0, i1, i2]
            raw_store(i0, i1, i2, descr=arraydescr)
            finish()
            """
            p = rawstorage.alloc_raw_storage(31)
            for i in range(31):
                p[i] = '\xDD'
            loop = parse(ops, self.cpu, namespace=locals())
            looptoken = JitCellToken()
            self.cpu.compile_loop(loop.inputargs, loop.operations, looptoken)
            self.cpu.execute_token(looptoken,
                                   rffi.cast(lltype.Signed, p), 16, value)
            result = rawstorage.raw_storage_getitem(T, p, 16)
            assert result == rffi.cast(T, value)
            rawstorage.free_raw_storage(p)

    def test_raw_store_float(self):
        if not self.cpu.supports_floats:
            py.test.skip("requires floats")
        from rpython.rlib import rawstorage
        for T in [rffi.DOUBLE]:
            arraydescr = self.cpu.arraydescrof(rffi.CArray(T))
            p = rawstorage.alloc_raw_storage(31)
            value = 1.23e20
            self.cpu.bh_raw_store_f(rffi.cast(lltype.Signed, p), 16,
                                    longlong.getfloatstorage(value),
                                    arraydescr)
            result = rawstorage.raw_storage_getitem(T, p, 16)
            assert result == rffi.cast(T, value)
            rawstorage.free_raw_storage(p)
            #
            ops = """
            [i0, i1, f2]
            raw_store(i0, i1, f2, descr=arraydescr)
            finish()
            """
            p = rawstorage.alloc_raw_storage(31)
            for i in range(31):
                p[i] = '\xDD'
            loop = parse(ops, self.cpu, namespace=locals())
            looptoken = JitCellToken()
            self.cpu.compile_loop(loop.inputargs, loop.operations, looptoken)
            self.cpu.execute_token(looptoken,
                                   rffi.cast(lltype.Signed, p), 16,
                                   longlong.getfloatstorage(value))
            result = rawstorage.raw_storage_getitem(T, p, 16)
            assert result == rffi.cast(T, value)
            rawstorage.free_raw_storage(p)

    def test_raw_store_singlefloat(self):
        if not self.cpu.supports_singlefloats:
            py.test.skip("requires singlefloats")
        from rpython.rlib import rawstorage
        for T in [rffi.FLOAT]:
            arraydescr = self.cpu.arraydescrof(rffi.CArray(T))
            p = rawstorage.alloc_raw_storage(31)
            value = rffi.cast(T, 1.23e20)
            self.cpu.bh_raw_store_i(rffi.cast(lltype.Signed, p), 16,
                                    longlong.singlefloat2int(value),
                                    arraydescr)
            result = rawstorage.raw_storage_getitem(T, p, 16)
            assert (rffi.cast(lltype.Float, result) ==
                    rffi.cast(lltype.Float, value))
            rawstorage.free_raw_storage(p)
            #
            ops = """
            [i0, i1, i2]
            raw_store(i0, i1, i2, descr=arraydescr)
            finish()
            """
            p = rawstorage.alloc_raw_storage(31)
            for i in range(31):
                p[i] = '\xDD'
            loop = parse(ops, self.cpu, namespace=locals())
            looptoken = JitCellToken()
            self.cpu.compile_loop(loop.inputargs, loop.operations, looptoken)
            self.cpu.execute_token(looptoken,
                                   rffi.cast(lltype.Signed, p), 16,
                                   longlong.singlefloat2int(value))
            result = rawstorage.raw_storage_getitem(T, p, 16)
            assert (rffi.cast(lltype.Float, result) ==
                    rffi.cast(lltype.Float, value))
            rawstorage.free_raw_storage(p)

    def test_forcing_op_with_fail_arg_in_reg(self):
        values = []
        def maybe_force(token, flag):
            deadframe = self.cpu.force(token)
            values.append(self.cpu.get_int_value(deadframe, 0))
            return 42

        FUNC = self.FuncType([llmemory.GCREF, lltype.Signed], lltype.Signed)
        func_ptr = llhelper(lltype.Ptr(FUNC), maybe_force)
        funcbox = self.get_funcbox(self.cpu, func_ptr).constbox()
        calldescr = self.cpu.calldescrof(FUNC, FUNC.ARGS, FUNC.RESULT,
                                         EffectInfo.MOST_GENERAL)
        i0 = BoxInt()
        i1 = BoxInt()
        i2 = BoxInt()
        tok = BoxPtr()
        faildescr = BasicFailDescr(23)
        ops = [
        ResOperation(rop.FORCE_TOKEN, [], tok),
        ResOperation(rop.CALL_MAY_FORCE, [funcbox, tok, i1], i2,
                     descr=calldescr),
        ResOperation(rop.GUARD_NOT_FORCED, [], None, descr=faildescr),
        ResOperation(rop.FINISH, [i2], None, descr=BasicFinalDescr(0))
        ]
        ops[2].setfailargs([i2])
        looptoken = JitCellToken()
        self.cpu.compile_loop([i0, i1], ops, looptoken)
        deadframe = self.cpu.execute_token(looptoken, 20, 0)
        fail = self.cpu.get_latest_descr(deadframe)
        assert fail.identifier == 23
        assert self.cpu.get_int_value(deadframe, 0) == 42

    def test_compile_bridge_while_running(self):
        def func():
            bridge = parse("""
            [i1, i2, px]
            i3 = int_add(i1, i2)
            i4 = int_add(i1, i3)
            i5 = int_add(i1, i4)
            i6 = int_add(i4, i5)
            i7 = int_add(i6, i5)
            i8 = int_add(i5, 1)
            i9 = int_add(i8, 1)
            force_spill(i1)
            force_spill(i2)
            force_spill(i3)
            force_spill(i4)
            force_spill(i5)
            force_spill(i6)
            force_spill(i7)
            force_spill(i8)
            force_spill(i9)
            call(ConstClass(func2_ptr), 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, descr=calldescr2)
            guard_true(i1, descr=guarddescr) [i1, i2, i3, i4, i5, i6, i7, i8, i9, px]
            finish(i1, descr=finaldescr)
            """, namespace={'finaldescr': finaldescr, 'calldescr2': calldescr2,
                            'guarddescr': guarddescr, 'func2_ptr': func2_ptr})
            self.cpu.compile_bridge(faildescr, bridge.inputargs,
                                    bridge.operations, looptoken)

        cpu = self.cpu
        finaldescr = BasicFinalDescr(13)
        finaldescr2 = BasicFinalDescr(133)
        guarddescr = BasicFailDescr(8)

        FUNC = self.FuncType([], lltype.Void)
        FPTR = self.Ptr(FUNC)
        func_ptr = llhelper(FPTR, func)
        calldescr = cpu.calldescrof(FUNC, FUNC.ARGS, FUNC.RESULT,
                                    EffectInfo.MOST_GENERAL)

        def func2(a, b, c, d, e, f, g, h, i, j, k, l):
            pass

        FUNC2 = self.FuncType([lltype.Signed] * 12, lltype.Void)
        FPTR2 = self.Ptr(FUNC2)
        func2_ptr = llhelper(FPTR2, func2)
        calldescr2 = cpu.calldescrof(FUNC2, FUNC2.ARGS, FUNC2.RESULT,
                                    EffectInfo.MOST_GENERAL)

        faildescr = BasicFailDescr(0)

        looptoken = JitCellToken()
        loop = parse("""
        [i0, i1, i2]
        call(ConstClass(func_ptr), descr=calldescr)
        px = force_token()
        guard_true(i0, descr=faildescr) [i1, i2, px]
        finish(i2, descr=finaldescr2)
        """, namespace=locals())
        self.cpu.compile_loop(loop.inputargs, loop.operations, looptoken)
        frame = self.cpu.execute_token(looptoken, 0, 0, 3)
        assert self.cpu.get_latest_descr(frame) is guarddescr

        if not isinstance(self.cpu, AbstractLLCPU):
            py.test.skip("pointless test on non-asm")

        frame = lltype.cast_opaque_ptr(jitframe.JITFRAMEPTR, frame)
        assert len(frame.jf_frame) == frame.jf_frame_info.jfi_frame_depth
        ref = self.cpu.get_ref_value(frame, 9)
        token = lltype.cast_opaque_ptr(jitframe.JITFRAMEPTR, ref)
        assert token != frame
        token = token.resolve()
        assert token == frame

    def test_compile_bridge_while_running_guard_no_exc(self):
        xtp = lltype.malloc(rclass.OBJECT_VTABLE, immortal=True)
        xtp.subclassrange_min = 1
        xtp.subclassrange_max = 3
        X = lltype.GcStruct('X', ('parent', rclass.OBJECT),
                            hints={'vtable':  xtp._obj})
        xptr = lltype.cast_opaque_ptr(llmemory.GCREF, lltype.malloc(X))
        def raising():
            bridge = parse("""
            [i1, i2]
            px = guard_exception(ConstClass(xtp), descr=faildescr2) [i1, i2]
            i3 = int_add(i1, i2)
            i4 = int_add(i1, i3)
            i5 = int_add(i1, i4)
            i6 = int_add(i4, i5)
            i7 = int_add(i6, i5)
            i8 = int_add(i5, 1)
            i9 = int_add(i8, 1)
            force_spill(i1)
            force_spill(i2)
            force_spill(i3)
            force_spill(i4)
            force_spill(i5)
            force_spill(i6)
            force_spill(i7)
            force_spill(i8)
            force_spill(i9)
            guard_true(i9) [i3, i4, i5, i6, i7, i8, i9]
            finish(i9, descr=finaldescr)
            """, namespace={'finaldescr': BasicFinalDescr(42),
                            'faildescr2': BasicFailDescr(1),
                            'xtp': xtp
            })
            self.cpu.compile_bridge(faildescr, bridge.inputargs,
                                    bridge.operations, looptoken)
            raise LLException(xtp, xptr)

        faildescr = BasicFailDescr(0)
        FUNC = self.FuncType([], lltype.Void)
        raising_ptr = llhelper(lltype.Ptr(FUNC), raising)
        calldescr = self.cpu.calldescrof(FUNC, FUNC.ARGS, FUNC.RESULT,
                                         EffectInfo.MOST_GENERAL)

        looptoken = JitCellToken()
        loop = parse("""
        [i0, i1, i2]
        call(ConstClass(raising_ptr), descr=calldescr)
        guard_no_exception(descr=faildescr) [i1, i2]
        finish(i2, descr=finaldescr2)
        """, namespace={'raising_ptr': raising_ptr,
                        'calldescr': calldescr,
                        'faildescr': faildescr,
                        'finaldescr2': BasicFinalDescr(1)})

        self.cpu.compile_loop(loop.inputargs, loop.operations, looptoken)
        frame = self.cpu.execute_token(looptoken, 1, 2, 3)
        descr = self.cpu.get_latest_descr(frame)
        assert descr.identifier == 42
        assert not self.cpu.grab_exc_value(frame)

    def test_setarrayitem_raw_short(self):
        # setarrayitem_raw(140737353744432, 0, 30583, descr=<ArrayS 2>)
        A = rffi.CArray(rffi.SHORT)
        arraydescr = self.cpu.arraydescrof(A)
        a = lltype.malloc(A, 2, flavor='raw')
        a[0] = rffi.cast(rffi.SHORT, 666)
        a[1] = rffi.cast(rffi.SHORT, 777)
        addr = llmemory.cast_ptr_to_adr(a)
        a_int = heaptracker.adr2int(addr)
        print 'a_int:', a_int
        self.execute_operation(rop.SETARRAYITEM_RAW,
                               [ConstInt(a_int), ConstInt(0), ConstInt(-7654)],
                               'void', descr=arraydescr)
        assert rffi.cast(lltype.Signed, a[0]) == -7654
        assert rffi.cast(lltype.Signed, a[1]) == 777
        lltype.free(a, flavor='raw')

    def test_increment_debug_counter(self):
        foo = lltype.malloc(rffi.CArray(lltype.Signed), 1, flavor='raw')
        foo[0] = 1789200
        self.execute_operation(rop.INCREMENT_DEBUG_COUNTER,
                               [ConstInt(rffi.cast(lltype.Signed, foo))],
                               'void')
        assert foo[0] == 1789201
        lltype.free(foo, flavor='raw')

    def test_cast_float_to_singlefloat(self):
        if not self.cpu.supports_singlefloats:
            py.test.skip("requires singlefloats")
        res = self.execute_operation(rop.CAST_FLOAT_TO_SINGLEFLOAT,
                                   [boxfloat(12.5)], 'int')
        assert res.getint() == struct.unpack("I", struct.pack("f", 12.5))[0]

    def test_zero_ptr_field(self):
        if not isinstance(self.cpu, AbstractLLCPU):
            py.test.skip("llgraph can't do zero_ptr_field")
        T = lltype.GcStruct('T')
        S = lltype.GcStruct('S', ('x', lltype.Ptr(T)))
        tdescr = self.cpu.sizeof(T)
        sdescr = self.cpu.sizeof(S)
        fielddescr = self.cpu.fielddescrof(S, 'x')
        loop = parse("""
        []
        p0 = new(descr=tdescr)
        p1 = new(descr=sdescr)
        setfield_gc(p1, p0, descr=fielddescr)
        zero_ptr_field(p1, %d)
        finish(p1)
        """ % fielddescr.offset, namespace=locals())
        looptoken = JitCellToken()
        self.cpu.compile_loop(loop.inputargs, loop.operations, looptoken)
        deadframe = self.cpu.execute_token(looptoken)
        ref = self.cpu.get_ref_value(deadframe, 0)
        s = lltype.cast_opaque_ptr(lltype.Ptr(S), ref)
        assert not s.x

    def test_zero_ptr_field_2(self):
        if not isinstance(self.cpu, AbstractLLCPU):
            py.test.skip("llgraph does not do zero_ptr_field")

        from rpython.jit.backend.llsupport import symbolic
        S = lltype.GcStruct('S', ('x', lltype.Signed),
                                 ('p', llmemory.GCREF),
                                 ('y', lltype.Signed))
        s = lltype.malloc(S)
        s.x = -1296321
        s.y = -4398176
        s_ref = lltype.cast_opaque_ptr(llmemory.GCREF, s)
        s.p = s_ref
        ofs_p, _ = symbolic.get_field_token(S, 'p', False)
        #
        self.execute_operation(rop.ZERO_PTR_FIELD, [
            BoxPtr(s_ref), ConstInt(ofs_p)],   # OK for now to assume that the
            'void')                            # 2nd argument is a constant
        #
        assert s.x == -1296321
        assert s.p == lltype.nullptr(llmemory.GCREF.TO)
        assert s.y == -4398176

    def test_zero_array(self):
        if not isinstance(self.cpu, AbstractLLCPU):
            py.test.skip("llgraph does not do zero_array")

        PAIR = lltype.Struct('PAIR', ('a', lltype.Signed), ('b', lltype.Signed))
        for OF in [lltype.Signed, rffi.INT, rffi.SHORT, rffi.UCHAR, PAIR]:
            A = lltype.GcArray(OF)
            arraydescr = self.cpu.arraydescrof(A)
            a = lltype.malloc(A, 100)
            addr = llmemory.cast_ptr_to_adr(a)
            a_int = heaptracker.adr2int(addr)
            a_ref = lltype.cast_opaque_ptr(llmemory.GCREF, a)
            for (start, length) in [(0, 100), (49, 49), (1, 98),
                                    (15, 9), (10, 10), (47, 0),
                                    (0, 4)]:
                for cls1 in [ConstInt, BoxInt]:
                    for cls2 in [ConstInt, BoxInt]:
                        print 'a_int:', a_int
                        print 'of:', OF
                        print 'start:', cls1.__name__, start
                        print 'length:', cls2.__name__, length
                        for i in range(100):
                            if OF == PAIR:
                                a[i].a = a[i].b = -123456789
                            else:
                                a[i] = rffi.cast(OF, -123456789)
                        startbox = cls1(start)
                        lengthbox = cls2(length)
                        if cls1 == cls2 and start == length:
                            lengthbox = startbox    # same box!
                        self.execute_operation(rop.ZERO_ARRAY,
                                               [BoxPtr(a_ref),
                                                startbox,
                                                lengthbox],
                                           'void', descr=arraydescr)
                        assert len(a) == 100
                        for i in range(100):
                            val = (0 if start <= i < start + length
                                     else -123456789)
                            if OF == PAIR:
                                assert a[i].a == a[i].b == val
                            else:
                                assert a[i] == rffi.cast(OF, val)
