import py, sys
from rpython.rlib.objectmodel import instantiate
from rpython.jit.metainterp import compile, resume
from rpython.jit.metainterp.history import AbstractDescr, ConstInt, BoxInt, TreeLoop
from rpython.jit.metainterp.optimize import InvalidLoop
from rpython.jit.metainterp.optimizeopt import build_opt_chain
from rpython.jit.metainterp.optimizeopt.test.test_util import (
    LLtypeMixin, BaseTest, convert_old_style_to_targets)
from rpython.jit.metainterp.optimizeopt.test.test_optimizebasic import \
    FakeMetaInterpStaticData
from rpython.jit.metainterp.resoperation import rop, opname, oparity


def test_build_opt_chain():
    def check(chain, expected_names):
        names = [opt.__class__.__name__ for opt in chain]
        assert names == expected_names
    #
    metainterp_sd = FakeMetaInterpStaticData(None)
    chain, _ = build_opt_chain(metainterp_sd, "")
    check(chain, ["OptSimplify"])
    #
    chain, _ = build_opt_chain(metainterp_sd, "")
    check(chain, ["OptSimplify"])
    #
    chain, _ = build_opt_chain(metainterp_sd, "")
    check(chain, ["OptSimplify"])
    #
    chain, _ = build_opt_chain(metainterp_sd, "heap:intbounds")
    check(chain, ["OptIntBounds", "OptHeap", "OptSimplify"])
    #
    chain, unroll = build_opt_chain(metainterp_sd, "unroll")
    check(chain, ["OptSimplify"])
    assert unroll
    #
    chain, _ = build_opt_chain(metainterp_sd, "aaa:bbb")
    check(chain, ["OptSimplify"])


# ____________________________________________________________


class BaseTestWithUnroll(BaseTest):
    enable_opts = "intbounds:rewrite:virtualize:string:earlyforce:pure:heap:unroll"

    def optimize_loop(self, ops, expected, expected_preamble=None,
                      call_pure_results=None, expected_short=None):
        loop = self.parse(ops, postprocess=self.postprocess)
        if expected != "crash!":
            expected = self.parse(expected)
        if expected_preamble:
            expected_preamble = self.parse(expected_preamble)
        if expected_short:
            # the short preamble doesn't have fail descrs, they are patched in when it is used
            expected_short = self.parse(expected_short, want_fail_descr=False)

        preamble = self.unroll_and_optimize(loop, call_pure_results)

        #
        print
        print "Preamble:"
        if preamble.operations:
            print '\n'.join([str(o) for o in preamble.operations])
        else:
            print 'Failed!'
        print
        print "Loop:"
        print '\n'.join([str(o) for o in loop.operations])
        print
        if expected_short:
            print "Short Preamble:"
            short = loop.operations[0].getdescr().short_preamble
            print '\n'.join([str(o) for o in short])
            print

        assert expected != "crash!", "should have raised an exception"
        self.assert_equal(loop, convert_old_style_to_targets(expected, jump=True))
        assert loop.operations[0].getdescr() == loop.operations[-1].getdescr()
        if expected_preamble:
            self.assert_equal(preamble, convert_old_style_to_targets(expected_preamble, jump=False),
                              text_right='expected preamble')
            assert preamble.operations[-1].getdescr() == loop.operations[0].getdescr()
        if expected_short:
            short_preamble = TreeLoop('short preamble')
            assert short[0].getopnum() == rop.LABEL
            short_preamble.inputargs = short[0].getarglist()
            short_preamble.operations = short
            self.assert_equal(short_preamble, convert_old_style_to_targets(expected_short, jump=True),
                              text_right='expected short preamble')
            assert short[-1].getdescr() == loop.operations[0].getdescr()

        return loop

    def raises(self, e, fn, *args):
        return py.test.raises(e, fn, *args).value


class OptimizeOptTest(BaseTestWithUnroll):
    def test_simple(self):
        ops = """
        []
        f = escape()
        f0 = float_sub(f, 1.0)
        guard_value(f0, 0.0) [f0]
        escape(f)
        jump()
        """
        self.optimize_loop(ops, ops)

    def test_constant_propagate(self):
        ops = """
        []
        i0 = int_add(2, 3)
        i1 = int_is_true(i0)
        guard_true(i1) []
        i2 = int_is_zero(i1)
        guard_false(i2) []
        guard_value(i0, 5) []
        jump()
        """
        expected = """
        []
        jump()
        """
        self.optimize_loop(ops, expected)

    def test_constant_propagate_ovf(self):
        ops = """
        []
        i0 = int_add_ovf(2, 3)
        guard_no_overflow() []
        i1 = int_is_true(i0)
        guard_true(i1) []
        i2 = int_is_zero(i1)
        guard_false(i2) []
        guard_value(i0, 5) []
        jump()
        """
        expected = """
        []
        jump()
        """
        self.optimize_loop(ops, expected)

    def test_constfold_all(self):
        from rpython.jit.metainterp.executor import execute_nonspec
        import random
        for opnum in range(rop.INT_ADD, rop.SAME_AS+1):
            try:
                op = opname[opnum]
            except KeyError:
                continue
            if 'FLOAT' in op:
                continue
            args = []
            for _ in range(oparity[opnum]):
                args.append(random.randrange(1, 20))
            if opnum == rop.INT_SIGNEXT:
                # 2nd arg is number of bytes to extend from ---
                # must not be too random
                args[-1] = random.choice([1, 2] if sys.maxint < 2**32 else
                                         [1, 2, 4])
            ops = """
            []
            i1 = %s(%s)
            escape(i1)
            jump()
            """ % (op.lower(), ', '.join(map(str, args)))
            argboxes = [BoxInt(a) for a in args]
            expected_value = execute_nonspec(self.cpu, None, opnum,
                                             argboxes).getint()
            expected = """
            []
            escape(%d)
            jump()
            """ % expected_value
            self.optimize_loop(ops, expected)

    def test_reverse_of_cast_1(self):
        ops = """
        [i0]
        p0 = cast_int_to_ptr(i0)
        i1 = cast_ptr_to_int(p0)
        jump(i1)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected)

    def test_reverse_of_cast_2(self):
        ops = """
        [p0]
        i1 = cast_ptr_to_int(p0)
        p1 = cast_int_to_ptr(i1)
        jump(p1)
        """
        expected = """
        [p0]
        jump(p0)
        """
        self.optimize_loop(ops, expected)

    # ----------

    def test_remove_guard_class_1(self):
        ops = """
        [p0]
        guard_class(p0, ConstClass(node_vtable)) []
        guard_class(p0, ConstClass(node_vtable)) []
        jump(p0)
        """
        preamble = """
        [p0]
        guard_class(p0, ConstClass(node_vtable)) []
        jump(p0)
        """
        expected = """
        [p0]
        jump(p0)
        """
        self.optimize_loop(ops, expected, expected_preamble=preamble)

    def test_remove_guard_class_2(self):
        ops = """
        [i0]
        p0 = new_with_vtable(ConstClass(node_vtable))
        escape(p0)
        guard_class(p0, ConstClass(node_vtable)) []
        jump(i0)
        """
        expected = """
        [i0]
        p0 = new_with_vtable(ConstClass(node_vtable))
        escape(p0)
        jump(i0)
        """
        self.optimize_loop(ops, expected)

    def test_remove_guard_class_constant(self):
        ops = """
        [i0]
        p0 = same_as(ConstPtr(myptr))
        guard_class(p0, ConstClass(node_vtable)) []
        jump(i0)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected)

    def test_constant_boolrewrite_lt(self):
        ops = """
        [i0]
        i1 = int_lt(i0, 0)
        guard_true(i1) []
        i2 = int_ge(i0, 0)
        guard_false(i2) []
        jump(i0)
        """
        preamble = """
        [i0]
        i1 = int_lt(i0, 0)
        guard_true(i1) []
        jump(i0)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected, expected_preamble=preamble)

    def test_constant_boolrewrite_gt(self):
        ops = """
        [i0]
        i1 = int_gt(i0, 0)
        guard_true(i1) []
        i2 = int_le(i0, 0)
        guard_false(i2) []
        jump(i0)
        """
        preamble = """
        [i0]
        i1 = int_gt(i0, 0)
        guard_true(i1) []
        jump(i0)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected, expected_preamble=preamble)

    def test_constant_boolrewrite_reflex(self):
        ops = """
        [i0]
        i1 = int_gt(i0, 0)
        guard_true(i1) []
        i2 = int_lt(0, i0)
        guard_true(i2) []
        jump(i0)
        """
        preamble = """
        [i0]
        i1 = int_gt(i0, 0)
        guard_true(i1) []
        jump(i0)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected, expected_preamble=preamble)

    def test_constant_boolrewrite_reflex_invers(self):
        ops = """
        [i0]
        i1 = int_gt(i0, 0)
        guard_true(i1) []
        i2 = int_ge(0, i0)
        guard_false(i2) []
        jump(i0)
        """
        preamble = """
        [i0]
        i1 = int_gt(i0, 0)
        guard_true(i1) []
        jump(i0)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected, expected_preamble=preamble)

    def test_remove_consecutive_guard_value_constfold(self):
        ops = """
        []
        i0 = escape()
        guard_value(i0, 0) []
        i1 = int_add(i0, 1)
        guard_value(i1, 1) []
        i2 = int_add(i1, 2)
        escape(i2)
        jump()
        """
        expected = """
        []
        i0 = escape()
        guard_value(i0, 0) []
        escape(3)
        jump()
        """
        self.optimize_loop(ops, expected)

    def test_remove_guard_value_if_constant(self):
        ops = """
        [p1]
        guard_value(p1, ConstPtr(myptr)) []
        jump(p1)
        """
        expected = """
        []
        jump()
        """
        self.optimize_loop(ops, expected)

    def test_ooisnull_oononnull_1(self):
        ops = """
        [p0]
        guard_class(p0, ConstClass(node_vtable)) []
        guard_nonnull(p0) []
        jump(p0)
        """
        preamble = """
        [p0]
        guard_class(p0, ConstClass(node_vtable)) []
        jump(p0)
        """
        expected = """
        [p0]
        jump(p0)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_guard_nonnull_class_1(self):
        ops = """
        [p0]
        guard_class(p0, ConstClass(node_vtable)) []
        guard_nonnull(p0) []
        guard_nonnull_class(p0, ConstClass(node_vtable)) []
        jump(p0)
        """
        preamble = """
        [p0]
        guard_class(p0, ConstClass(node_vtable)) []
        jump(p0)
        """
        expected = """
        [p0]
        jump(p0)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_guard_nonnull_class_2(self):
        ops = """
        [p0]
        guard_nonnull_class(p0, ConstClass(node_vtable)) []
        jump(p0)
        """
        preamble = """
        [p0]
        guard_nonnull_class(p0, ConstClass(node_vtable)) []
        jump(p0)
        """
        expected = """
        [p0]
        jump(p0)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_int_is_true_1(self):
        ops = """
        [i0]
        i1 = int_is_true(i0)
        guard_true(i1) []
        i2 = int_is_true(i0)
        guard_true(i2) []
        jump(i0)
        """
        preamble = """
        [i0]
        i1 = int_is_true(i0)
        guard_true(i1) []
        jump(i0)
        """
        expected = """
        [i0]
        jump(i0)
        """
        short = """
        [i0]
        i1 = int_is_true(i0)
        guard_value(i1, 1) []
        jump(i0)
        """
        self.optimize_loop(ops, expected, preamble, expected_short=short)

    def test_bound_int_is_true(self):
        ops = """
        [i0]
        i1 = int_add(i0, 1)
        i2 = int_gt(i1, 0)
        guard_true(i2) []
        i3 = int_is_true(i1)
        guard_true(i3) []
        jump(i1)
        """
        expected = """
        [i0]
        i1 = int_add(i0, 1)
        i2 = int_gt(i1, 0)
        guard_true(i2) []
        jump(i1)
        """
        self.optimize_loop(ops, expected, expected)

    def test_int_is_true_is_zero(self):
        py.test.skip("in-progress")
        ops = """
        [i0]
        i1 = int_add(i0, 1)
        i2 = int_is_true(i1)
        guard_true(i2) []
        i3 = int_is_zero(i1)
        guard_false(i3) []
        jump(i1)
        """
        expected = """
        [i0]
        i1 = int_add(i0, 1)
        i2 = int_is_true(i1)
        guard_true(i2) []
        jump(i1)
        """
        self.optimize_loop(ops, expected)

    def test_ooisnull_oononnull_2(self):
        ops = """
        [p0]
        guard_nonnull(p0) []
        guard_nonnull(p0) []
        jump(p0)
        """
        preamble = """
        [p0]
        guard_nonnull(p0) []
        jump(p0)
        """
        expected = """
        [p0]
        jump(p0)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_ooisnull_on_null_ptr_1(self):
        ops = """
        []
        p0 = escape()
        guard_isnull(p0) []
        guard_isnull(p0) []
        jump()
        """
        expected = """
        []
        p0 = escape()
        guard_isnull(p0) []
        jump()
        """
        self.optimize_loop(ops, expected)

    def test_ooisnull_oononnull_via_virtual(self):
        ops = """
        [p0]
        pv = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(pv, p0, descr=valuedescr)
        guard_nonnull(p0) []
        p1 = getfield_gc(pv, descr=valuedescr)
        guard_nonnull(p1) []
        jump(p0)
        """
        preamble = """
        [p0]
        guard_nonnull(p0) []
        jump(p0)
        """
        expected = """
        [p0]
        jump(p0)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_oois_1(self):
        ops = """
        [p0]
        guard_class(p0, ConstClass(node_vtable)) []
        i0 = ptr_ne(p0, NULL)
        guard_true(i0) []
        i1 = ptr_eq(p0, NULL)
        guard_false(i1) []
        i2 = ptr_ne(NULL, p0)
        guard_true(i0) []
        i3 = ptr_eq(NULL, p0)
        guard_false(i1) []
        jump(p0)
        """
        preamble = """
        [p0]
        guard_class(p0, ConstClass(node_vtable)) []
        jump(p0)
        """
        expected = """
        [p0]
        jump(p0)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_nonnull_1(self):
        ops = """
        [p0]
        setfield_gc(p0, 5, descr=valuedescr)     # forces p0 != NULL
        i0 = ptr_ne(p0, NULL)
        guard_true(i0) []
        i1 = ptr_eq(p0, NULL)
        guard_false(i1) []
        i2 = ptr_ne(NULL, p0)
        guard_true(i2) []
        i3 = ptr_eq(NULL, p0)
        guard_false(i3) []
        guard_nonnull(p0) []
        jump(p0)
        """
        preamble = """
        [p0]
        setfield_gc(p0, 5, descr=valuedescr)
        jump(p0)
        """
        expected = """
        [p0]
        jump(p0)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_nonnull_2(self):
        ops = """
        []
        p0 = new_array(5, descr=arraydescr)     # forces p0 != NULL
        i0 = ptr_ne(p0, NULL)
        guard_true(i0) []
        i1 = ptr_eq(p0, NULL)
        guard_false(i1) []
        i2 = ptr_ne(NULL, p0)
        guard_true(i2) []
        i3 = ptr_eq(NULL, p0)
        guard_false(i3) []
        guard_nonnull(p0) []
        escape(p0)
        jump()
        """
        expected = """
        []
        p0 = new_array(5, descr=arraydescr)
        escape(p0)
        jump()
        """
        self.optimize_loop(ops, expected)

    def test_const_guard_value(self):
        ops = """
        []
        i = int_add(5, 3)
        guard_value(i, 8) []
        jump()
        """
        expected = """
        []
        jump()
        """
        self.optimize_loop(ops, expected)

    def test_constptr_guard_value(self):
        ops = """
        []
        p1 = escape()
        guard_value(p1, ConstPtr(myptr)) []
        jump()
        """
        self.optimize_loop(ops, ops)

    def test_guard_value_to_guard_true(self):
        ops = """
        [i]
        i1 = int_lt(i, 3)
        guard_value(i1, 1) [i]
        jump(i)
        """
        preamble = """
        [i]
        i1 = int_lt(i, 3)
        guard_true(i1) [i]
        jump(i)
        """
        expected = """
        [i]
        jump(i)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_guard_value_to_guard_false(self):
        ops = """
        [i]
        i1 = int_is_true(i)
        guard_value(i1, 0) [i]
        jump(i)
        """
        preamble = """
        [i]
        i1 = int_is_true(i)
        guard_false(i1) [i]
        jump()
        """
        expected = """
        []
        jump()
        """
        self.optimize_loop(ops, expected, preamble)

    def test_guard_value_on_nonbool(self):
        ops = """
        [i]
        i1 = int_add(i, 3)
        guard_value(i1, 0) [i]
        jump(i)
        """
        preamble = """
        [i]
        i1 = int_add(i, 3)
        guard_value(i1, 0) [i]
        jump()
        """
        expected = """
        []
        jump()
        """
        self.optimize_loop(ops, expected, preamble)

    def test_int_is_true_of_bool(self):
        ops = """
        [i0, i1]
        i2 = int_gt(i0, i1)
        i3 = int_is_true(i2)
        i4 = int_is_true(i3)
        guard_value(i4, 0) [i0, i1]
        jump(i0, i1)
        """
        preamble = """
        [i0, i1]
        i2 = int_gt(i0, i1)
        guard_false(i2) [i0, i1]
        jump(i0, i1)
        """
        expected = """
        [i0, i1]
        jump(i0, i1)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_compare_with_itself(self):
        ops = """
        []
        i0 = escape()
        i1 = int_lt(i0, i0)
        guard_false(i1) []
        i2 = int_le(i0, i0)
        guard_true(i2) []
        i3 = int_eq(i0, i0)
        guard_true(i3) []
        i4 = int_ne(i0, i0)
        guard_false(i4) []
        i5 = int_gt(i0, i0)
        guard_false(i5) []
        i6 = int_ge(i0, i0)
        guard_true(i6) []
        jump()
        """
        expected = """
        []
        i0 = escape()
        jump()
        """
        self.optimize_loop(ops, expected)

    def test_compare_with_itself_uint(self):
        py.test.skip("implement me")
        ops = """
        []
        i0 = escape()
        i7 = uint_lt(i0, i0)
        guard_false(i7) []
        i8 = uint_le(i0, i0)
        guard_true(i8) []
        i9 = uint_gt(i0, i0)
        guard_false(i9) []
        i10 = uint_ge(i0, i0)
        guard_true(i10) []
        jump()
        """
        expected = """
        []
        i0 = escape()
        jump()
        """
        self.optimize_loop(ops, expected)

    def test_p123_simple(self):
        ops = """
        [i1, p2, p3]
        i3 = getfield_gc(p3, descr=valuedescr)
        escape(i3)
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i1, descr=valuedescr)
        jump(i1, p1, p2)
        """
        preamble = """
        [i1, p2, p3]
        i3 = getfield_gc(p3, descr=valuedescr)
        escape(i3)
        jump(i1, p2)
        """
        expected = """
        [i1, p2]
        i3 = getfield_gc(p2, descr=valuedescr)
        escape(i3)
        p3 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p3, i1, descr=valuedescr)
        jump(i1, p3)
        """
        # We cannot track virtuals that survive for more than two iterations.
        self.optimize_loop(ops, expected, preamble)

    def test_p123_nested(self):
        ops = """
        [i1, p2, p3]
        i3 = getfield_gc(p3, descr=valuedescr)
        escape(i3)
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i1, descr=valuedescr)
        p1sub = new_with_vtable(ConstClass(node_vtable2))
        setfield_gc(p1sub, i1, descr=valuedescr)
        setfield_gc(p1, p1sub, descr=nextdescr)
        jump(i1, p1, p2)
        """
        # The same as test_p123_simple, but with a virtual containing another
        # virtual.
        preamble = """
        [i1, p2, p3]
        i3 = getfield_gc(p3, descr=valuedescr)
        escape(i3)
        jump(i1, p2)
        """
        expected = """
        [i1, p2]
        i3 = getfield_gc(p2, descr=valuedescr)
        escape(i3)
        p4 = new_with_vtable(ConstClass(node_vtable))
        p1sub = new_with_vtable(ConstClass(node_vtable2))
        setfield_gc(p1sub, i1, descr=valuedescr)
        setfield_gc(p4, i1, descr=valuedescr)
        setfield_gc(p4, p1sub, descr=nextdescr)
        jump(i1, p4)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_p123_anti_nested(self):
        ops = """
        [i1, p2, p3]
        p3sub = getfield_gc(p3, descr=nextdescr)
        i3 = getfield_gc(p3sub, descr=valuedescr)
        escape(i3)
        p1 = new_with_vtable(ConstClass(node_vtable))
        p2sub = new_with_vtable(ConstClass(node_vtable2))
        setfield_gc(p2sub, i1, descr=valuedescr)
        setfield_gc(p2, p2sub, descr=nextdescr)
        jump(i1, p1, p2)
        """
        # The same as test_p123_simple, but in the end the "old" p2 contains
        # a "young" virtual p2sub.  Make sure it is all forced.
        preamble = """
        [i1, p2, p3]
        p3sub = getfield_gc(p3, descr=nextdescr)
        i3 = getfield_gc(p3sub, descr=valuedescr)
        escape(i3)
        p2sub = new_with_vtable(ConstClass(node_vtable2))
        setfield_gc(p2sub, i1, descr=valuedescr)
        setfield_gc(p2, p2sub, descr=nextdescr)
        jump(i1, p2, p2sub)
        """
        expected = """
        [i1, p2, p2sub]
        i3 = getfield_gc(p2sub, descr=valuedescr)
        escape(i3)
        p1 = new_with_vtable(ConstClass(node_vtable))
        p3sub = new_with_vtable(ConstClass(node_vtable2))
        setfield_gc(p3sub, i1, descr=valuedescr)
        setfield_gc(p1, p3sub, descr=nextdescr)
        jump(i1, p1, p3sub)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_dont_delay_setfields(self):
        ops = """
        [p1, p2]
        i1 = getfield_gc(p1, descr=nextdescr)
        i2 = int_sub(i1, 1)
        i2b = int_is_true(i2)
        guard_true(i2b) []
        setfield_gc(p2, i2, descr=nextdescr)
        p3 = new_with_vtable(ConstClass(node_vtable))
        jump(p2, p3)
        """
        preamble = """
        [p1, p2]
        i1 = getfield_gc(p1, descr=nextdescr)
        i2 = int_sub(i1, 1)
        i2b = int_is_true(i2)
        guard_true(i2b) []
        setfield_gc(p2, i2, descr=nextdescr)
        jump(p2, i2)
        """
        expected = """
        [p2, i1]
        i2 = int_sub(i1, 1)
        i2b = int_is_true(i2)
        guard_true(i2b) []
        p3 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p3, i2, descr=nextdescr)
        jump(p3, i2)
        """
        self.optimize_loop(ops, expected, preamble)

    # ----------

    def test_fold_guard_no_exception(self):
        ops = """
        [i]
        guard_no_exception() []
        i1 = int_add(i, 3)
        i2 = call(i1, descr=nonwritedescr)
        guard_no_exception() [i1, i2]
        i3 = call(i2, descr=nonwritedescr)
        jump(i1)       # the exception is considered lost when we loop back
        """
        preamble = """
        [i]
        guard_no_exception() []    # occurs at the start of bridges, so keep it
        i1 = int_add(i, 3)
        i2 = call(i1, descr=nonwritedescr)
        guard_no_exception() [i1, i2]
        i3 = call(i2, descr=nonwritedescr)
        jump(i1)
        """
        expected = """
        [i]
        guard_no_exception() []    # occurs at the start of bridges, so keep it
        i1 = int_add(i, 3)
        i2 = call(i1, descr=nonwritedescr)
        guard_no_exception() [i1, i2]
        i3 = call(i2, descr=nonwritedescr)
        jump(i1)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_bug_guard_no_exception(self):
        ops = """
        []
        i0 = call(123, descr=nonwritedescr)
        p0 = call(0, "xy", descr=s2u_descr)      # string -> unicode
        guard_no_exception() []
        escape(p0)
        jump()
        """
        expected = """
        []
        i0 = call(123, descr=nonwritedescr)
        escape(u"xy")
        jump()
        """
        self.optimize_loop(ops, expected)

    # ----------

    def test_call_loopinvariant(self):
        ops = """
        [i1]
        i2 = call_loopinvariant(1, i1, descr=nonwritedescr)
        guard_no_exception() []
        guard_value(i2, 1) []
        i3 = call_loopinvariant(1, i1, descr=nonwritedescr)
        guard_no_exception() []
        guard_value(i3, 1) []
        i4 = call_loopinvariant(1, i1, descr=nonwritedescr)
        guard_no_exception() []
        guard_value(i4, 1) []
        jump(i1)
        """
        preamble = """
        [i1]
        i2 = call(1, i1, descr=nonwritedescr)
        guard_no_exception() []
        guard_value(i2, 1) []
        jump(i1)
        """
        expected = """
        [i1]
        jump(i1)
        """
        self.optimize_loop(ops, expected, preamble)

    # ----------

    def test_virtual_1(self):
        ops = """
        [i, p0]
        i0 = getfield_gc(p0, descr=valuedescr)
        i1 = int_add(i0, i)
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i1, descr=valuedescr)
        jump(i, p1)
        """
        preamble = """
        [i, p0]
        i0 = getfield_gc(p0, descr=valuedescr)
        i1 = int_add(i0, i)
        jump(i, i1)
        """
        expected = """
        [i, i2]
        i1 = int_add(i2, i)
        jump(i, i1)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_virtual_float(self):
        ops = """
        [f, p0]
        f0 = getfield_gc(p0, descr=floatdescr)
        f1 = float_add(f0, f)
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, f1, descr=floatdescr)
        jump(f, p1)
        """
        preamble = """
        [f, p0]
        f2 = getfield_gc(p0, descr=floatdescr)
        f1 = float_add(f2, f)
        jump(f, f1)
        """
        expected = """
        [f, f2]
        f1 = float_add(f2, f)
        jump(f, f1)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_virtual_oois(self):
        ops = """
        [p0, p1, p2]
        guard_nonnull(p0) []
        i3 = ptr_ne(p0, NULL)
        guard_true(i3) []
        i4 = ptr_eq(p0, NULL)
        guard_false(i4) []
        i5 = ptr_ne(NULL, p0)
        guard_true(i5) []
        i6 = ptr_eq(NULL, p0)
        guard_false(i6) []
        i7 = ptr_ne(p0, p1)
        guard_true(i7) []
        i8 = ptr_eq(p0, p1)
        guard_false(i8) []
        i9 = ptr_ne(p0, p2)
        guard_true(i9) []
        i10 = ptr_eq(p0, p2)
        guard_false(i10) []
        i11 = ptr_ne(p2, p1)
        guard_true(i11) []
        i12 = ptr_eq(p2, p1)
        guard_false(i12) []
        jump(p0, p1, p2)
        """
        expected = """
        [p0, p1, p2]
        # all constant-folded :-)
        jump(p0, p1, p2)
        """
        #
        # to be complete, we also check the no-opt case where most comparisons
        # are not removed.  The exact set of comparisons removed depends on
        # the details of the algorithm...
        expected2 = """
        [p0, p1, p2]
        guard_nonnull(p0) []
        i7 = ptr_ne(p0, p1)
        guard_true(i7) []
        i9 = ptr_ne(p0, p2)
        guard_true(i9) []
        i11 = ptr_ne(p2, p1)
        guard_true(i11) []
        jump(p0, p1, p2)
        """
        self.optimize_loop(ops, expected, expected2)

    def test_virtual_default_field(self):
        ops = """
        [p0]
        i0 = getfield_gc(p0, descr=valuedescr)
        guard_value(i0, 0) []
        p1 = new_with_vtable(ConstClass(node_vtable))
        # the field 'value' has its default value of 0
        jump(p1)
        """
        preamble = """
        [p0]
        i0 = getfield_gc(p0, descr=valuedescr)
        guard_value(i0, 0) []
        jump()
        """
        expected = """
        []
        jump()
        """
        self.optimize_loop(ops, expected, preamble)

    def test_virtual_3(self):
        ops = """
        [i]
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i, descr=valuedescr)
        i0 = getfield_gc(p1, descr=valuedescr)
        i1 = int_add(i0, 1)
        jump(i1)
        """
        expected = """
        [i]
        i1 = int_add(i, 1)
        jump(i1)
        """
        self.optimize_loop(ops, expected)

    def test_virtual_4(self):
        ops = """
        [i0, p0]
        guard_class(p0, ConstClass(node_vtable)) []
        i1 = getfield_gc(p0, descr=valuedescr)
        i2 = int_sub(i1, 1)
        i3 = int_add(i0, i1)
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i2, descr=valuedescr)
        jump(i3, p1)
        """
        preamble = """
        [i0, p0]
        guard_class(p0, ConstClass(node_vtable)) []
        i1 = getfield_gc(p0, descr=valuedescr)
        i2 = int_sub(i1, 1)
        i3 = int_add(i0, i1)
        jump(i3, i2)
        """
        expected = """
        [i0, i1]
        i2 = int_sub(i1, 1)
        i3 = int_add(i0, i1)
        jump(i3, i2)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_virtual_5(self):
        ops = """
        [i0, p0]
        guard_class(p0, ConstClass(node_vtable)) []
        i1 = getfield_gc(p0, descr=valuedescr)
        i2 = int_sub(i1, 1)
        i3 = int_add(i0, i1)
        p2 = new_with_vtable(ConstClass(node_vtable2))
        setfield_gc(p2, i1, descr=valuedescr)
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i2, descr=valuedescr)
        setfield_gc(p1, p2, descr=nextdescr)
        jump(i3, p1)
        """
        preamble = """
        [i0, p0]
        guard_class(p0, ConstClass(node_vtable)) []
        i1 = getfield_gc(p0, descr=valuedescr)
        i2 = int_sub(i1, 1)
        i3 = int_add(i0, i1)
        i4 = same_as(i2) # This same_as should be killed by backend
        jump(i3, i1, i2)
        """
        expected = """
        [i0, i1, i1bis]
        i2 = int_sub(i1bis, 1)
        i3 = int_add(i0, i1bis)
        jump(i3, i1bis, i2)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_virtual_recursive(self):
        ops = """
        [p0]
        p41 = getfield_gc(p0, descr=nextdescr)
        i0 = getfield_gc(p41, descr=valuedescr)
        p1 = new_with_vtable(ConstClass(node_vtable2))
        p2 = new_with_vtable(ConstClass(node_vtable2))
        setfield_gc(p2, p1, descr=nextdescr)
        setfield_gc(p1, p2, descr=nextdescr)
        i1 = int_add(i0, 1)
        setfield_gc(p2, i1, descr=valuedescr)
        jump(p1)
        """
        preamble = """
        [p0]
        p41 = getfield_gc(p0, descr=nextdescr)
        i0 = getfield_gc(p41, descr=valuedescr)
        i3 = int_add(i0, 1)
        jump(i3)
        """
        expected = """
        [i0]
        i1 = int_add(i0, 1)
        jump(i1)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_virtual_recursive_forced(self):
        ops = """
        [p0]
        p41 = getfield_gc(p0, descr=nextdescr)
        i0 = getfield_gc(p41, descr=valuedescr)
        p1 = new_with_vtable(ConstClass(node_vtable2))
        p2 = new_with_vtable(ConstClass(node_vtable2))
        setfield_gc(p2, p1, descr=nextdescr)
        setfield_gc(p1, p2, descr=nextdescr)
        i1 = int_add(i0, 1)
        setfield_gc(p2, i1, descr=valuedescr)
        setfield_gc(p0, p1, descr=nextdescr)
        jump(p1)
        """
        preamble = """
        [p0]
        p41 = getfield_gc(p0, descr=nextdescr)
        i0 = getfield_gc(p41, descr=valuedescr)
        i1 = int_add(i0, 1)
        p1 = new_with_vtable(ConstClass(node_vtable2))
        p2 = new_with_vtable(ConstClass(node_vtable2))
        setfield_gc(p2, p1, descr=nextdescr)
        setfield_gc(p2, i1, descr=valuedescr)
        setfield_gc(p1, p2, descr=nextdescr)
        setfield_gc(p0, p1, descr=nextdescr)
        jump(p1)
        """
        loop = """
        [p0]
        p41 = getfield_gc(p0, descr=nextdescr)
        i0 = getfield_gc(p41, descr=valuedescr)
        i1 = int_add(i0, 1)
        p1 = new_with_vtable(ConstClass(node_vtable2))
        p2 = new_with_vtable(ConstClass(node_vtable2))
        setfield_gc(p2, p1, descr=nextdescr)
        setfield_gc(p2, i1, descr=valuedescr)
        setfield_gc(p1, p2, descr=nextdescr)
        setfield_gc(p0, p1, descr=nextdescr)
        jump(p1)
        """
        self.optimize_loop(ops, loop, preamble)

    def test_virtual_constant_isnull(self):
        ops = """
        [i0]
        p0 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p0, NULL, descr=nextdescr)
        p2 = getfield_gc(p0, descr=nextdescr)
        i1 = ptr_eq(p2, NULL)
        jump(i1)
        """
        preamble = """
        [i0]
        jump()
        """
        expected = """
        []
        jump()
        """
        self.optimize_loop(ops, expected, preamble)

    def test_virtual_constant_isnonnull(self):
        ops = """
        [i0]
        p0 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p0, ConstPtr(myptr), descr=nextdescr)
        p2 = getfield_gc(p0, descr=nextdescr)
        i1 = ptr_eq(p2, NULL)
        jump(i1)
        """
        preamble = """
        [i0]
        jump()
        """
        expected = """
        []
        jump()
        """
        self.optimize_loop(ops, expected)

    def test_virtual_field_forced_by_lazy_setfield(self):
        ops = """
        [i0, p1, p3]
        i28 = int_add(i0, 1)
        p30 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p30, i28, descr=nextdescr)
        setfield_gc(p3, p30, descr=valuedescr)
        p45 = getfield_gc(p3, descr=valuedescr)
        i29 = int_add(i28, 1)
        jump(i29, p45, p3)
        """
        preamble = """
        [i0, p1, p3]
        i28 = int_add(i0, 1)
        i29 = int_add(i0, 2)
        p30 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p30, i28, descr=nextdescr)
        setfield_gc(p3, p30, descr=valuedescr)
        p46 = same_as(p30) # This same_as should be killed by backend
        jump(i29, p30, p3)
        """
        expected = """
        [i0, p1, p3]
        i28 = int_add(i0, 1)
        i29 = int_add(i0, 2)
        p30 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p30, i28, descr=nextdescr)
        setfield_gc(p3, p30, descr=valuedescr)
        jump(i29, p30, p3)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_nonvirtual_1(self):
        ops = """
        [i]
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i, descr=valuedescr)
        i0 = getfield_gc(p1, descr=valuedescr)
        i1 = int_add(i0, 1)
        escape(p1)
        escape(p1)
        jump(i1)
        """
        expected = """
        [i]
        i1 = int_add(i, 1)
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i, descr=valuedescr)
        escape(p1)
        escape(p1)
        jump(i1)
        """
        self.optimize_loop(ops, expected)

    def test_nonvirtual_2(self):
        ops = """
        [i, p0]
        i0 = getfield_gc(p0, descr=valuedescr)
        escape(p0)
        i1 = int_add(i0, i)
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i1, descr=valuedescr)
        jump(i, p1)
        """
        preamble = """
        [i, p0]
        i0 = getfield_gc(p0, descr=valuedescr)
        escape(p0)
        i1 = int_add(i0, i)
        jump(i, i1)
        """
        expected = """
        [i, i1]
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i1, descr=valuedescr)
        escape(p1)
        i2 = int_add(i1, i)
        jump(i, i2)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_nonvirtual_later(self):
        ops = """
        [i]
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i, descr=valuedescr)
        i1 = getfield_gc(p1, descr=valuedescr)
        escape(p1)
        i2 = getfield_gc(p1, descr=valuedescr)
        i3 = int_add(i1, i2)
        jump(i3)
        """
        expected = """
        [i]
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i, descr=valuedescr)
        escape(p1)
        i2 = getfield_gc(p1, descr=valuedescr)
        i3 = int_add(i, i2)
        jump(i3)
        """
        self.optimize_loop(ops, expected)

    def test_nonvirtual_write_null_fields_on_force(self):
        ops = """
        [i]
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i, descr=valuedescr)
        i1 = getfield_gc(p1, descr=valuedescr)
        setfield_gc(p1, 0, descr=valuedescr)
        escape(p1)
        i2 = getfield_gc(p1, descr=valuedescr)
        jump(i2)
        """
        expected = """
        [i]
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, 0, descr=valuedescr)
        escape(p1)
        i2 = getfield_gc(p1, descr=valuedescr)
        jump(i2)
        """
        self.optimize_loop(ops, expected)

    def test_getfield_gc_pure_1(self):
        ops = """
        [i]
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i, descr=valuedescr)
        i1 = getfield_gc_pure(p1, descr=valuedescr)
        jump(i1)
        """
        expected = """
        [i]
        jump(i)
        """
        self.optimize_loop(ops, expected)

    def test_getfield_gc_pure_2(self):
        ops = """
        [i]
        i1 = getfield_gc_pure(ConstPtr(myptr), descr=valuedescr)
        jump(i1)
        """
        expected = """
        []
        jump()
        """
        self.node.value = 5
        self.optimize_loop(ops, expected)

    def test_getfield_gc_pure_3(self):
        ops = """
        []
        p1 = escape()
        p2 = getfield_gc_pure(p1, descr=nextdescr)
        escape(p2)
        p3 = getfield_gc_pure(p1, descr=nextdescr)
        escape(p3)
        jump()
        """
        expected = """
        []
        p1 = escape()
        p2 = getfield_gc_pure(p1, descr=nextdescr)
        escape(p2)
        escape(p2)
        jump()
        """
        self.optimize_loop(ops, expected)

    def test_getfield_gc_nonpure_2(self):
        ops = """
        [i]
        i1 = getfield_gc(ConstPtr(myptr), descr=valuedescr)
        call(i1, descr=nonwritedescr)
        jump(i)
        """
        preamble = """
        [i]
        i1 = getfield_gc(ConstPtr(myptr), descr=valuedescr)
        call(i1, descr=nonwritedescr)
        jump(i, i1)
        """
        expected = """
        [i, i1]
        call(i1, descr=nonwritedescr)
        jump(i, i1)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_varray_boxed1(self):
        ops = """
        [p0, p8]
        p11 = getfield_gc(p0, descr=otherdescr)
        guard_nonnull(p11) [p0, p8]
        guard_class(p11, ConstClass(node_vtable2)) [p0, p8]
        p14 = getfield_gc(p11, descr=otherdescr)
        guard_isnull(p14) [p0, p8]
        p18 = getfield_gc(ConstPtr(myptr), descr=otherdescr)
        guard_isnull(p18) [p0, p8]
        p31 = new(descr=ssize)
        setfield_gc(p31, 0, descr=adescr)
        p33 = new_array(0, descr=arraydescr)
        setfield_gc(p31, p33, descr=bdescr)
        p35 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p35, p31, descr=valuedescr)
        jump(p0, p35)
        """
        expected = """
        [p0]
        jump(p0)
        """
        self.optimize_loop(ops, expected)

    def test_varray_boxed_simplified(self):
        ops = """
        [p0, p8]
        p18 = getfield_gc(ConstPtr(myptr), descr=otherdescr)
        guard_isnull(p18) [p0, p8]
        p31 = new(descr=ssize)
        p35 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p35, p31, descr=valuedescr)
        jump(p0, p35)
        """
        expected = """
        [p0]
        jump(p0)
        """
        self.optimize_loop(ops, expected)

    def test_varray_boxed_noconst(self):
        ops = """
        [p0, p8, p18, p19]
        guard_isnull(p18) [p0, p8]
        p31 = new(descr=ssize)
        p35 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p35, p31, descr=valuedescr)
        jump(p0, p35, p19, p18)
        """
        expected = """
        [p0, p19]
        guard_isnull(p19) [p0]
        jump(p0, NULL)
        """
        self.optimize_loop(ops, expected)

    def test_varray_1(self):
        ops = """
        [i1]
        p1 = new_array(3, descr=arraydescr)
        i3 = arraylen_gc(p1, descr=arraydescr)
        guard_value(i3, 3) []
        setarrayitem_gc(p1, 1, i1, descr=arraydescr)
        setarrayitem_gc(p1, 0, 25, descr=arraydescr)
        i2 = getarrayitem_gc(p1, 1, descr=arraydescr)
        jump(i2)
        """
        expected = """
        [i1]
        jump(i1)
        """
        self.optimize_loop(ops, expected)

    def test_varray_clear_unroll_bug(self):
        ops = """
        [p0]
        i0 = getarrayitem_gc(p0, 0, descr=arraydescr)
        i1 = getarrayitem_gc(p0, 1, descr=arraydescr)
        i2 = getarrayitem_gc(p0, 2, descr=arraydescr)
        i3 = int_add(i0, i1)
        i4 = int_add(i3, i2)
        p1 = new_array_clear(3, descr=arraydescr)
        setarrayitem_gc(p1, 1, i4, descr=arraydescr)
        setarrayitem_gc(p1, 0, 25, descr=arraydescr)
        jump(p1)
        """
        expected = """
        [i1]
        i2 = int_add(25, i1)
        jump(i2)
        """
        self.optimize_loop(ops, expected)

    def test_varray_alloc_and_set(self):
        ops = """
        [i1]
        p1 = new_array(2, descr=arraydescr)
        setarrayitem_gc(p1, 0, 25, descr=arraydescr)
        i2 = getarrayitem_gc(p1, 0, descr=arraydescr)
        jump(i2)
        """
        preamble = """
        [i1]
        jump()
        """
        expected = """
        []
        jump()
        """
        self.optimize_loop(ops, expected, preamble)

    def test_varray_float(self):
        ops = """
        [f1]
        p1 = new_array(3, descr=floatarraydescr)
        i3 = arraylen_gc(p1, descr=floatarraydescr)
        guard_value(i3, 3) []
        setarrayitem_gc(p1, 1, f1, descr=floatarraydescr)
        setarrayitem_gc(p1, 0, 3.5, descr=floatarraydescr)
        f2 = getarrayitem_gc(p1, 1, descr=floatarraydescr)
        jump(f2)
        """
        expected = """
        [f1]
        jump(f1)
        """
        self.optimize_loop(ops, expected)

    def test_array_non_optimized(self):
        ops = """
        [i1, p0]
        setarrayitem_gc(p0, 0, i1, descr=arraydescr)
        guard_nonnull(p0) []
        p1 = new_array(i1, descr=arraydescr)
        jump(i1, p1)
        """
        expected = """
        [i1, p0]
        p1 = new_array(i1, descr=arraydescr)
        setarrayitem_gc(p0, 0, i1, descr=arraydescr)
        jump(i1, p1)
        """
        self.optimize_loop(ops, expected)

    def test_nonvirtual_array_write_null_fields_on_force(self):
        ops = """
        [i1]
        p1 = new_array(5, descr=arraydescr)
        setarrayitem_gc(p1, 0, i1, descr=arraydescr)
        setarrayitem_gc(p1, 1, 0, descr=arraydescr)
        escape(p1)
        jump(i1)
        """
        expected = """
        [i1]
        p1 = new_array(5, descr=arraydescr)
        setarrayitem_gc(p1, 0, i1, descr=arraydescr)
        setarrayitem_gc(p1, 1, 0, descr=arraydescr)
        escape(p1)
        jump(i1)
        """
        self.optimize_loop(ops, expected)

    def test_varray_2(self):
        ops = """
        [i0, p1]
        i1 = getarrayitem_gc(p1, 0, descr=arraydescr)
        i2 = getarrayitem_gc(p1, 1, descr=arraydescr)
        i3 = int_sub(i1, i2)
        guard_value(i3, 15) []
        p2 = new_array(2, descr=arraydescr)
        setarrayitem_gc(p2, 1, i0, descr=arraydescr)
        setarrayitem_gc(p2, 0, 20, descr=arraydescr)
        jump(i0, p2)
        """
        preamble = """
        [i0, p1]
        i1 = getarrayitem_gc(p1, 0, descr=arraydescr)
        i2 = getarrayitem_gc(p1, 1, descr=arraydescr)
        i3 = int_sub(i1, i2)
        guard_value(i3, 15) []
        jump(i0)
        """
        expected = """
        [i0]
        i3 = int_sub(20, i0)
        guard_value(i3, 15) []
        jump(5)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_p123_array(self):
        ops = """
        [i1, p2, p3]
        i3 = getarrayitem_gc(p3, 0, descr=arraydescr)
        escape(i3)
        p1 = new_array(1, descr=arraydescr)
        setarrayitem_gc(p1, 0, i1, descr=arraydescr)
        jump(i1, p1, p2)
        """
        preamble = """
        [i1, p2, p3]
        i3 = getarrayitem_gc(p3, 0, descr=arraydescr)
        escape(i3)
        jump(i1, p2)
        """
        expected = """
        [i1, p2]
        i3 = getarrayitem_gc(p2, 0, descr=arraydescr)
        escape(i3)
        p1 = new_array(1, descr=arraydescr)
        setarrayitem_gc(p1, 0, i1, descr=arraydescr)
        jump(i1, p1)
        """
        # We cannot track virtuals that survive for more than two iterations.
        self.optimize_loop(ops, expected, preamble)

    def test_varray_forced_1(self):
        ops = """
        []
        p2 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p2, 3, descr=valuedescr)
        i1 = getfield_gc(p2, descr=valuedescr)    # i1 = const 3
        p1 = new_array(i1, descr=arraydescr)
        escape(p1)
        i2 = arraylen_gc(p1)
        escape(i2)
        jump()
        """
        expected = """
        []
        p1 = new_array(3, descr=arraydescr)
        escape(p1)
        escape(3)
        jump()
        """
        self.optimize_loop(ops, expected)

    def test_vstruct_1(self):
        ops = """
        [i1, p2]
        i2 = getfield_gc(p2, descr=adescr)
        escape(i2)
        p3 = new(descr=ssize)
        setfield_gc(p3, i1, descr=adescr)
        jump(i1, p3)
        """
        preamble = """
        [i1, p2]
        i2 = getfield_gc(p2, descr=adescr)
        escape(i2)
        jump(i1)
        """
        expected = """
        [i1]
        escape(i1)
        jump(i1)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_p123_vstruct(self):
        ops = """
        [i1, p2, p3]
        i3 = getfield_gc(p3, descr=adescr)
        escape(i3)
        p1 = new(descr=ssize)
        setfield_gc(p1, i1, descr=adescr)
        jump(i1, p1, p2)
        """
        preamble = """
        [i1, p2, p3]
        i3 = getfield_gc(p3, descr=adescr)
        escape(i3)
        jump(i1, p2)
        """
        expected = """
        [i1, p2]
        i3 = getfield_gc(p2, descr=adescr)
        escape(i3)
        p1 = new(descr=ssize)
        setfield_gc(p1, i1, descr=adescr)
        jump(i1, p1)
        """
        # We cannot track virtuals that survive for more than two iterations.
        self.optimize_loop(ops, expected, preamble)

    def test_virtual_raw_malloc_basic(self):
        ops = """
        [i1]
        i2 = call('malloc', 10, descr=raw_malloc_descr)
        guard_no_exception() []
        setarrayitem_raw(i2, 0, i1, descr=rawarraydescr)
        i3 = getarrayitem_raw(i2, 0, descr=rawarraydescr)
        call('free', i2, descr=raw_free_descr)
        jump(i3)
        """
        expected = """
        [i1]
        jump(i1)
        """
        self.optimize_loop(ops, expected)

    def test_virtual_raw_malloc_const(self):
        ops = """
        [i1]
        i5 = int_mul(10, 1)
        i2 = call('malloc', i5, descr=raw_malloc_descr)
        guard_no_exception() []
        setarrayitem_raw(i2, 0, i1, descr=rawarraydescr)
        i3 = getarrayitem_raw(i2, 0, descr=rawarraydescr)
        call('free', i2, descr=raw_free_descr)
        jump(i3)
        """
        expected = """
        [i1]
        jump(i1)
        """
        self.optimize_loop(ops, expected)

    def test_virtual_raw_malloc_force(self):
        ops = """
        [i1]
        i2 = call('malloc', 20, descr=raw_malloc_descr)
        guard_no_exception() []
        setarrayitem_raw(i2, 0, i1, descr=rawarraydescr_char)
        setarrayitem_raw(i2, 2, 456, descr=rawarraydescr_char)
        setarrayitem_raw(i2, 1, 123, descr=rawarraydescr_char)
        setarrayitem_raw(i2, 1, 789, descr=rawarraydescr_float)
        label('foo') # we expect the buffer to be forced *after* the label
        escape(i2)
        call('free', i2, descr=raw_free_descr)
        jump(i1)
        """
        expected = """
        [i1]
        label('foo')
        i2 = call('malloc', 20, descr=raw_malloc_descr)
        #guard_no_exception() []  # XXX should appear
        raw_store(i2, 0, i1, descr=rawarraydescr_char)
        raw_store(i2, 1, 123, descr=rawarraydescr_char)
        raw_store(i2, 2, 456, descr=rawarraydescr_char)
        raw_store(i2, 8, 789, descr=rawarraydescr_float)
        escape(i2)
        call('free', i2, descr=raw_free_descr)
        jump(i1)
        """
        self.optimize_loop(ops, expected)

    def test_virtual_raw_malloc_invalid_write_force(self):
        ops = """
        [i1]
        i2 = call('malloc', 10, descr=raw_malloc_descr)
        guard_no_exception() []
        setarrayitem_raw(i2, 0, i1, descr=rawarraydescr)
        label('foo') # we expect the buffer to be forced *after* the label
        setarrayitem_raw(i2, 2, 456, descr=rawarraydescr_char) # overlap!
        call('free', i2, descr=raw_free_descr)
        jump(i1)
        """
        expected = """
        [i1]
        label('foo')
        i2 = call('malloc', 10, descr=raw_malloc_descr)
        #guard_no_exception() []  # XXX should appear
        raw_store(i2, 0, i1, descr=rawarraydescr)
        setarrayitem_raw(i2, 2, 456, descr=rawarraydescr_char)
        call('free', i2, descr=raw_free_descr)
        jump(i1)
        """
        self.optimize_loop(ops, expected)

    def test_virtual_raw_malloc_invalid_read_force(self):
        ops = """
        [i1]
        i2 = call('malloc', 10, descr=raw_malloc_descr)
        guard_no_exception() []
        setarrayitem_raw(i2, 0, i1, descr=rawarraydescr)
        label('foo') # we expect the buffer to be forced *after* the label
        i3 = getarrayitem_raw(i2, 0, descr=rawarraydescr_char)
        call('free', i2, descr=raw_free_descr)
        jump(i1)
        """
        expected = """
        [i1]
        label('foo')
        i2 = call('malloc', 10, descr=raw_malloc_descr)
        #guard_no_exception() []  # XXX should appear
        raw_store(i2, 0, i1, descr=rawarraydescr)
        i3 = getarrayitem_raw(i2, 0, descr=rawarraydescr_char)
        call('free', i2, descr=raw_free_descr)
        jump(i1)
        """
        self.optimize_loop(ops, expected)

    def test_virtual_raw_slice(self):
        ops = """
        [i0, i1]
        i2 = call('malloc', 10, descr=raw_malloc_descr)
        guard_no_exception() []
        setarrayitem_raw(i2, 0, 42, descr=rawarraydescr_char)
        i3 = int_add(i2, 1) # get a slice of the original buffer
        setarrayitem_raw(i3, 0, 4242, descr=rawarraydescr) # write to the slice
        i4 = getarrayitem_raw(i2, 0, descr=rawarraydescr_char)
        i5 = int_add(i2, 1)
        i6 = getarrayitem_raw(i5, 0, descr=rawarraydescr)
        call('free', i2, descr=raw_free_descr)
        jump(i0, i1)
        """
        expected = """
        [i0, i1]
        jump(i0, i1)
        """
        self.optimize_loop(ops, expected)

    def test_virtual_raw_slice_of_a_raw_slice(self):
        ops = """
        [i0, i1]
        i2 = call('malloc', 10, descr=raw_malloc_descr)
        guard_no_exception() []
        i3 = int_add(i2, 1) # get a slice of the original buffer
        i4 = int_add(i3, 1) # get a slice of a slice
        setarrayitem_raw(i4, 0, i1, descr=rawarraydescr_char) # write to the slice
        i5 = getarrayitem_raw(i2, 2, descr=rawarraydescr_char)
        call('free', i2, descr=raw_free_descr)
        jump(i0, i5)
        """
        expected = """
        [i0, i1]
        jump(i0, i1)
        """
        self.optimize_loop(ops, expected)

    def test_virtual_raw_slice_force(self):
        ops = """
        [i0, i1]
        i2 = call('malloc', 10, descr=raw_malloc_descr)
        guard_no_exception() []
        setarrayitem_raw(i2, 0, 42, descr=rawarraydescr_char)
        i3 = int_add(i2, 1) # get a slice of the original buffer
        setarrayitem_raw(i3, 4, 4242, descr=rawarraydescr_char) # write to the slice
        label('foo')
        escape(i3)
        jump(i0, i1)
        """
        expected = """
        [i0, i1]
        label('foo')
        # these ops are generated by VirtualRawBufferValue._really_force
        i2 = call('malloc', 10, descr=raw_malloc_descr)
        #guard_no_exception() []  # XXX should appear
        raw_store(i2, 0, 42, descr=rawarraydescr_char)
        raw_store(i2, 5, 4242, descr=rawarraydescr_char)
        # this is generated by VirtualRawSliceValue._really_force
        i4 = int_add(i2, 1)
        escape(i4)
        jump(i0, i1)
        """
        self.optimize_loop(ops, expected)

    def test_virtual_raw_malloc_virtualstate(self):
        ops = """
        [i0]
        i1 = getarrayitem_raw(i0, 0, descr=rawarraydescr)
        i2 = int_add(i1, 1)
        call('free', i0, descr=raw_free_descr)
        i3 = call('malloc', 10, descr=raw_malloc_descr)
        guard_no_exception() []
        setarrayitem_raw(i3, 0, i2, descr=rawarraydescr)
        label('foo')
        jump(i3)
        """
        expected = """
        [i0]
        i1 = getarrayitem_raw(i0, 0, descr=rawarraydescr)
        i2 = int_add(i1, 1)
        call('free', i0, descr=raw_free_descr)
        label('foo')
        i3 = call('malloc', 10, descr=raw_malloc_descr)
        #guard_no_exception() []  # XXX should appear
        raw_store(i3, 0, i2, descr=rawarraydescr)
        jump(i3)
        """
        self.optimize_loop(ops, expected)

    def test_virtual_raw_store_raw_load(self):
        ops = """
        [i1]
        i0 = call('malloc', 10, descr=raw_malloc_descr)
        guard_no_exception() []
        raw_store(i0, 0, i1, descr=rawarraydescr)
        i2 = raw_load(i0, 0, descr=rawarraydescr)
        i3 = int_add(i1, i2)
        call('free', i0, descr=raw_free_descr)
        jump(i3)
        """
        expected = """
        [i1]
        i2 = int_add(i1, i1)
        jump(i2)
        """
        self.optimize_loop(ops, expected)

    def test_virtual_raw_store_getarrayitem_raw(self):
        ops = """
        [f1]
        i0 = call('malloc', 16, descr=raw_malloc_descr)
        guard_no_exception() []
        raw_store(i0, 8, f1, descr=rawarraydescr_float)
        f2 = getarrayitem_raw(i0, 1, descr=rawarraydescr_float)
        f3 = float_add(f1, f2)
        call('free', i0, descr=raw_free_descr)
        jump(f3)
        """
        expected = """
        [f1]
        f2 = float_add(f1, f1)
        jump(f2)
        """
        self.optimize_loop(ops, expected)

    def test_virtual_setarrayitem_raw_raw_load(self):
        ops = """
        [f1]
        i0 = call('malloc', 16, descr=raw_malloc_descr)
        guard_no_exception() []
        setarrayitem_raw(i0, 1, f1, descr=rawarraydescr_float)
        f2 = raw_load(i0, 8, descr=rawarraydescr_float)
        f3 = float_add(f1, f2)
        call('free', i0, descr=raw_free_descr)
        jump(f3)
        """
        expected = """
        [f1]
        f2 = float_add(f1, f1)
        jump(f2)
        """
        self.optimize_loop(ops, expected)

    def test_virtual_raw_buffer_forced_but_slice_not_forced(self):
        ops = """
        [f1]
        i0 = call('malloc', 16, descr=raw_malloc_descr)
        guard_no_exception() []
        i1 = int_add(i0, 8)
        escape(i0)
        setarrayitem_raw(i1, 0, f1, descr=rawarraydescr_float)
        jump(f1)
        """
        expected = """
        [f1]
        i0 = call('malloc', 16, descr=raw_malloc_descr)
        #guard_no_exception() []  # XXX should appear
        escape(i0)
        i1 = int_add(i0, 8)
        setarrayitem_raw(i1, 0, f1, descr=rawarraydescr_float)
        jump(f1)
        """
        self.optimize_loop(ops, expected)

    def test_duplicate_getfield_1(self):
        ops = """
        [p1, p2]
        i1 = getfield_gc(p1, descr=valuedescr)
        i2 = getfield_gc(p2, descr=valuedescr)
        i3 = getfield_gc(p1, descr=valuedescr)
        i4 = getfield_gc(p2, descr=valuedescr)
        escape(i1)
        escape(i2)
        escape(i3)
        escape(i4)
        jump(p1, p2)
        """
        expected = """
        [p1, p2]
        i1 = getfield_gc(p1, descr=valuedescr)
        i2 = getfield_gc(p2, descr=valuedescr)
        escape(i1)
        escape(i2)
        escape(i1)
        escape(i2)
        jump(p1, p2)
        """
        self.optimize_loop(ops, expected)

    def test_duplicate_getfield_2(self):
        ops = """
        [p1, p2, i0]
        i1 = getfield_gc(p1, descr=valuedescr)
        i2 = getfield_gc(p2, descr=valuedescr)
        i3 = getfield_gc(p1, descr=valuedescr)
        i4 = getfield_gc(p2, descr=valuedescr)
        i5 = int_add(i3, i4)
        i6 = int_add(i0, i5)
        jump(p1, p2, i6)
        """
        expected = """
        [p1, p2, i0, i5]
        i6 = int_add(i0, i5)
        jump(p1, p2, i6, i5)
        """
        self.optimize_loop(ops, expected)

    def test_getfield_after_setfield(self):
        ops = """
        [p1, i1]
        setfield_gc(p1, i1, descr=valuedescr)
        i2 = getfield_gc(p1, descr=valuedescr)
        escape(i2)
        jump(p1, i1)
        """
        expected = """
        [p1, i1]
        setfield_gc(p1, i1, descr=valuedescr)
        escape(i1)
        jump(p1, i1)
        """
        self.optimize_loop(ops, expected)

    def test_setfield_of_different_type_does_not_clear(self):
        ops = """
        [p1, p2, i1]
        setfield_gc(p1, i1, descr=valuedescr)
        setfield_gc(p2, p1, descr=nextdescr)
        i2 = getfield_gc(p1, descr=valuedescr)
        escape(i2)
        jump(p1, p2, i1)
        """
        expected = """
        [p1, p2, i1]
        setfield_gc(p1, i1, descr=valuedescr)
        setfield_gc(p2, p1, descr=nextdescr)
        escape(i1)
        jump(p1, p2, i1)
        """
        self.optimize_loop(ops, expected)

    def test_setfield_of_same_type_clears(self):
        ops = """
        [p1, p2, i1, i2]
        setfield_gc(p1, i1, descr=valuedescr)
        setfield_gc(p2, i2, descr=valuedescr)
        i3 = getfield_gc(p1, descr=valuedescr)
        escape(i3)
        jump(p1, p2, i1, i3)
        """
        self.optimize_loop(ops, ops)

    def test_duplicate_getfield_mergepoint_has_no_side_effects(self):
        ops = """
        [p1]
        i1 = getfield_gc(p1, descr=valuedescr)
        debug_merge_point(15, 0)
        i2 = getfield_gc(p1, descr=valuedescr)
        escape(i1)
        escape(i2)
        jump(p1)
        """
        expected = """
        [p1]
        i1 = getfield_gc(p1, descr=valuedescr)
        debug_merge_point(15, 0)
        escape(i1)
        escape(i1)
        jump(p1)
        """
        self.optimize_loop(ops, expected)

    def test_duplicate_getfield_ovf_op_does_not_clear(self):
        ops = """
        [p1]
        i1 = getfield_gc(p1, descr=valuedescr)
        i2 = int_add_ovf(i1, 14)
        guard_no_overflow() []
        i3 = getfield_gc(p1, descr=valuedescr)
        escape(i2)
        escape(i3)
        jump(p1)
        """
        expected = """
        [p1]
        i1 = getfield_gc(p1, descr=valuedescr)
        i2 = int_add_ovf(i1, 14)
        guard_no_overflow() []
        escape(i2)
        escape(i1)
        jump(p1)
        """
        self.optimize_loop(ops, expected)

    def test_duplicate_getfield_setarrayitem_does_not_clear(self):
        ops = """
        [p1, p2]
        i1 = getfield_gc(p1, descr=valuedescr)
        setarrayitem_gc(p2, 0, p1, descr=arraydescr2)
        i3 = getfield_gc(p1, descr=valuedescr)
        escape(i1)
        escape(i3)
        jump(p1, p2)
        """
        expected = """
        [p1, p2]
        i1 = getfield_gc(p1, descr=valuedescr)
        setarrayitem_gc(p2, 0, p1, descr=arraydescr2)
        escape(i1)
        escape(i1)
        jump(p1, p2)
        """
        self.optimize_loop(ops, expected)

    def test_duplicate_getfield_constant(self):
        ops = """
        []
        i1 = getfield_gc(ConstPtr(myptr), descr=valuedescr)
        i2 = getfield_gc(ConstPtr(myptr), descr=valuedescr)
        escape(i1)
        escape(i2)
        jump()
        """
        expected = """
        []
        i1 = getfield_gc(ConstPtr(myptr), descr=valuedescr)
        escape(i1)
        escape(i1)
        jump()
        """
        self.optimize_loop(ops, expected)

    def test_duplicate_getfield_guard_value_const(self):
        ops = """
        [p1]
        guard_value(p1, ConstPtr(myptr)) []
        i1 = getfield_gc(p1, descr=valuedescr)
        i2 = getfield_gc(ConstPtr(myptr), descr=valuedescr)
        escape(i1)
        escape(i2)
        jump(p1)
        """
        expected = """
        []
        i1 = getfield_gc(ConstPtr(myptr), descr=valuedescr)
        escape(i1)
        escape(i1)
        jump()
        """
        self.optimize_loop(ops, expected)

    def test_duplicate_getfield_sideeffects_1(self):
        ops = """
        [p1]
        i1 = getfield_gc(p1, descr=valuedescr)
        escape()
        i2 = getfield_gc(p1, descr=valuedescr)
        escape(i1)
        escape(i2)
        jump(p1)
        """
        self.optimize_loop(ops, ops)

    def test_duplicate_getfield_sideeffects_2(self):
        ops = """
        [p1, i1]
        setfield_gc(p1, i1, descr=valuedescr)
        escape()
        i2 = getfield_gc(p1, descr=valuedescr)
        escape(i2)
        jump(p1, i1)
        """
        self.optimize_loop(ops, ops)

    def test_duplicate_setfield_0(self):
        ops = """
        [p1, i1, i2]
        setfield_gc(p1, i1, descr=valuedescr)
        setfield_gc(p1, i2, descr=valuedescr)
        jump(p1, i1, i2)
        """
        expected = """
        [p1, i1, i2]
        setfield_gc(p1, i2, descr=valuedescr)
        jump(p1, i1, i2)
        """
        # in this case, all setfields are removed, because we can prove
        # that in the loop it will always have the same value
        self.optimize_loop(ops, expected)

    def test_duplicate_setfield_1(self):
        ops = """
        [p1]
        i1 = escape()
        i2 = escape()
        setfield_gc(p1, i1, descr=valuedescr)
        setfield_gc(p1, i2, descr=valuedescr)
        jump(p1)
        """
        expected = """
        [p1]
        i1 = escape()
        i2 = escape()
        setfield_gc(p1, i2, descr=valuedescr)
        jump(p1)
        """
        self.optimize_loop(ops, expected)

    def test_duplicate_setfield_2(self):
        ops = """
        [p1, i1, i3]
        setfield_gc(p1, i1, descr=valuedescr)
        i2 = getfield_gc(p1, descr=valuedescr)
        setfield_gc(p1, i3, descr=valuedescr)
        escape(i2)
        jump(p1, i1, i3)
        """
        expected = """
        [p1, i1, i3]
        setfield_gc(p1, i3, descr=valuedescr)
        escape(i1)
        jump(p1, i1, i3)
        """
        self.optimize_loop(ops, expected)

    def test_duplicate_setfield_3(self):
        ops = """
        [p1, p2, i1, i3]
        setfield_gc(p1, i1, descr=valuedescr)
        i2 = getfield_gc(p2, descr=valuedescr)
        setfield_gc(p1, i3, descr=valuedescr)
        escape(i2)
        jump(p1, p2, i1, i3)
        """
        # potential aliasing of p1 and p2 means that we cannot kill the
        # the setfield_gc
        self.optimize_loop(ops, ops)

    def test_duplicate_setfield_4(self):
        ops = """
        [p1, i1, i2, p3]
        setfield_gc(p1, i1, descr=valuedescr)
        #
        # some operations on which the above setfield_gc cannot have effect
        i3 = getarrayitem_gc_pure(p3, 1, descr=arraydescr)
        i4 = getarrayitem_gc(p3, i3, descr=arraydescr)
        i5 = int_add(i3, i4)
        setarrayitem_gc(p3, 0, i5, descr=arraydescr)
        setfield_gc(p1, i4, descr=nextdescr)
        #
        setfield_gc(p1, i2, descr=valuedescr)
        escape()
        jump(p1, i1, i2, p3)
        """
        preamble = """
        [p1, i1, i2, p3]
        #
        i3 = getarrayitem_gc_pure(p3, 1, descr=arraydescr)
        i4 = getarrayitem_gc(p3, i3, descr=arraydescr)
        i5 = int_add(i3, i4)
        #
        setfield_gc(p1, i2, descr=valuedescr)
        setarrayitem_gc(p3, 0, i5, descr=arraydescr)
        setfield_gc(p1, i4, descr=nextdescr)
        escape()
        jump(p1, i1, i2, p3, i3)
        """
        expected = """
        [p1, i1, i2, p3, i3]
        #
        i4 = getarrayitem_gc(p3, i3, descr=arraydescr)
        i5 = int_add(i3, i4)
        #
        setfield_gc(p1, i2, descr=valuedescr)
        setarrayitem_gc(p3, 0, i5, descr=arraydescr)
        setfield_gc(p1, i4, descr=nextdescr)
        escape()
        jump(p1, i1, i2, p3, i3)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_duplicate_setfield_5(self):
        ops = """
        [p0, i1]
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i1, descr=valuedescr)
        setfield_gc(p0, p1, descr=nextdescr)
        setfield_raw(i1, i1, descr=valuedescr)    # random op with side-effects
        p2 = getfield_gc(p0, descr=nextdescr)
        i2 = getfield_gc(p2, descr=valuedescr)
        setfield_gc(p0, NULL, descr=nextdescr)
        escape(i2)
        jump(p0, i1)
        """
        expected = """
        [p0, i1]
        setfield_raw(i1, i1, descr=valuedescr)
        setfield_gc(p0, NULL, descr=nextdescr)
        escape(i1)
        jump(p0, i1)
        """
        self.optimize_loop(ops, expected)

    def test_duplicate_setfield_sideeffects_1(self):
        ops = """
        [p1, i1, i2]
        setfield_gc(p1, i1, descr=valuedescr)
        escape()
        setfield_gc(p1, i2, descr=valuedescr)
        jump(p1, i1, i2)
        """
        self.optimize_loop(ops, ops)

    def test_duplicate_setfield_residual_guard_1(self):
        ops = """
        [p1, i1, i2, i3]
        setfield_gc(p1, i1, descr=valuedescr)
        guard_true(i3) []
        i4 = int_neg(i2)
        setfield_gc(p1, i2, descr=valuedescr)
        jump(p1, i1, i2, i4)
        """
        preamble = """
        [p1, i1, i2, i3]
        setfield_gc(p1, i1, descr=valuedescr)
        guard_true(i3) []
        i4 = int_neg(i2)
        setfield_gc(p1, i2, descr=valuedescr)
        i7 = same_as(i2) # This same_as should be killed by backend
        i6 = same_as(i4)
        jump(p1, i1, i2, i4, i6)
        """
        expected = """
        [p1, i1, i2, i4, i5]
        setfield_gc(p1, i1, descr=valuedescr)
        guard_true(i4) []
        setfield_gc(p1, i2, descr=valuedescr)
        jump(p1, i1, i2, i5, i5)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_duplicate_setfield_residual_guard_2(self):
        # the difference with the previous test is that the field value is
        # a virtual, which we try hard to keep virtual
        ops = """
        [p1, i2, i3]
        p2 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, p2, descr=nextdescr)
        guard_true(i3) []
        i4 = int_neg(i2)
        setfield_gc(p1, NULL, descr=nextdescr)
        escape()
        jump(p1, i2, i4)
        """
        preamble = """
        [p1, i2, i3]
        guard_true(i3) [p1]
        i4 = int_neg(i2)
        setfield_gc(p1, NULL, descr=nextdescr)
        escape()
        i5 = same_as(i4)
        jump(p1, i2, i4, i5)
        """
        expected = """
        [p1, i2, i4, i5]
        guard_true(i4) [p1]
        setfield_gc(p1, NULL, descr=nextdescr)
        escape()
        jump(p1, i2, i5, i5)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_duplicate_setfield_residual_guard_3(self):
        ops = """
        [p1, i2, i3]
        p2 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p2, i2, descr=valuedescr)
        setfield_gc(p1, p2, descr=nextdescr)
        guard_true(i3) []
        i4 = int_neg(i2)
        setfield_gc(p1, NULL, descr=nextdescr)
        escape()
        jump(p1, i2, i4)
        """
        preamble = """
        [p1, i2, i3]
        guard_true(i3) [i2, p1]
        i4 = int_neg(i2)
        setfield_gc(p1, NULL, descr=nextdescr)
        escape()
        i5 = same_as(i4)
        jump(p1, i2, i4, i5)
        """
        expected = """
        [p1, i2, i4, i5]
        guard_true(i4) [i2, p1]
        setfield_gc(p1, NULL, descr=nextdescr)
        escape()
        jump(p1, i2, i5, i5)
        """
        self.optimize_loop(ops, expected)

    def test_duplicate_setfield_residual_guard_4(self):
        # test that the setfield_gc does not end up between int_eq and
        # the following guard_true
        ops = """
        [p1, i1, i2, i3]
        setfield_gc(p1, i1, descr=valuedescr)
        i5 = int_eq(i3, 5)
        guard_true(i5) []
        i4 = int_neg(i2)
        setfield_gc(p1, i2, descr=valuedescr)
        jump(p1, i1, i2, i4)
        """
        preamble = """
        [p1, i1, i2, i3]
        setfield_gc(p1, i1, descr=valuedescr)
        i5 = int_eq(i3, 5)
        guard_true(i5) []
        i4 = int_neg(i2)
        setfield_gc(p1, i2, descr=valuedescr)
        i8 = same_as(i2) # This same_as should be killed by backend
        i7 = same_as(i4)
        jump(p1, i1, i2, i4, i7)
        """
        expected = """
        [p1, i1, i2, i4, i7]
        setfield_gc(p1, i1, descr=valuedescr)
        i5 = int_eq(i4, 5)
        guard_true(i5) []
        setfield_gc(p1, i2, descr=valuedescr)
        jump(p1, i1, i2, i7, i7)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_duplicate_setfield_aliasing(self):
        # a case where aliasing issues (and not enough cleverness) mean
        # that we fail to remove any setfield_gc
        ops = """
        [p1, p2, i1, i2, i3]
        setfield_gc(p1, i1, descr=valuedescr)
        setfield_gc(p2, i2, descr=valuedescr)
        setfield_gc(p1, i3, descr=valuedescr)
        jump(p1, p2, i1, i2, i3)
        """
        self.optimize_loop(ops, ops)

    def test_duplicate_setfield_guard_value_const(self):
        ops = """
        [p1, i1, i2]
        guard_value(p1, ConstPtr(myptr)) []
        setfield_gc(p1, i1, descr=valuedescr)
        setfield_gc(ConstPtr(myptr), i2, descr=valuedescr)
        escape()
        jump(p1, i1, i2)
        """
        expected = """
        [i1, i2]
        setfield_gc(ConstPtr(myptr), i2, descr=valuedescr)
        escape()
        jump(i1, i2)
        """
        self.optimize_loop(ops, expected)

    def test_dont_force_setfield_around_copystrcontent(self):
        ops = """
        [p0, i0, p1, i1, i2]
        setfield_gc(p0, i1, descr=valuedescr)
        copystrcontent(p0, p1, i0, i1, i2)
        escape()
        jump(p0, i0, p1, i1, i2)
        """
        expected = """
        [p0, i0, p1, i1, i2]
        copystrcontent(p0, p1, i0, i1, i2)
        setfield_gc(p0, i1, descr=valuedescr)
        escape()
        jump(p0, i0, p1, i1, i2)
        """
        self.optimize_loop(ops, expected)

    def test_duplicate_getarrayitem_1(self):
        ops = """
        [p1]
        p2 = getarrayitem_gc(p1, 0, descr=arraydescr2)
        p3 = getarrayitem_gc(p1, 1, descr=arraydescr2)
        p4 = getarrayitem_gc(p1, 0, descr=arraydescr2)
        p5 = getarrayitem_gc(p1, 1, descr=arraydescr2)
        escape(p2)
        escape(p3)
        escape(p4)
        escape(p5)
        jump(p1)
        """
        expected = """
        [p1]
        p2 = getarrayitem_gc(p1, 0, descr=arraydescr2)
        p3 = getarrayitem_gc(p1, 1, descr=arraydescr2)
        escape(p2)
        escape(p3)
        escape(p2)
        escape(p3)
        jump(p1)
        """
        self.optimize_loop(ops, expected)

    def test_duplicate_getarrayitem_2(self):
        ops = """
        [p1, i0]
        i2 = getarrayitem_gc(p1, 0, descr=arraydescr2)
        i3 = getarrayitem_gc(p1, 1, descr=arraydescr2)
        i4 = getarrayitem_gc(p1, 0, descr=arraydescr2)
        i5 = getarrayitem_gc(p1, 1, descr=arraydescr2)
        i6 = int_add(i3, i4)
        i7 = int_add(i0, i6)
        jump(p1, i7)
        """
        expected = """
        [p1, i0, i6]
        i7 = int_add(i0, i6)
        jump(p1, i7, i6)
        """
        self.optimize_loop(ops, expected)

    def test_duplicate_getarrayitem_after_setarrayitem_1(self):
        ops = """
        [p1, p2]
        setarrayitem_gc(p1, 0, p2, descr=arraydescr2)
        p3 = getarrayitem_gc(p1, 0, descr=arraydescr2)
        escape(p3)
        jump(p1, p3)
        """
        expected = """
        [p1, p2]
        setarrayitem_gc(p1, 0, p2, descr=arraydescr2)
        escape(p2)
        jump(p1, p2)
        """
        self.optimize_loop(ops, expected)

    def test_duplicate_getarrayitem_after_setarrayitem_2(self):
        py.test.skip("setarrayitem with variable index")
        ops = """
        [p1, p2, p3, i1]
        setarrayitem_gc(p1, 0, p2, descr=arraydescr2)
        setarrayitem_gc(p1, i1, p3, descr=arraydescr2)
        p4 = getarrayitem_gc(p1, 0, descr=arraydescr2)
        p5 = getarrayitem_gc(p1, i1, descr=arraydescr2)
        escape(p4)
        escape(p5)
        jump(p1, p2, p3, i1)
        """
        expected = """
        [p1, p2, p3, i1]
        setarrayitem_gc(p1, 0, p2, descr=arraydescr2)
        setarrayitem_gc(p1, i1, p3, descr=arraydescr2)
        p4 = getarrayitem_gc(p1, 0, descr=arraydescr2)
        escape(p4)
        escape(p3)
        jump(p1, p2, p3, i1)
        """
        self.optimize_loop(ops, expected)

    def test_duplicate_getarrayitem_after_setarrayitem_3(self):
        ops = """
        [p1, p2, p3, p4, i1]
        setarrayitem_gc(p1, i1, p2, descr=arraydescr2)
        setarrayitem_gc(p1, 0, p3, descr=arraydescr2)
        setarrayitem_gc(p1, 1, p4, descr=arraydescr2)
        p5 = getarrayitem_gc(p1, i1, descr=arraydescr2)
        p6 = getarrayitem_gc(p1, 0, descr=arraydescr2)
        p7 = getarrayitem_gc(p1, 1, descr=arraydescr2)
        escape(p5)
        escape(p6)
        escape(p7)
        jump(p1, p2, p3, p4, i1)
        """
        expected = """
        [p1, p2, p3, p4, i1]
        setarrayitem_gc(p1, i1, p2, descr=arraydescr2)
        setarrayitem_gc(p1, 0, p3, descr=arraydescr2)
        setarrayitem_gc(p1, 1, p4, descr=arraydescr2)
        p5 = getarrayitem_gc(p1, i1, descr=arraydescr2)
        escape(p5)
        escape(p3)
        escape(p4)
        jump(p1, p2, p3, p4, i1)
        """
        self.optimize_loop(ops, expected)

    def test_getarrayitem_pure_does_not_invalidate(self):
        ops = """
        [p1, p2]
        p3 = getarrayitem_gc(p1, 0, descr=arraydescr2)
        i4 = getfield_gc_pure(ConstPtr(myptr), descr=valuedescr)
        p5 = getarrayitem_gc(p1, 0, descr=arraydescr2)
        escape(p3)
        escape(i4)
        escape(p5)
        jump(p1, p2)
        """
        expected = """
        [p1, p2]
        p3 = getarrayitem_gc(p1, 0, descr=arraydescr2)
        escape(p3)
        escape(5)
        escape(p3)
        jump(p1, p2)
        """
        self.optimize_loop(ops, expected)

    def test_duplicate_getarrayitem_after_setarrayitem_two_arrays(self):
        ops = """
        [p1, p2, p3, p4, i1]
        setarrayitem_gc(p1, 0, p3, descr=arraydescr2)
        setarrayitem_gc(p2, 1, p4, descr=arraydescr2)
        p5 = getarrayitem_gc(p1, 0, descr=arraydescr2)
        p6 = getarrayitem_gc(p2, 1, descr=arraydescr2)
        escape(p5)
        escape(p6)
        jump(p1, p2, p3, p4, i1)
        """
        expected = """
        [p1, p2, p3, p4, i1]
        setarrayitem_gc(p1, 0, p3, descr=arraydescr2)
        setarrayitem_gc(p2, 1, p4, descr=arraydescr2)
        escape(p3)
        escape(p4)
        jump(p1, p2, p3, p4, i1)
        """
        self.optimize_loop(ops, expected)

    def test_duplicate_setfield_virtual(self):
        ops = """
        [p1, i2, i3, p4]
        p2 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p2, p4, descr=nextdescr)
        setfield_gc(p1, p2, descr=nextdescr)
        guard_true(i3) []
        i4 = int_neg(i2)
        jump(p1, i2, i4, p4)
        """
        preamble = """
        [p1, i2, i3, p4]
        guard_true(i3) [p1, p4]
        i4 = int_neg(i2)
        p2 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p2, p4, descr=nextdescr)
        setfield_gc(p1, p2, descr=nextdescr)
        i101 = same_as(i4)
        jump(p1, i2, i4, p4, i101)
        """
        expected = """
        [p1, i2, i4, p4, i5]
        guard_true(i4) [p1, p4]
        p2 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p2, p4, descr=nextdescr)
        setfield_gc(p1, p2, descr=nextdescr)
        jump(p1, i2, i5, p4, i5)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_bug_1(self):
        ops = """
        [i0, p1]
        p4 = getfield_gc(p1, descr=nextdescr)
        guard_nonnull(p4) []
        escape(p4)
        #
        p2 = new_with_vtable(ConstClass(node_vtable))
        p3 = escape()
        setfield_gc(p2, p3, descr=nextdescr)
        jump(i0, p2)
        """
        expected = """
        [i0, p4]
        guard_nonnull(p4) []
        escape(p4)
        #
        p3 = escape()
        jump(i0, p3)
        """
        self.optimize_loop(ops, expected)

    def test_bug_2(self):
        ops = """
        [i0, p1]
        p4 = getarrayitem_gc(p1, 0, descr=arraydescr2)
        guard_nonnull(p4) []
        escape(p4)
        #
        p2 = new_array(1, descr=arraydescr2)
        p3 = escape()
        setarrayitem_gc(p2, 0, p3, descr=arraydescr2)
        jump(i0, p2)
        """
        expected = """
        [i0, p4]
        guard_nonnull(p4) []
        escape(p4)
        #
        p3 = escape()
        jump(i0, p3)
        """
        self.optimize_loop(ops, expected)

    def test_bug_3(self):
        ops = """
        [p1]
        guard_nonnull(p1) []
        guard_class(p1, ConstClass(node_vtable2)) []
        p2 = getfield_gc(p1, descr=nextdescr)
        guard_nonnull(12) []
        guard_class(p2, ConstClass(node_vtable)) []
        p3 = getfield_gc(p1, descr=otherdescr)
        guard_nonnull(12) []
        guard_class(p3, ConstClass(node_vtable)) []
        setfield_gc(p3, p2, descr=otherdescr)
        p1a = new_with_vtable(ConstClass(node_vtable2))
        p2a = new_with_vtable(ConstClass(node_vtable))
        p3a = new_with_vtable(ConstClass(node_vtable))
        escape(p3a)
        setfield_gc(p1a, p2a, descr=nextdescr)
        setfield_gc(p1a, p3a, descr=otherdescr)
        jump(p1a)
        """
        preamble = """
        [p1]
        guard_nonnull_class(p1, ConstClass(node_vtable2)) []
        p2 = getfield_gc(p1, descr=nextdescr)
        guard_class(p2, ConstClass(node_vtable)) []
        p3 = getfield_gc(p1, descr=otherdescr)
        guard_class(p3, ConstClass(node_vtable)) []
        p3a = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p3, p2, descr=otherdescr)
        escape(p3a)
        jump(p3a)
        """
        expected = """
        [p3a]
        # p1=p1a(next=p2a, other=p3a), p2()
        # p2 = getfield_gc(p1, descr=nextdescr) # p2a
        # p3 = getfield_gc(p1, descr=otherdescr)# p3a
        # setfield_gc(p3, p2, descr=otherdescr) # p3a.other = p2a
        # p1a = new_with_vtable(ConstClass(node_vtable2))
        # p2a = new_with_vtable(ConstClass(node_vtable))
        p3anew = new_with_vtable(ConstClass(node_vtable))
        p2 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p3a, p2, descr=otherdescr) # p3a.other = p2a
        escape(p3anew)
        jump(p3anew)
        """
        #self.optimize_loop(ops, expected) # XXX Virtual(node_vtable2, nextdescr=Not, otherdescr=Not)
        self.optimize_loop(ops, expected, preamble)

    def test_bug_3bis(self):
        ops = """
        [p1]
        guard_nonnull(p1) []
        guard_class(p1, ConstClass(node_vtable2)) []
        p2 = getfield_gc(p1, descr=nextdescr)
        guard_nonnull(12) []
        guard_class(p2, ConstClass(node_vtable)) []
        p3 = getfield_gc(p1, descr=otherdescr)
        guard_nonnull(12) []
        guard_class(p3, ConstClass(node_vtable)) []
        p1a = new_with_vtable(ConstClass(node_vtable2))
        p2a = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p3, p2a, descr=otherdescr)
        p3a = new_with_vtable(ConstClass(node_vtable))
        escape(p3a)
        setfield_gc(p1a, p2a, descr=nextdescr)
        setfield_gc(p1a, p3a, descr=otherdescr)
        jump(p1a)
        """
        preamble = """
        [p1]
        guard_nonnull_class(p1, ConstClass(node_vtable2)) []
        p2 = getfield_gc(p1, descr=nextdescr)
        guard_class(p2, ConstClass(node_vtable)) []
        p3 = getfield_gc(p1, descr=otherdescr)
        guard_class(p3, ConstClass(node_vtable)) []
        # p1a = new_with_vtable(ConstClass(node_vtable2))
        p3a = new_with_vtable(ConstClass(node_vtable))
        p2a = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p3, p2a, descr=otherdescr)
        escape(p3a)
        # setfield_gc(p1a, p2a, descr=nextdescr)
        # setfield_gc(p1a, p3a, descr=otherdescr)
        jump(p2a, p3a)
        """
        expected = """
        [p2, p3]
        p3a = new_with_vtable(ConstClass(node_vtable))
        p2a = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p3, p2a, descr=otherdescr)
        escape(p3a)
        jump(p2a, p3a)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_bug_4(self):
        ops = """
        [p9]
        p30 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(ConstPtr(myptr), p9, descr=nextdescr)
        jump(p30)
        """
        preamble = """
        [p9]
        setfield_gc(ConstPtr(myptr), p9, descr=nextdescr)
        jump()
        """
        expected = """
        []
        p30 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(ConstPtr(myptr), p30, descr=nextdescr)
        jump()
        """
        self.optimize_loop(ops, expected, preamble)

    def test_bug_5(self):
        ops = """
        [p0]
        i0 = escape()
        i2 = getfield_gc(p0, descr=valuedescr)
        i4 = int_add(i2, 1)
        setfield_gc(p0, i4, descr=valuedescr)
        guard_true(i0) []
        i6 = getfield_gc(p0, descr=valuedescr)
        i8 = int_sub(i6, 1)
        setfield_gc(p0, i8, descr=valuedescr)
        escape()
        jump(p0)
        """
        expected = """
        [p0]
        i0 = escape()
        i2 = getfield_gc(p0, descr=valuedescr)
        i4 = int_add(i2, 1)
        setfield_gc(p0, i4, descr=valuedescr)
        guard_true(i0) []
        setfield_gc(p0, i2, descr=valuedescr)
        escape()
        jump(p0)
        """
        self.optimize_loop(ops, expected)

    def test_invalid_loop_1(self):
        ops = """
        [p1]
        guard_isnull(p1) []
        #
        p2 = new_with_vtable(ConstClass(node_vtable))
        jump(p2)
        """
        self.raises(InvalidLoop, self.optimize_loop, ops, "crash!")

    def test_invalid_loop_2(self):
        ops = """
        [p1]
        guard_class(p1, ConstClass(node_vtable2)) []
        #
        p2 = new_with_vtable(ConstClass(node_vtable))
        escape(p2)      # prevent it from staying Virtual
        jump(p2)
        """
        self.raises(InvalidLoop, self.optimize_loop, ops, "crash!")

    def test_invalid_loop_3(self):
        ops = """
        [p1]
        p2 = getfield_gc(p1, descr=nextdescr)
        guard_isnull(p2) []
        #
        p3 = new_with_vtable(ConstClass(node_vtable))
        p4 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p3, p4, descr=nextdescr)
        jump(p3)
        """
        self.raises(InvalidLoop, self.optimize_loop, ops, "crash!")

    def test_invalid_loop_guard_value_of_virtual(self):
        ops = """
        [p1]
        p2 = new_with_vtable(ConstClass(node_vtable))
        guard_value(p2, ConstPtr(myptr)) []
        jump(p2)
        """
        exc = self.raises(InvalidLoop, self.optimize_loop, ops, "crash!")
        if exc:
            assert "node" in exc.msg

    def test_merge_guard_class_guard_value(self):
        ops = """
        [p1, i0, i1, i2, p2]
        guard_class(p1, ConstClass(node_vtable)) [i0]
        i3 = int_add(i1, i2)
        guard_value(p1, ConstPtr(myptr)) [i1]
        jump(p2, i0, i1, i3, p2)
        """
        preamble = """
        [p1, i0, i1, i2, p2]
        guard_value(p1, ConstPtr(myptr)) [i0]
        i3 = int_add(i1, i2)
        jump(p2, i0, i1, i3)
        """
        expected = """
        [p2, i0, i1, i2]
        guard_value(p2, ConstPtr(myptr)) [i0]
        i3 = int_add(i1, i2)
        jump(ConstPtr(myptr), i0, i1, i3)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_merge_guard_nonnull_guard_class(self):
        ops = """
        [p1, i0, i1, i2, p2]
        guard_nonnull(p1) [i0]
        i3 = int_add(i1, i2)
        guard_class(p1, ConstClass(node_vtable)) [i1]
        jump(p2, i0, i1, i3, p2)
        """
        preamble = """
        [p1, i0, i1, i2, p2]
        guard_nonnull_class(p1, ConstClass(node_vtable)) [i0]
        i3 = int_add(i1, i2)
        jump(p2, i0, i1, i3)
        """
        expected = """
        [p2, i0, i1, i2]
        guard_nonnull_class(p2, ConstClass(node_vtable)) [i0]
        i3 = int_add(i1, i2)
        jump(p2, i0, i1, i3)
        """
        self.optimize_loop(ops, expected, preamble)
        #self.check_expanded_fail_descr("i0", rop.GUARD_NONNULL_CLASS)

    def test_merge_guard_nonnull_guard_value(self):
        ops = """
        [p1, i0, i1, i2, p2]
        guard_nonnull(p1) [i0]
        i3 = int_add(i1, i2)
        guard_value(p1, ConstPtr(myptr)) [i1]
        jump(p2, i0, i1, i3, p2)
        """
        preamble = """
        [p1, i0, i1, i2, p2]
        guard_value(p1, ConstPtr(myptr)) [i0]
        i3 = int_add(i1, i2)
        jump(p2, i0, i1, i3)
        """
        expected = """
        [p2, i0, i1, i2]
        guard_value(p2, ConstPtr(myptr)) [i0]
        i3 = int_add(i1, i2)
        jump(ConstPtr(myptr), i0, i1, i3)
        """
        self.optimize_loop(ops, expected, preamble)
        #self.check_expanded_fail_descr("i0", rop.GUARD_VALUE)

    def test_merge_guard_nonnull_guard_class_guard_value(self):
        ops = """
        [p1, i0, i1, i2, p2]
        guard_nonnull(p1) [i0]
        i3 = int_add(i1, i2)
        guard_class(p1, ConstClass(node_vtable)) [i2]
        i4 = int_sub(i3, 1)
        guard_value(p1, ConstPtr(myptr)) [i1]
        jump(p2, i0, i1, i4, p2)
        """
        preamble = """
        [p1, i0, i1, i2, p2]
        guard_value(p1, ConstPtr(myptr)) [i0]
        i3 = int_add(i1, i2)
        i4 = int_sub(i3, 1)
        jump(p2, i0, i1, i4)
        """
        expected = """
        [p2, i0, i1, i2]
        guard_value(p2, ConstPtr(myptr)) [i0]
        i3 = int_add(i1, i2)
        i4 = int_sub(i3, 1)
        jump(ConstPtr(myptr), i0, i1, i4)
        """
        self.optimize_loop(ops, expected, preamble)
        #self.check_expanded_fail_descr("i0", rop.GUARD_VALUE)

    def test_guard_class_oois(self):
        ops = """
        [p1]
        guard_class(p1, ConstClass(node_vtable2)) []
        i = instance_ptr_ne(ConstPtr(myptr), p1)
        guard_true(i) []
        jump(p1)
        """
        preamble = """
        [p1]
        guard_class(p1, ConstClass(node_vtable2)) []
        jump(p1)
        """
        expected = """
        [p1]
        jump(p1)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_oois_of_itself(self):
        ops = """
        [p0]
        p1 = getfield_gc(p0, descr=nextdescr)
        p2 = getfield_gc(p0, descr=nextdescr)
        i1 = ptr_eq(p1, p2)
        guard_true(i1) []
        i2 = ptr_ne(p1, p2)
        guard_false(i2) []
        jump(p0)
        """
        preamble = """
        [p0]
        p1 = getfield_gc(p0, descr=nextdescr)
        jump(p0)
        """
        expected = """
        [p0]
        jump(p0)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_remove_multiple_add_1(self):
        ops = """
        [i0]
        i1 = int_add(i0, 1)
        i2 = int_add(i1, 2)
        i3 = int_add(i2, 1)
        jump(i3)
        """
        expected = """
        [i0]
        i1 = int_add(i0, 1)
        i2 = int_add(i0, 3)
        i3 = int_add(i0, 4)
        jump(i3)
        """
        self.optimize_loop(ops, expected)

    def test_remove_multiple_add_2(self):
        ops = """
        [i0]
        i1 = int_add(i0, 1)
        i2 = int_add(2, i1)
        i3 = int_add(i2, 1)
        i4 = int_mul(i3, 5)
        i5 = int_add(5, i4)
        i6 = int_add(1, i5)
        i7 = int_add(i2, i6)
        i8 = int_add(i7, 1)
        jump(i8)
        """
        expected = """
        [i0]
        i1 = int_add(i0, 1)
        i2 = int_add(i0, 3)
        i3 = int_add(i0, 4)
        i4 = int_mul(i3, 5)
        i5 = int_add(5, i4)
        i6 = int_add(i4, 6)
        i7 = int_add(i2, i6)
        i8 = int_add(i7, 1)
        jump(i8)
        """
        self.optimize_loop(ops, expected)

    def test_remove_multiple_add_3(self):
        ops = """
        [i0]
        i1 = int_add(i0, %s)
        i2 = int_add(i1, %s)
        i3 = int_add(i0, %s)
        i4 = int_add(i3, %s)
        jump(i4)
        """ % (sys.maxint - 1, sys.maxint - 2, -sys.maxint, -sys.maxint + 1)
        expected = """
        [i0]
        i1 = int_add(i0, %s)
        i2 = int_add(i0, %s)
        i3 = int_add(i0, %s)
        i4 = int_add(i0, %s)
        jump(i4)
        """ % (sys.maxint - 1, -5, -sys.maxint, 3)
        self.optimize_loop(ops, expected)

    def test_remove_duplicate_pure_op(self):
        ops = """
        [p1, p2]
        i1 = ptr_eq(p1, p2)
        i2 = ptr_eq(p1, p2)
        i3 = int_add(i1, 1)
        i3b = int_is_true(i3)
        guard_true(i3b) []
        i4 = int_add(i2, 1)
        i4b = int_is_true(i4)
        guard_true(i4b) []
        escape(i3)
        escape(i4)
        guard_true(i1) []
        guard_true(i2) []
        jump(p1, p2)
        """
        preamble = """
        [p1, p2]
        i1 = ptr_eq(p1, p2)
        i3 = int_add(i1, 1)
        i3b = int_is_true(i3)
        guard_true(i3b) []
        escape(i3)
        escape(i3)
        guard_true(i1) []
        jump(p1, p2)
        """
        expected = """
        [p1, p2]
        escape(2)
        escape(2)
        jump(p1, p2)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_remove_duplicate_pure_op_with_descr(self):
        ops = """
        [p1]
        i0 = arraylen_gc(p1, descr=arraydescr)
        i1 = int_gt(i0, 0)
        guard_true(i1) []
        i2 = arraylen_gc(p1, descr=arraydescr)
        i3 = int_gt(i0, 0)
        guard_true(i3) []
        jump(p1)
        """
        preamble = """
        [p1]
        i0 = arraylen_gc(p1, descr=arraydescr)
        i1 = int_gt(i0, 0)
        guard_true(i1) []
        jump(p1)
        """
        expected = """
        [p1]
        jump(p1)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_remove_duplicate_pure_op_ovf(self):
        ops = """
        [i1]
        i3 = int_add_ovf(i1, 1)
        guard_no_overflow() []
        i3b = int_is_true(i3)
        guard_true(i3b) []
        i4 = int_add_ovf(i1, 1)
        guard_no_overflow() []
        i4b = int_is_true(i4)
        guard_true(i4b) []
        escape(i3)
        escape(i4)
        jump(i1)
        """
        preamble = """
        [i1]
        i3 = int_add_ovf(i1, 1)
        guard_no_overflow() []
        i3b = int_is_true(i3)
        guard_true(i3b) []
        escape(i3)
        escape(i3)
        jump(i1, i3)
        """
        expected = """
        [i1, i3]
        escape(i3)
        escape(i3)
        jump(i1, i3)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_remove_duplicate_pure_op_ovf_with_lazy_setfield(self):
        ops = """
        [i1, p1]
        i3 = int_add_ovf(i1, 1)
        guard_no_overflow() []
        i3b = int_is_true(i3)
        guard_true(i3b) []
        setfield_gc(p1, i1, descr=valuedescr)
        i4 = int_add_ovf(i1, 1)
        guard_no_overflow() []
        i4b = int_is_true(i4)
        guard_true(i4b) []
        escape(i3)
        escape(i4)
        jump(i1, p1)
        """
        preamble = """
        [i1, p1]
        i3 = int_add_ovf(i1, 1)
        guard_no_overflow() []
        i3b = int_is_true(i3)
        guard_true(i3b) []
        setfield_gc(p1, i1, descr=valuedescr)
        escape(i3)
        escape(i3)
        jump(i1, p1, i3)
        """
        expected = """
        [i1, p1, i3]
        setfield_gc(p1, i1, descr=valuedescr)
        escape(i3)
        escape(i3)
        jump(i1, p1, i3)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_ovf_guard_in_short_preamble1(self):
        ops = """
        [p8, p11, i24]
        p26 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p26, i24, descr=adescr)
        i34 = getfield_gc_pure(p11, descr=valuedescr)
        i35 = getfield_gc_pure(p26, descr=adescr)
        i36 = int_add_ovf(i34, i35)
        guard_no_overflow() []
        jump(p8, p11, i35)
        """
        expected = """
        [p8, p11, i26]
        jump(p8, p11, i26)
        """
        self.optimize_loop(ops, expected)

    def test_ovf_guard_in_short_preamble2(self):
        ops = """
        [p8, p11, p12]
        p16 = getfield_gc(p8, descr=valuedescr)
        i17 = getfield_gc(p8, descr=nextdescr)
        i19 = getfield_gc(p16, descr=valuedescr)
        i20 = int_ge(i17, i19)
        guard_false(i20) []
        i21 = getfield_gc(p16, descr=otherdescr)
        i22 = getfield_gc(p16, descr=nextdescr)
        i23 = int_mul(i17, i22)
        i24 = int_add(i21, i23)
        p26 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p26, i24, descr=adescr)
        i28 = int_add(i17, 1)
        setfield_gc(p8, i28, descr=nextdescr)
        i34 = getfield_gc_pure(p11, descr=valuedescr)
        i35 = getfield_gc_pure(p26, descr=adescr)
        guard_nonnull(p12) []
        i36 = int_add_ovf(i34, i35)
        guard_no_overflow() []
        p38 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p38, i36, descr=adescr)
        jump(p8, p11, p26)
        """
        expected = """
        [p8, p11, i24, i39, i19, p16, i21, i34]
        i40 = int_ge(i39, i19)
        guard_false(i40) []
        i41 = getfield_gc(p16, descr=nextdescr)
        i42 = int_mul(i39, i41)
        i43 = int_add(i21, i42)
        i44 = int_add(i39, 1)
        setfield_gc(p8, i44, descr=nextdescr)
        i45 = int_add_ovf(i34, i43)
        guard_no_overflow() []
        jump(p8, p11, i43, i44, i19, p16, i21, i34)
        """
        self.optimize_loop(ops, expected)

    def test_int_and_or_with_zero(self):
        ops = """
        [i0, i1]
        i2 = int_and(i0, 0)
        i3 = int_and(0, i2)
        i4 = int_or(i2, i1)
        i5 = int_or(i0, i3)
        jump(i4, i5)
        """
        expected = """
        [i0, i1]
        jump(i1, i0)
        """
        self.optimize_loop(ops, expected)

    def test_fold_partially_constant_add_sub(self):
        ops = """
        [i0]
        i1 = int_sub(i0, 0)
        jump(i1)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected)

        ops = """
        [i0]
        i1 = int_add(i0, 0)
        jump(i1)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected)

        ops = """
        [i0]
        i1 = int_add(0, i0)
        jump(i1)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected)

    def test_fold_partially_constant_add_sub_ovf(self):
        ops = """
        [i0]
        i1 = int_sub_ovf(i0, 0)
        guard_no_overflow() []
        jump(i1)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected)

        ops = """
        [i0]
        i1 = int_add_ovf(i0, 0)
        guard_no_overflow() []
        jump(i1)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected)

        ops = """
        [i0]
        i1 = int_add_ovf(0, i0)
        guard_no_overflow() []
        jump(i1)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected)

    def test_fold_int_sub_ovf_xx(self):
        ops = """
        [i0]
        i1 = int_sub_ovf(i0, i0)
        guard_no_overflow() []
        escape(i1)
        jump(i1)
        """
        expected = """
        []
        escape(0)
        jump()
        """
        self.optimize_loop(ops, expected)

    def test_fold_partially_constant_shift(self):
        ops = """
        [i0]
        i1 = int_lshift(i0, 0)
        i2 = int_rshift(i1, 0)
        i3 = int_eq(i2, i0)
        guard_true(i3) []
        jump(i2)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected)

    def test_fold_partially_constant_uint_floordiv(self):
        ops = """
        [i0]
        i1 = uint_floordiv(i0, 1)
        jump(i1)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected)

    def test_fold_partially_constant_xor(self):
        ops = """
        [i0, i1]
        i2 = int_xor(i0, 23)
        i3 = int_xor(i1, 0)
        jump(i2, i3)
        """
        expected = """
        [i0, i1]
        i2 = int_xor(i0, 23)
        jump(i2, i1)
        """
        self.optimize_loop(ops, expected)

    # ----------

    def test_residual_call_does_not_invalidate_caches(self):
        ops = """
        [p1, p2]
        i1 = getfield_gc(p1, descr=valuedescr)
        i2 = call(i1, descr=nonwritedescr)
        i3 = getfield_gc(p1, descr=valuedescr)
        escape(i1)
        escape(i3)
        jump(p1, p2)
        """
        expected = """
        [p1, p2]
        i1 = getfield_gc(p1, descr=valuedescr)
        i2 = call(i1, descr=nonwritedescr)
        escape(i1)
        escape(i1)
        jump(p1, p2)
        """
        self.optimize_loop(ops, expected)

    def test_residual_call_invalidate_some_caches(self):
        ops = """
        [p1, p2]
        i1 = getfield_gc(p1, descr=adescr)
        i2 = getfield_gc(p1, descr=bdescr)
        i3 = call(i1, descr=writeadescr)
        i4 = getfield_gc(p1, descr=adescr)
        i5 = getfield_gc(p1, descr=bdescr)
        escape(i1)
        escape(i2)
        escape(i4)
        escape(i5)
        jump(p1, p2)
        """
        expected = """
        [p1, p2]
        i1 = getfield_gc(p1, descr=adescr)
        i2 = getfield_gc(p1, descr=bdescr)
        i3 = call(i1, descr=writeadescr)
        i4 = getfield_gc(p1, descr=adescr)
        escape(i1)
        escape(i2)
        escape(i4)
        escape(i2)
        jump(p1, p2)
        """
        self.optimize_loop(ops, expected)

    def test_residual_call_invalidate_arrays(self):
        ops = """
        [p1, p2, i1]
        p3 = getarrayitem_gc(p1, 0, descr=arraydescr2)
        p4 = getarrayitem_gc(p2, 1, descr=arraydescr2)
        i3 = call(i1, descr=writeadescr)
        p5 = getarrayitem_gc(p1, 0, descr=arraydescr2)
        p6 = getarrayitem_gc(p2, 1, descr=arraydescr2)
        escape(p3)
        escape(p4)
        escape(p5)
        escape(p6)
        jump(p1, p2, i1)
        """
        expected = """
        [p1, p2, i1]
        p3 = getarrayitem_gc(p1, 0, descr=arraydescr2)
        p4 = getarrayitem_gc(p2, 1, descr=arraydescr2)
        i3 = call(i1, descr=writeadescr)
        escape(p3)
        escape(p4)
        escape(p3)
        escape(p4)
        jump(p1, p2, i1)
        """
        self.optimize_loop(ops, expected)

    def test_residual_call_invalidate_some_arrays(self):
        ops = """
        [p1, p2, i1]
        p3 = getarrayitem_gc(p2, 0, descr=arraydescr2)
        p4 = getarrayitem_gc(p2, 1, descr=arraydescr2)
        i2 = getarrayitem_gc(p1, 1, descr=arraydescr)
        i3 = call(i1, descr=writearraydescr)
        p5 = getarrayitem_gc(p2, 0, descr=arraydescr2)
        p6 = getarrayitem_gc(p2, 1, descr=arraydescr2)
        i4 = getarrayitem_gc(p1, 1, descr=arraydescr)
        escape(p3)
        escape(p4)
        escape(p5)
        escape(p6)
        escape(i2)
        escape(i4)
        jump(p1, p2, i1)
        """
        expected = """
        [p1, p2, i1]
        p3 = getarrayitem_gc(p2, 0, descr=arraydescr2)
        p4 = getarrayitem_gc(p2, 1, descr=arraydescr2)
        i2 = getarrayitem_gc(p1, 1, descr=arraydescr)
        i3 = call(i1, descr=writearraydescr)
        i4 = getarrayitem_gc(p1, 1, descr=arraydescr)
        escape(p3)
        escape(p4)
        escape(p3)
        escape(p4)
        escape(i2)
        escape(i4)
        jump(p1, p2, i1)
        """
        self.optimize_loop(ops, expected)

    def test_residual_call_invalidates_some_read_caches_1(self):
        ops = """
        [p1, i1, p2, i2]
        setfield_gc(p1, i1, descr=valuedescr)
        setfield_gc(p2, i2, descr=adescr)
        i3 = call(i1, descr=readadescr)
        setfield_gc(p1, i3, descr=valuedescr)
        setfield_gc(p2, i3, descr=adescr)
        jump(p1, i1, p2, i2)
        """
        expected = """
        [p1, i1, p2, i2]
        setfield_gc(p2, i2, descr=adescr)
        i3 = call(i1, descr=readadescr)
        setfield_gc(p1, i3, descr=valuedescr)
        setfield_gc(p2, i3, descr=adescr)
        jump(p1, i1, p2, i2)
        """
        self.optimize_loop(ops, expected)

    def test_residual_call_invalidates_some_read_caches_2(self):
        ops = """
        [p1, i1, p2, i2]
        setfield_gc(p1, i1, descr=valuedescr)
        setfield_gc(p2, i2, descr=adescr)
        i3 = call(i1, descr=writeadescr)
        setfield_gc(p1, i3, descr=valuedescr)
        setfield_gc(p2, i3, descr=adescr)
        jump(p1, i1, p2, i2)
        """
        expected = """
        [p1, i1, p2, i2]
        setfield_gc(p2, i2, descr=adescr)
        i3 = call(i1, descr=writeadescr)
        setfield_gc(p1, i3, descr=valuedescr)
        setfield_gc(p2, i3, descr=adescr)
        jump(p1, i1, p2, i2)
        """
        self.optimize_loop(ops, expected)

    def test_residual_call_invalidates_some_read_caches_3(self):
        ops = """
        [p1, i1, p2, i2]
        setfield_gc(p1, i1, descr=valuedescr)
        setfield_gc(p2, i2, descr=adescr)
        i3 = call(i1, descr=plaincalldescr)
        setfield_gc(p1, i3, descr=valuedescr)
        setfield_gc(p2, i3, descr=adescr)
        jump(p1, i1, p2, i2)
        """
        self.optimize_loop(ops, ops)

    def test_call_assembler_invalidates_caches(self):
        ops = '''
        [p1, i1, i4]
        setfield_gc(p1, i1, descr=valuedescr)
        i3 = call_assembler(i1, descr=asmdescr)
        setfield_gc(p1, i3, descr=valuedescr)
        jump(p1, i4, i3)
        '''
        preamble = '''
        [p1, i1, i4]
        setfield_gc(p1, i1, descr=valuedescr)
        i3 = call_assembler(i1, descr=asmdescr)
        setfield_gc(p1, i3, descr=valuedescr)
        i143 = same_as(i3) # Should be killed by backend
        jump(p1, i4, i3)
        '''
        self.optimize_loop(ops, ops, preamble)

    def test_call_assembler_invalidates_heap_knowledge(self):
        ops = '''
        [p1, i1, i4]
        setfield_gc(p1, i1, descr=valuedescr)
        i3 = call_assembler(i1, descr=asmdescr)
        setfield_gc(p1, i1, descr=valuedescr)
        jump(p1, i4, i3)
        '''
        self.optimize_loop(ops, ops, ops)

    def test_call_pure_invalidates_caches(self):
        # CALL_PURE should still force the setfield_gc() to occur before it
        ops = '''
        [p1, i1, i4]
        setfield_gc(p1, i1, descr=valuedescr)
        i3 = call_pure(p1, descr=elidablecalldescr)
        setfield_gc(p1, i3, descr=valuedescr)
        jump(p1, i4, i3)
        '''
        expected = '''
        [p1, i4, i3, i5]
        setfield_gc(p1, i5, descr=valuedescr)
        jump(p1, i3, i5, i5)
        '''
        preamble = '''
        [p1, i1, i4]
        setfield_gc(p1, i1, descr=valuedescr)
        i3 = call(p1, descr=elidablecalldescr)
        setfield_gc(p1, i3, descr=valuedescr)
        i148 = same_as(i3)
        i147 = same_as(i3)
        jump(p1, i4, i3, i148)
        '''
        self.optimize_loop(ops, expected, preamble)

    def test_call_pure_invalidates_caches_2(self):
        # same as test_call_pure_invalidates_caches, but with
        # an EF_ELIDABLE_OR_MEMORYERROR, which can still be moved
        # out of the main loop potentially into the short preamble
        ops = '''
        [p1, i1, i4]
        setfield_gc(p1, i1, descr=valuedescr)
        i3 = call_pure(p1, descr=elidable2calldescr)
        guard_no_exception() []
        setfield_gc(p1, i3, descr=valuedescr)
        jump(p1, i4, i3)
        '''
        expected = '''
        [p1, i4, i3, i5]
        setfield_gc(p1, i5, descr=valuedescr)
        jump(p1, i3, i5, i5)
        '''
        preamble = '''
        [p1, i1, i4]
        setfield_gc(p1, i1, descr=valuedescr)
        i3 = call(p1, descr=elidable2calldescr)
        guard_no_exception() []
        setfield_gc(p1, i3, descr=valuedescr)
        i148 = same_as(i3)
        i147 = same_as(i3)
        jump(p1, i4, i3, i148)
        '''
        self.optimize_loop(ops, expected, preamble)

    def test_call_pure_can_raise(self):
        # same as test_call_pure_invalidates_caches, but this time
        # with an EF_ELIDABLE_CAN_RAISE, which *cannot* be moved
        # out of the main loop: the problem is that it cannot be
        # present in the short preamble, because nobody would catch
        # the potential exception
        ops = '''
        [p1, i1, i4]
        setfield_gc(p1, i1, descr=valuedescr)
        i3 = call_pure(p1, descr=elidable3calldescr)
        guard_no_exception() []
        setfield_gc(p1, i3, descr=valuedescr)
        jump(p1, i4, i3)
        '''
        expected = '''
        [p1, i1, i4]
        setfield_gc(p1, i1, descr=valuedescr)
        i3 = call(p1, descr=elidable3calldescr)
        guard_no_exception() []
        setfield_gc(p1, i3, descr=valuedescr)
        jump(p1, i4, i3)
        '''
        preamble = '''
        [p1, i1, i4]
        setfield_gc(p1, i1, descr=valuedescr)
        i3 = call(p1, descr=elidable3calldescr)
        guard_no_exception() []
        setfield_gc(p1, i3, descr=valuedescr)
        i167 = same_as(i3)
        jump(p1, i4, i3)
        '''
        self.optimize_loop(ops, expected, preamble)

    def test_call_pure_invalidates_heap_knowledge(self):
        # CALL_PURE should still force the setfield_gc() to occur before it
        ops = '''
        [p1, i1, i4]
        setfield_gc(p1, i1, descr=valuedescr)
        i3 = call_pure(p1, descr=elidablecalldescr)
        setfield_gc(p1, i1, descr=valuedescr)
        jump(p1, i4, i3)
        '''
        expected = '''
        [p1, i4, i3, i5]
        setfield_gc(p1, i4, descr=valuedescr)
        jump(p1, i3, i5, i5)
        '''
        preamble = '''
        [p1, i1, i4]
        setfield_gc(p1, i1, descr=valuedescr)
        i3 = call(p1, descr=elidablecalldescr)
        i151 = same_as(i3)
        jump(p1, i4, i3, i151)
        '''
        self.optimize_loop(ops, expected, preamble)

    def test_call_pure_constant_folding(self):
        arg_consts = [ConstInt(i) for i in (123456, 4, 5, 6)]
        call_pure_results = {tuple(arg_consts): ConstInt(42)}
        ops = '''
        [i0, i1, i2]
        escape(i1)
        escape(i2)
        i3 = call_pure(123456, 4, 5, 6, descr=elidablecalldescr)
        i4 = call_pure(123456, 4, i0, 6, descr=elidablecalldescr)
        jump(i0, i3, i4)
        '''
        preamble = '''
        [i0, i1, i2]
        escape(i1)
        escape(i2)
        i4 = call(123456, 4, i0, 6, descr=elidablecalldescr)
        i153 = same_as(i4)
        jump(i0, i4, i153)
        '''
        expected = '''
        [i0, i4, i5]
        escape(42)
        escape(i4)
        jump(i0, i5, i5)
        '''
        self.optimize_loop(ops, expected, preamble, call_pure_results)

    def test_call_pure_constant_folding_memoryerr(self):
        ops = '''
        [p0, i0]
        escape(i0)
        i3 = call_pure(123456, p0, descr=elidable2calldescr)
        guard_no_exception() []
        jump(p0, i3)
        '''
        preamble = '''
        [p0, i0]
        escape(i0)
        i3 = call(123456, p0, descr=elidable2calldescr)
        guard_no_exception() []
        i4 = same_as(i3)
        jump(p0, i3, i4)
        '''
        expected = '''
        [p0, i3, i4]
        escape(i3)
        jump(p0, i4, i4)
        '''
        self.optimize_loop(ops, expected, preamble)

    def test_call_pure_constant_folding_exc(self):
        # CALL_PURE may be followed by GUARD_NO_EXCEPTION
        # we can't remove such call_pures from the loop,
        # because the short preamble can't call them safely.
        # We can still check that an all-constant call_pure is removed,
        # and that duplicate call_pures are folded.
        arg_consts = [ConstInt(i) for i in (123456, 4, 5, 6)]
        call_pure_results = {tuple(arg_consts): ConstInt(42)}
        ops = '''
        [i0, i1, i2, i9]
        escape(i1)
        escape(i2)
        escape(i9)
        i3 = call_pure(123456, 4, 5, 6, descr=elidable3calldescr)
        guard_no_exception() []
        i4 = call_pure(123456, 4, i0, 6, descr=elidable3calldescr)
        guard_no_exception() []
        i5 = call_pure(123456, 4, i0, 6, descr=elidable3calldescr)
        guard_no_exception() []
        jump(i0, i3, i4, i5)
        '''
        preamble = '''
        [i0, i1, i2, i9]
        escape(i1)
        escape(i2)
        escape(i9)
        i4 = call(123456, 4, i0, 6, descr=elidable3calldescr)
        guard_no_exception() []
        jump(i0, i4)
        '''
        expected = '''
        [i0, i2]
        escape(42)
        escape(i2)
        escape(i2)
        i4 = call(123456, 4, i0, 6, descr=elidable3calldescr)
        guard_no_exception() []
        jump(i0, i4)
        '''
        self.optimize_loop(ops, expected, preamble, call_pure_results)

    def test_call_pure_returning_virtual(self):
        # XXX: This kind of loop invaraint call_pure will be forced
        #      both in the preamble and in the peeled loop
        ops = '''
        [p1, i1, i2]
        p2 = call_pure(0, p1, i1, i2, descr=strslicedescr)
        guard_no_exception() []
        escape(p2)
        jump(p1, i1, i2)
        '''
        preamble = '''
        [p1, i1, i2]
        i6 = int_sub(i2, i1)
        p2 = newstr(i6)
        copystrcontent(p1, p2, i1, 0, i6)
        escape(p2)
        jump(p1, i1, i2, i6)
        '''
        expected = '''
        [p1, i1, i2, i6]
        p2 = newstr(i6)
        copystrcontent(p1, p2, i1, 0, i6)
        escape(p2)
        jump(p1, i1, i2, i6)
        '''
        self.optimize_loop(ops, expected, preamble)


    # ----------

    def test_vref_nonvirtual_nonescape(self):
        ops = """
        [p1]
        p2 = virtual_ref(p1, 5)
        virtual_ref_finish(p2, p1)
        jump(p1)
        """
        expected = """
        [p1]
        p0 = force_token()
        jump(p1)
        """
        self.optimize_loop(ops, expected, expected)

    def test_vref_nonvirtual_escape(self):
        ops = """
        [p1]
        p2 = virtual_ref(p1, 5)
        escape(p2)
        virtual_ref_finish(p2, p1)
        jump(p1)
        """
        expected = """
        [p1]
        p0 = force_token()
        p2 = new_with_vtable(ConstClass(jit_virtual_ref_vtable))
        setfield_gc(p2, NULL, descr=virtualforceddescr)
        setfield_gc(p2, p0, descr=virtualtokendescr)
        escape(p2)
        setfield_gc(p2, p1, descr=virtualforceddescr)
        setfield_gc(p2, NULL, descr=virtualtokendescr)
        jump(p1)
        """
        # XXX we should optimize a bit more the case of a nonvirtual.
        # in theory it is enough to just do 'p2 = p1'.
        self.optimize_loop(ops, expected, expected)

    def test_vref_virtual_1(self):
        ops = """
        [p0, i1]
        #
        p1 = new_with_vtable(ConstClass(node_vtable))
        p1b = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1b, 252, descr=valuedescr)
        setfield_gc(p1, p1b, descr=nextdescr)
        #
        p2 = virtual_ref(p1, 3)
        setfield_gc(p0, p2, descr=nextdescr)
        call_may_force(i1, descr=mayforcevirtdescr)
        guard_not_forced() [i1]
        virtual_ref_finish(p2, p1)
        setfield_gc(p0, NULL, descr=nextdescr)
        jump(p0, i1)
        """
        expected = """
        [p0, i1]
        p3 = force_token()
        #
        p2 = new_with_vtable(ConstClass(jit_virtual_ref_vtable))
        setfield_gc(p2, NULL, descr=virtualforceddescr)
        setfield_gc(p2, p3, descr=virtualtokendescr)
        setfield_gc(p0, p2, descr=nextdescr)
        #
        call_may_force(i1, descr=mayforcevirtdescr)
        guard_not_forced() [i1]
        #
        setfield_gc(p0, NULL, descr=nextdescr)
        p1 = new_with_vtable(ConstClass(node_vtable))
        p1b = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1b, 252, descr=valuedescr)
        setfield_gc(p1, p1b, descr=nextdescr)
        setfield_gc(p2, p1, descr=virtualforceddescr)
        setfield_gc(p2, NULL, descr=virtualtokendescr)
        jump(p0, i1)
        """
        self.optimize_loop(ops, expected, expected)

    def test_vref_virtual_2(self):
        ops = """
        [p0, i1]
        #
        p1 = new_with_vtable(ConstClass(node_vtable))
        p1b = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1b, i1, descr=valuedescr)
        setfield_gc(p1, p1b, descr=nextdescr)
        #
        p2 = virtual_ref(p1, 2)
        setfield_gc(p0, p2, descr=nextdescr)
        call_may_force(i1, descr=mayforcevirtdescr)
        guard_not_forced() [p2, p1]
        virtual_ref_finish(p2, p1)
        setfield_gc(p0, NULL, descr=nextdescr)
        jump(p0, i1)
        """
        expected = """
        [p0, i1]
        p3 = force_token()
        #
        p2 = new_with_vtable(ConstClass(jit_virtual_ref_vtable))
        setfield_gc(p2, NULL, descr=virtualforceddescr)
        setfield_gc(p2, p3, descr=virtualtokendescr)
        setfield_gc(p0, p2, descr=nextdescr)
        #
        call_may_force(i1, descr=mayforcevirtdescr)
        guard_not_forced() [p2, i1]
        #
        setfield_gc(p0, NULL, descr=nextdescr)
        p1 = new_with_vtable(ConstClass(node_vtable))
        p1b = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1b, i1, descr=valuedescr)
        setfield_gc(p1, p1b, descr=nextdescr)
        setfield_gc(p2, p1, descr=virtualforceddescr)
        setfield_gc(p2, NULL, descr=virtualtokendescr)
        jump(p0, i1)
        """
        # the point of this test is that 'i1' should show up in the fail_args
        # of 'guard_not_forced', because it was stored in the virtual 'p1b'.
        self.optimize_loop(ops, expected)
        #self.check_expanded_fail_descr('''p2, p1
        #    where p1 is a node_vtable, nextdescr=p1b
        #    where p1b is a node_vtable, valuedescr=i1
        #    ''', rop.GUARD_NOT_FORCED)

    def test_vref_virtual_and_lazy_setfield(self):
        ops = """
        [p0, i1]
        #
        p1 = new_with_vtable(ConstClass(node_vtable))
        p1b = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1b, i1, descr=valuedescr)
        setfield_gc(p1, p1b, descr=nextdescr)
        #
        p2 = virtual_ref(p1, 2)
        setfield_gc(p0, p2, descr=refdescr)
        call(i1, descr=nonwritedescr)
        guard_no_exception() [p2, p1]
        virtual_ref_finish(p2, p1)
        setfield_gc(p0, NULL, descr=refdescr)
        escape()
        jump(p0, i1)
        """
        preamble = """
        [p0, i1]
        p3 = force_token()
        call(i1, descr=nonwritedescr)
        guard_no_exception() [p3, i1, p0]
        setfield_gc(p0, NULL, descr=refdescr)
        escape()
        jump(p0, i1)
        """
        expected = """
        [p0, i1]
        p3 = force_token()
        call(i1, descr=nonwritedescr)
        guard_no_exception() [p3, i1, p0]
        setfield_gc(p0, NULL, descr=refdescr)
        escape()
        jump(p0, i1)
        """
        self.optimize_loop(ops, expected, preamble)
        # the fail_args contain [p3, i1, p0]:
        #  - p3 is from the virtual expansion of p2
        #  - i1 is from the virtual expansion of p1
        #  - p0 is from the extra pendingfields
        #self.loop.inputargs[0].value = self.nodeobjvalue
        #self.check_expanded_fail_descr('''p2, p1
        #    p0.refdescr = p2
        #    where p2 is a jit_virtual_ref_vtable, virtualtokendescr=p3
        #    where p1 is a node_vtable, nextdescr=p1b
        #    where p1b is a node_vtable, valuedescr=i1
        #    ''', rop.GUARD_NO_EXCEPTION)

    def test_vref_virtual_after_finish(self):
        ops = """
        [i1]
        p1 = new_with_vtable(ConstClass(node_vtable))
        p2 = virtual_ref(p1, 7)
        escape(p2)
        virtual_ref_finish(p2, p1)
        call_may_force(i1, descr=mayforcevirtdescr)
        guard_not_forced() []
        jump(i1)
        """
        expected = """
        [i1]
        p3 = force_token()
        p2 = new_with_vtable(ConstClass(jit_virtual_ref_vtable))
        setfield_gc(p2, NULL, descr=virtualforceddescr)
        setfield_gc(p2, p3, descr=virtualtokendescr)
        escape(p2)
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p2, p1, descr=virtualforceddescr)
        setfield_gc(p2, NULL, descr=virtualtokendescr)
        call_may_force(i1, descr=mayforcevirtdescr)
        guard_not_forced() []
        jump(i1)
        """
        self.optimize_loop(ops, expected, expected)

    def test_vref_nonvirtual_and_lazy_setfield(self):
        ops = """
        [i1, p1]
        p2 = virtual_ref(p1, 23)
        escape(p2)
        virtual_ref_finish(p2, p1)
        call_may_force(i1, descr=mayforcevirtdescr)
        guard_not_forced() [i1]
        jump(i1, p1)
        """
        expected = """
        [i1, p1]
        p3 = force_token()
        p2 = new_with_vtable(ConstClass(jit_virtual_ref_vtable))
        setfield_gc(p2, NULL, descr=virtualforceddescr)
        setfield_gc(p2, p3, descr=virtualtokendescr)
        escape(p2)
        setfield_gc(p2, p1, descr=virtualforceddescr)
        setfield_gc(p2, NULL, descr=virtualtokendescr)
        call_may_force(i1, descr=mayforcevirtdescr)
        guard_not_forced() [i1]
        jump(i1, p1)
        """
        self.optimize_loop(ops, expected, expected)

    # ----------

    def test_arraycopy_1(self):
        ops = '''
        [i0]
        p1 = new_array(3, descr=arraydescr)
        setarrayitem_gc(p1, 1, 1, descr=arraydescr)
        p2 = new_array(3, descr=arraydescr)
        setarrayitem_gc(p2, 1, 3, descr=arraydescr)
        call(0, p1, p2, 1, 1, 2, descr=arraycopydescr)
        i2 = getarrayitem_gc(p2, 1, descr=arraydescr)
        jump(i2)
        '''
        expected = '''
        []
        jump()
        '''
        self.optimize_loop(ops, expected)

    def test_arraycopy_2(self):
        ops = '''
        [i0]
        p1 = new_array(3, descr=arraydescr)
        p2 = new_array(3, descr=arraydescr)
        setarrayitem_gc(p1, 0, i0, descr=arraydescr)
        setarrayitem_gc(p2, 0, 3, descr=arraydescr)
        call(0, p1, p2, 1, 1, 2, descr=arraycopydescr)
        i2 = getarrayitem_gc(p2, 0, descr=arraydescr)
        jump(i2)
        '''
        expected = '''
        []
        jump()
        '''
        self.optimize_loop(ops, expected)

    def test_arraycopy_not_virtual(self):
        ops = '''
        []
        p1 = new_array(3, descr=arraydescr)
        p2 = new_array(3, descr=arraydescr)
        setarrayitem_gc(p1, 2, 10, descr=arraydescr)
        setarrayitem_gc(p2, 2, 13, descr=arraydescr)
        call(0, p1, p2, 0, 0, 3, descr=arraycopydescr)
        escape(p2)
        jump()
        '''
        expected = '''
        []
        p2 = new_array(3, descr=arraydescr)
        setarrayitem_gc(p2, 2, 10, descr=arraydescr)
        escape(p2)
        jump()
        '''
        self.optimize_loop(ops, expected)

    def test_arraycopy_no_elem(self):
        """ this was actually observed in the wild
        """
        ops = '''
        [p1]
        p0 = new_array(0, descr=arraydescr)
        call(0, p0, p1, 0, 0, 0, descr=arraycopydescr)
        jump(p1)
        '''
        expected = '''
        [p1]
        jump(p1)
        '''
        self.optimize_loop(ops, expected)

    def test_arraycopy_dest_not_virtual(self):
        ops = '''
        []
        p1 = new_array_clear(3, descr=arraydescr)
        p2 = new_array(3, descr=arraydescr)
        setarrayitem_gc(p1, 2, 10, descr=arraydescr)
        setarrayitem_gc(p2, 2, 13, descr=arraydescr)
        escape(p2)
        call(0, p1, p2, 0, 0, 3, descr=arraycopydescr)
        escape(p2)
        jump()
        '''
        expected = '''
        []
        p2 = new_array(3, descr=arraydescr)
        setarrayitem_gc(p2, 2, 13, descr=arraydescr)
        escape(p2)
        setarrayitem_gc(p2, 0, 0, descr=arraydescr)
        setarrayitem_gc(p2, 1, 0, descr=arraydescr)
        setarrayitem_gc(p2, 2, 10, descr=arraydescr)
        escape(p2)
        jump()
        '''
        self.optimize_loop(ops, expected)

    def test_arraycopy_dest_not_virtual_too_long(self):
        ops = '''
        []
        p1 = new_array(10, descr=arraydescr)
        p2 = new_array(10, descr=arraydescr)
        setarrayitem_gc(p1, 2, 10, descr=arraydescr)
        setarrayitem_gc(p2, 2, 13, descr=arraydescr)
        escape(p2)
        call(0, p1, p2, 0, 0, 10, descr=arraycopydescr)
        escape(p2)
        jump()
        '''
        expected = '''
        []
        p2 = new_array(10, descr=arraydescr)
        setarrayitem_gc(p2, 2, 13, descr=arraydescr)
        escape(p2)
        p1 = new_array(10, descr=arraydescr)
        setarrayitem_gc(p1, 2, 10, descr=arraydescr)
        call(0, p1, p2, 0, 0, 10, descr=arraycopydescr)
        escape(p2)
        jump()
        '''
        self.optimize_loop(ops, expected)

    def test_bound_lt(self):
        ops = """
        [i0]
        i1 = int_lt(i0, 4)
        guard_true(i1) []
        i2 = int_lt(i0, 5)
        guard_true(i2) []
        jump(i0)
        """
        preamble = """
        [i0]
        i1 = int_lt(i0, 4)
        guard_true(i1) []
        jump(i0)
        """
        expected = """
        [i0]
        jump(i0)
        """

        self.optimize_loop(ops, expected, preamble)

    def test_bound_lt_noguard(self):
        ops = """
        [i0]
        i1 = int_lt(i0, 4)
        i2 = int_lt(i0, 5)
        jump(i2)
        """
        expected = """
        [i0]
        i1 = int_lt(i0, 4)
        i2 = int_lt(i0, 5)
        jump(i2)
        """
        self.optimize_loop(ops, expected, expected)

    def test_bound_lt_noopt(self):
        ops = """
        [i0]
        i1 = int_lt(i0, 4)
        guard_false(i1) []
        i2 = int_lt(i0, 5)
        guard_true(i2) []
        jump(i0)
        """
        preamble = """
        [i0]
        i1 = int_lt(i0, 4)
        guard_false(i1) []
        i2 = int_lt(i0, 5)
        guard_true(i2) []
        jump()
        """
        expected = """
        []
        jump()
        """
        self.optimize_loop(ops, expected, preamble)

    def test_bound_lt_rev(self):
        ops = """
        [i0]
        i1 = int_lt(i0, 4)
        guard_false(i1) []
        i2 = int_gt(i0, 3)
        guard_true(i2) []
        jump(i0)
        """
        preamble = """
        [i0]
        i1 = int_lt(i0, 4)
        guard_false(i1) []
        jump(i0)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_bound_lt_tripple(self):
        ops = """
        [i0]
        i1 = int_lt(i0, 0)
        guard_true(i1) []
        i2 = int_lt(i0, 7)
        guard_true(i2) []
        i3 = int_lt(i0, 5)
        guard_true(i3) []
        jump(i0)
        """
        preamble = """
        [i0]
        i1 = int_lt(i0, 0)
        guard_true(i1) []
        jump(i0)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_bound_lt_add(self):
        ops = """
        [i0]
        i1 = int_lt(i0, 4)
        guard_true(i1) []
        i2 = int_add(i0, 10)
        i3 = int_lt(i2, 15)
        guard_true(i3) []
        jump(i0)
        """
        preamble = """
        [i0]
        i1 = int_lt(i0, 4)
        guard_true(i1) []
        i2 = int_add(i0, 10)
        jump(i0)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_bound_lt_add_before(self):
        ops = """
        [i0]
        i2 = int_add(i0, 10)
        i3 = int_lt(i2, 15)
        guard_true(i3) []
        i1 = int_lt(i0, 6)
        guard_true(i1) []
        jump(i0)
        """
        preamble = """
        [i0]
        i2 = int_add(i0, 10)
        i3 = int_lt(i2, 15)
        guard_true(i3) []
        jump(i0)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_bound_lt_add_ovf(self):
        ops = """
        [i0]
        i1 = int_lt(i0, 4)
        guard_true(i1) []
        i2 = int_add_ovf(i0, 10)
        guard_no_overflow() []
        i3 = int_lt(i2, 15)
        guard_true(i3) []
        jump(i0)
        """
        preamble = """
        [i0]
        i1 = int_lt(i0, 4)
        guard_true(i1) []
        i2 = int_add(i0, 10)
        jump(i0)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_bound_lt_add_ovf_before(self):
        ops = """
        [i0]
        i2 = int_add_ovf(i0, 10)
        guard_no_overflow() []
        i3 = int_lt(i2, 15)
        guard_true(i3) []
        i1 = int_lt(i0, 6)
        guard_true(i1) []
        jump(i0)
        """
        preamble = """
        [i0]
        i2 = int_add_ovf(i0, 10)
        guard_no_overflow() []
        i3 = int_lt(i2, 15)
        guard_true(i3) []
        jump(i0)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_bound_lt_sub1(self):
        ops = """
        [i0]
        i1 = int_lt(i0, 4)
        guard_true(i1) []
        i2 = int_sub(i0, 10)
        i3 = int_lt(i2, -5)
        guard_true(i3) []
        jump(i0)
        """
        preamble = """
        [i0]
        i1 = int_lt(i0, 4)
        guard_true(i1) []
        i2 = int_sub(i0, 10)
        i3 = int_lt(i2, -5)
        guard_true(i3) []
        jump(i0)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_bound_lt_sub2(self):
        ops = """
        [i0]
        i1 = int_lt(i0, 4)
        guard_true(i1) []
        i1p = int_gt(i0, -4)
        guard_true(i1p) []
        i2 = int_sub(i0, 10)
        i3 = int_lt(i2, -5)
        guard_true(i3) []
        jump(i0)
        """
        preamble = """
        [i0]
        i1 = int_lt(i0, 4)
        guard_true(i1) []
        i1p = int_gt(i0, -4)
        guard_true(i1p) []
        i2 = int_sub(i0, 10)
        jump(i0)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_bound_lt_sub_before(self):
        ops = """
        [i0]
        i2 = int_sub(i0, 10)
        i3 = int_lt(i2, -5)
        guard_true(i3) []
        i1 = int_lt(i0, 5)
        guard_true(i1) []
        jump(i0)
        """
        preamble = """
        [i0]
        i2 = int_sub(i0, 10)
        i3 = int_lt(i2, -5)
        guard_true(i3) []
        jump(i0)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_bound_ltle(self):
        ops = """
        [i0]
        i1 = int_lt(i0, 4)
        guard_true(i1) []
        i2 = int_le(i0, 3)
        guard_true(i2) []
        jump(i0)
        """
        preamble = """
        [i0]
        i1 = int_lt(i0, 4)
        guard_true(i1) []
        jump(i0)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_bound_lelt(self):
        ops = """
        [i0]
        i1 = int_le(i0, 4)
        guard_true(i1) []
        i2 = int_lt(i0, 5)
        guard_true(i2) []
        jump(i0)
        """
        preamble = """
        [i0]
        i1 = int_le(i0, 4)
        guard_true(i1) []
        jump(i0)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_bound_gt(self):
        ops = """
        [i0]
        i1 = int_gt(i0, 5)
        guard_true(i1) []
        i2 = int_gt(i0, 4)
        guard_true(i2) []
        jump(i0)
        """
        preamble = """
        [i0]
        i1 = int_gt(i0, 5)
        guard_true(i1) []
        jump(i0)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_bound_gtge(self):
        ops = """
        [i0]
        i1 = int_gt(i0, 5)
        guard_true(i1) []
        i2 = int_ge(i0, 6)
        guard_true(i2) []
        jump(i0)
        """
        preamble = """
        [i0]
        i1 = int_gt(i0, 5)
        guard_true(i1) []
        jump(i0)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_bound_gegt(self):
        ops = """
        [i0]
        i1 = int_ge(i0, 5)
        guard_true(i1) []
        i2 = int_gt(i0, 4)
        guard_true(i2) []
        jump(i0)
        """
        preamble = """
        [i0]
        i1 = int_ge(i0, 5)
        guard_true(i1) []
        jump(i0)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_bound_ovf(self):
        ops = """
        [i0]
        i1 = int_ge(i0, 0)
        guard_true(i1) []
        i2 = int_lt(i0, 10)
        guard_true(i2) []
        i3 = int_add_ovf(i0, 1)
        guard_no_overflow() []
        jump(i3)
        """
        preamble = """
        [i0]
        i1 = int_ge(i0, 0)
        guard_true(i1) []
        i2 = int_lt(i0, 10)
        guard_true(i2) []
        i3 = int_add(i0, 1)
        jump(i3)
        """
        expected = """
        [i0]
        i2 = int_lt(i0, 10)
        guard_true(i2) []
        i3 = int_add(i0, 1)
        jump(i3)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_bound_arraylen(self):
        ops = """
        [i0, p0]
        p1 = new_array(i0, descr=arraydescr)
        i1 = arraylen_gc(p1)
        i2 = int_gt(i1, -1)
        guard_true(i2) []
        setarrayitem_gc(p0, 0, p1)
        jump(i0, p0)
        """
        # The dead arraylen_gc will be eliminated by the backend.
        expected = """
        [i0, p0]
        p1 = new_array(i0, descr=arraydescr)
        i1 = arraylen_gc(p1)
        setarrayitem_gc(p0, 0, p1)
        jump(i0, p0)
        """
        self.optimize_loop(ops, expected)

    def test_bound_strlen(self):
        ops = """
        [p0]
        i0 = strlen(p0)
        i1 = int_ge(i0, 0)
        guard_true(i1) []
        jump(p0)
        """
        preamble = """
        [p0]
        i0 = strlen(p0)
        jump(p0)
        """
        expected = """
        [p0]
        jump(p0)
        """
        self.optimize_strunicode_loop(ops, expected, preamble)

    def test_bound_force_ge_zero(self):
        ops = """
        [p0]
        i0 = arraylen_gc(p0)
        i1 = int_force_ge_zero(i0)
        escape(i1)
        jump(p0)
        """
        preamble = """
        [p0]
        i0 = arraylen_gc(p0)
        escape(i0)
        jump(p0, i0)
        """
        expected = """
        [p0, i0]
        escape(i0)
        jump(p0, i0)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_addsub_const(self):
        ops = """
        [i0]
        i1 = int_add(i0, 1)
        i2 = int_sub(i1, 1)
        i3 = int_add(i2, 1)
        i4 = int_mul(i2, i3)
        jump(i4)
        """
        expected = """
        [i0]
        i1 = int_add(i0, 1)
        i4 = int_mul(i0, i1)
        jump(i4)
        """
        self.optimize_loop(ops, expected)

    def test_addsub_int(self):
        ops = """
        [i0, i10]
        i1 = int_add(i0, i10)
        i2 = int_sub(i1, i10)
        i3 = int_add(i2, i10)
        i4 = int_add(i2, i3)
        jump(i4, i10)
        """
        expected = """
        [i0, i10]
        i1 = int_add(i0, i10)
        i4 = int_add(i0, i1)
        jump(i4, i10)
        """
        self.optimize_loop(ops, expected)

    def test_addsub_int2(self):
        ops = """
        [i0, i10]
        i1 = int_add(i10, i0)
        i2 = int_sub(i1, i10)
        i3 = int_add(i10, i2)
        i4 = int_add(i2, i3)
        jump(i4, i10)
        """
        expected = """
        [i0, i10]
        i1 = int_add(i10, i0)
        i4 = int_add(i0, i1)
        jump(i4, i10)
        """
        self.optimize_loop(ops, expected)

    def test_add_sub_ovf(self):
        ops = """
        [i1]
        i2 = int_add_ovf(i1, 1)
        guard_no_overflow() []
        i3 = int_sub_ovf(i2, 1)
        guard_no_overflow() []
        escape(i3)
        jump(i2)
        """
        expected = """
        [i1]
        i2 = int_add_ovf(i1, 1)
        guard_no_overflow() []
        escape(i1)
        jump(i2)
        """
        self.optimize_loop(ops, expected)

    def test_add_sub_ovf_second_operation_regular(self):
        py.test.skip("Smalltalk would like this to pass")
        # This situation occurs in Smalltalk because it uses 1-based indexing.
        # The below code is equivalent to a loop over an array.
        ops = """
        [i1]
        i2 = int_sub(i1, 1)
        escape(i2)
        i3 = int_add_ovf(i1, 1)
        guard_no_overflow() []
        jump(i3)
        """
        preamble = """
        [i1]
        i2 = int_sub(i1, 1)
        escape(i2)
        i3 = int_add_ovf(i1, 1)
        guard_no_overflow() []
        jump(i3, i1)
        """
        expected = """
        [i1, i2]
        escape(i2)
        i3 = int_add_ovf(i1, 1)
        guard_no_overflow() []
        jump(i3, i1)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_add_sub_ovf_virtual_unroll(self):
        ops = """
        [p15]
        i886 = getfield_gc_pure(p15, descr=valuedescr)
        i888 = int_sub_ovf(i886, 1)
        guard_no_overflow() []
        escape(i888)
        i4360 = getfield_gc_pure(p15, descr=valuedescr)
        i4362 = int_add_ovf(i4360, 1)
        guard_no_overflow() []
        i4360p = int_sub_ovf(i4362, 1)
        guard_no_overflow() []
        p4364 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p4364, i4362, descr=valuedescr)
        jump(p4364)
        """
        expected = """
        [i0]
        i1 = int_sub_ovf(i0, 1)
        guard_no_overflow() []
        escape(i1)
        i2 = int_add_ovf(i0, 1)
        guard_no_overflow() []
        jump(i2)
        """
        self.optimize_loop(ops, expected)

    def test_framestackdepth_overhead(self):
        ops = """
        [p0, i22]
        i1 = getfield_gc(p0, descr=valuedescr)
        i2 = int_gt(i1, i22)
        guard_false(i2) []
        i3 = int_add(i1, 1)
        setfield_gc(p0, i3, descr=valuedescr)
        i4 = int_sub(i3, 1)
        setfield_gc(p0, i4, descr=valuedescr)
        i5 = int_gt(i4, i22)
        guard_false(i5) []
        i6 = int_add(i4, 1)
        p331 = force_token()
        i7 = int_sub(i6, 1)
        setfield_gc(p0, i7, descr=valuedescr)
        jump(p0, i22)
        """
        expected = """
        [p0, i22]
        p331 = force_token()
        jump(p0, i22)
        """
        self.optimize_loop(ops, expected)

    def test_setgetfield_raw(self):
        ops = """
        [p4, p7, i30]
        p16 = getfield_gc(p4, descr=valuedescr)
        p17 = getarrayitem_gc(p4, 1, descr=arraydescr)
        guard_value(p16, ConstPtr(myptr), descr=<Guard3>) []
        i1 = getfield_raw(p7, descr=nextdescr)
        i2 = int_add(i1, i30)
        setfield_raw(p7, 7, descr=nextdescr)
        setfield_raw(p7, i2, descr=nextdescr)
        jump(p4, p7, i30)
        """
        expected = """
        [p4, p7, i30]
        i1 = getfield_raw(p7, descr=nextdescr)
        i2 = int_add(i1, i30)
        setfield_raw(p7, 7, descr=nextdescr)
        setfield_raw(p7, i2, descr=nextdescr)
        jump(p4, p7, i30)
        """
        self.optimize_loop(ops, expected, ops)

    def test_setgetarrayitem_raw(self):
        ops = """
        [p4, p7, i30]
        p16 = getfield_gc(p4, descr=valuedescr)
        guard_value(p16, ConstPtr(myptr), descr=<Guard3>) []
        p17 = getarrayitem_gc(p4, 1, descr=arraydescr)
        i1 = getarrayitem_raw(p7, 1, descr=arraydescr)
        i2 = int_add(i1, i30)
        setarrayitem_raw(p7, 1, 7, descr=arraydescr)
        setarrayitem_raw(p7, 1, i2, descr=arraydescr)
        jump(p4, p7, i30)
        """
        expected = """
        [p4, p7, i30]
        i1 = getarrayitem_raw(p7, 1, descr=arraydescr)
        i2 = int_add(i1, i30)
        setarrayitem_raw(p7, 1, 7, descr=arraydescr)
        setarrayitem_raw(p7, 1, i2, descr=arraydescr)
        jump(p4, p7, i30)
        """
        self.optimize_loop(ops, expected, ops)

    def test_pure(self):
        ops = """
        [p42]
        p53 = getfield_gc(ConstPtr(myptr), descr=nextdescr)
        p59 = getfield_gc_pure(p53, descr=valuedescr)
        i61 = call(1, p59, descr=nonwritedescr)
        jump(p42)
        """
        expected = """
        [p42, p59]
        i61 = call(1, p59, descr=nonwritedescr)
        jump(p42, p59)

        """
        self.node.value = 5
        self.optimize_loop(ops, expected)

    def test_complains_getfieldpure_setfield(self):
        from rpython.jit.metainterp.optimizeopt.heap import BogusImmutableField
        ops = """
        [p3]
        p1 = escape()
        p2 = getfield_gc_pure(p1, descr=nextdescr)
        setfield_gc(p1, p3, descr=nextdescr)
        jump(p3)
        """
        self.raises(BogusImmutableField, self.optimize_loop, ops, "crash!")

    def test_dont_complains_different_field(self):
        ops = """
        [p3]
        p1 = escape()
        p2 = getfield_gc_pure(p1, descr=nextdescr)
        setfield_gc(p1, p3, descr=otherdescr)
        escape(p2)
        jump(p3)
        """
        expected = """
        [p3]
        p1 = escape()
        p2 = getfield_gc_pure(p1, descr=nextdescr)
        setfield_gc(p1, p3, descr=otherdescr)
        escape(p2)
        jump(p3)
        """
        self.optimize_loop(ops, expected)

    def test_dont_complains_different_object(self):
        ops = """
        []
        p1 = escape()
        p2 = getfield_gc_pure(p1, descr=nextdescr)
        p3 = escape()
        setfield_gc(p3, p1, descr=nextdescr)
        jump()
        """
        self.optimize_loop(ops, ops)

    def test_getfield_guard_const(self):
        ops = """
        [p0]
        p20 = getfield_gc(p0, descr=nextdescr)
        guard_nonnull(p20) []
        guard_class(p20, ConstClass(node_vtable)) []
        guard_class(p20, ConstClass(node_vtable)) []
        p23 = getfield_gc(p20, descr=valuedescr)
        guard_isnull(p23) []
        guard_class(p20, ConstClass(node_vtable)) []
        guard_value(p20, ConstPtr(myptr)) []

        p37 = getfield_gc(p0, descr=nextdescr)
        guard_nonnull(p37) []
        guard_class(p37, ConstClass(node_vtable)) []
        guard_class(p37, ConstClass(node_vtable)) []
        p40 = getfield_gc(p37, descr=valuedescr)
        guard_isnull(p40) []
        guard_class(p37, ConstClass(node_vtable)) []
        guard_value(p37, ConstPtr(myptr)) []

        p64 = call_may_force(p23, p40, descr=plaincalldescr)
        jump(p0)
        """
        expected = """
        [p0]
        p20 = getfield_gc(p0, descr=nextdescr)
        guard_value(p20, ConstPtr(myptr)) []
        p23 = getfield_gc(p20, descr=valuedescr)
        guard_isnull(p23) []
        p64 = call_may_force(NULL, NULL, descr=plaincalldescr)
        jump(p0)
        """
        self.optimize_loop(ops, expected, expected)

    def test_getfield_guard_const_preamble(self):
        ops = """
        [p0]
        p01 = getfield_gc(p0, descr=nextdescr)
        p02 = getfield_gc(p01, descr=valuedescr)
        guard_value(p01, ConstPtr(myptr)) []
        p11 = getfield_gc(p0, descr=nextdescr)
        p12 = getfield_gc(p11, descr=valuedescr)
        guard_value(p11, ConstPtr(myptr)) []
        p64 = call_may_force(p02, p12, descr=plaincalldescr)

        p21 = getfield_gc(p0, descr=nextdescr)
        p22 = getfield_gc(p21, descr=valuedescr)
        guard_value(p21, ConstPtr(myptr)) []
        p31 = getfield_gc(p0, descr=nextdescr)
        p32 = getfield_gc(p31, descr=valuedescr)
        guard_value(p31, ConstPtr(myptr)) []
        p65 = call_may_force(p22, p32, descr=plaincalldescr)
        jump(p0)
        """
        expected = """
        [p0]
        p01 = getfield_gc(p0, descr=nextdescr)
        p02 = getfield_gc(p01, descr=valuedescr)
        guard_value(p01, ConstPtr(myptr)) []
        p64 = call_may_force(p02, p02, descr=plaincalldescr)

        p21 = getfield_gc(p0, descr=nextdescr)
        p22 = getfield_gc(p21, descr=valuedescr)
        guard_value(p21, ConstPtr(myptr)) []
        p65 = call_may_force(p22, p22, descr=plaincalldescr)
        jump(p0)
        """
        self.optimize_loop(ops, expected, expected)

    def test_addsub_ovf(self):
        ops = """
        [i0]
        i1 = int_add_ovf(i0, 10)
        guard_no_overflow() []
        i2 = int_sub_ovf(i1, 5)
        guard_no_overflow() []
        jump(i2)
        """
        expected = """
        [i0]
        i1 = int_add_ovf(i0, 10)
        guard_no_overflow() []
        i2 = int_sub(i1, 5)
        jump(i2)
        """
        self.optimize_loop(ops, expected)

    def test_subadd_ovf(self):
        ops = """
        [i0]
        i1 = int_sub_ovf(i0, 10)
        guard_no_overflow() []
        i2 = int_add_ovf(i1, 5)
        guard_no_overflow() []
        jump(i2)
        """
        expected = """
        [i0]
        i1 = int_sub_ovf(i0, 10)
        guard_no_overflow() []
        i2 = int_add(i1, 5)
        jump(i2)
        """
        self.optimize_loop(ops, expected)

    def test_bound_and(self):
        ops = """
        []
        i0 = escape()
        i1 = int_and(i0, 255)
        i2 = int_lt(i1, 500)
        guard_true(i2) []
        i3 = int_le(i1, 255)
        guard_true(i3) []
        i4 = int_gt(i1, -1)
        guard_true(i4) []
        i5 = int_ge(i1, 0)
        guard_true(i5) []
        i6 = int_lt(i1, 0)
        guard_false(i6) []
        i7 = int_le(i1, -1)
        guard_false(i7) []
        i8 = int_gt(i1, 255)
        guard_false(i8) []
        i9 = int_ge(i1, 500)
        guard_false(i9) []
        i12 = int_lt(i1, 100)
        guard_true(i12) []
        i13 = int_le(i1, 90)
        guard_true(i13) []
        i14 = int_gt(i1, 10)
        guard_true(i14) []
        i15 = int_ge(i1, 20)
        guard_true(i15) []
        jump()
        """
        expected = """
        []
        i0 = escape()
        i1 = int_and(i0, 255)
        i12 = int_lt(i1, 100)
        guard_true(i12) []
        i13 = int_le(i1, 90)
        guard_true(i13) []
        i14 = int_gt(i1, 10)
        guard_true(i14) []
        i15 = int_ge(i1, 20)
        guard_true(i15) []
        jump()
        """
        self.optimize_loop(ops, expected)

    def test_bound_xor(self):
        ops = """
        [i0, i1, i2]
        it1 = int_ge(i1, 0)
        guard_true(it1) []
        it2 = int_gt(i2, 0)
        guard_true(it2) []
        ix1 = int_xor(i0, i0)
        ix1t = int_ge(ix1, 0)
        guard_true(ix1t) []
        ix2 = int_xor(i0, i1)
        ix2t = int_ge(ix2, 0)
        guard_true(ix2t) []
        ix3 = int_xor(i1, i0)
        ix3t = int_ge(ix3, 0)
        guard_true(ix3t) []
        ix4 = int_xor(i1, i2)
        ix4t = int_ge(ix4, 0)
        guard_true(ix4t) []
        jump(i0, i1, i2)
        """
        preamble = """
        [i0, i1, i2]
        it1 = int_ge(i1, 0)
        guard_true(it1) []
        it2 = int_gt(i2, 0)
        guard_true(it2) []
        ix2 = int_xor(i0, i1)
        ix2t = int_ge(ix2, 0)
        guard_true(ix2t) []
        ix3 = int_xor(i1, i0)
        ix3t = int_ge(ix3, 0)
        guard_true(ix3t) []
        ix4 = int_xor(i1, i2)
        jump(i0, i1, i2)
        """
        expected = """
        [i0, i1, i2]
        jump(i0, i1, i2)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_bound_floordiv(self):
        ops = """
        [i0, i1, i2]
        it1 = int_ge(i1, 0)
        guard_true(it1) []
        it2 = int_gt(i2, 0)
        guard_true(it2) []
        ix2 = int_floordiv(i0, i1)
        ix2t = int_ge(ix2, 0)
        guard_true(ix2t) []
        ix3 = int_floordiv(i1, i0)
        ix3t = int_ge(ix3, 0)
        guard_true(ix3t) []
        ix4 = int_floordiv(i1, i2)
        ix4t = int_ge(ix4, 0)
        guard_true(ix4t) []
        jump(i0, i1, i2)
        """
        preamble = """
        [i0, i1, i2]
        it1 = int_ge(i1, 0)
        guard_true(it1) []
        it2 = int_gt(i2, 0)
        guard_true(it2) []
        ix2 = int_floordiv(i0, i1)
        ix2t = int_ge(ix2, 0)
        guard_true(ix2t) []
        ix3 = int_floordiv(i1, i0)
        ix3t = int_ge(ix3, 0)
        guard_true(ix3t) []
        ix4 = int_floordiv(i1, i2)
        jump(i0, i1, i2)
        """
        expected = """
        [i0, i1, i2]
        jump(i0, i1, i2)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_bound_int_is_zero(self):
        ops = """
        [i1, i2a, i2b, i2c]
        i3 = int_is_zero(i1)
        i4 = int_gt(i2a, 7)
        guard_true(i4) []
        i5 = int_is_zero(i2a)
        guard_false(i5) []
        i6 = int_le(i2b, -7)
        guard_true(i6) []
        i7 = int_is_zero(i2b)
        guard_false(i7) []
        i8 = int_gt(i2c, -7)
        guard_true(i8) []
        i9 = int_is_zero(i2c)
        jump(i1, i2a, i2b, i2c)
        """
        preamble = """
        [i1, i2a, i2b, i2c]
        i3 = int_is_zero(i1)
        i4 = int_gt(i2a, 7)
        guard_true(i4) []
        i6 = int_le(i2b, -7)
        guard_true(i6) []
        i8 = int_gt(i2c, -7)
        guard_true(i8) []
        i9 = int_is_zero(i2c)
        jump(i1, i2a, i2b, i2c)
        """
        expected = """
        [i0, i1, i2, i3]
        jump(i0, i1, i2, i3)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_division(self):
        ops = """
        [i7, i6, i8]
        it1 = int_gt(i7, 0)
        guard_true(it1) []
        it2 = int_gt(i6, 0)
        guard_true(it2) []
        i13 = int_is_zero(i6)
        guard_false(i13) []
        i15 = int_and(i8, i6)
        i17 = int_eq(i15, -1)
        guard_false(i17) []
        i18 = int_floordiv(i7, i6)
        i19 = int_xor(i7, i6)
        i21 = int_lt(i19, 0)
        i22 = int_mod(i7, i6)
        i23 = int_is_true(i22)
        i24 = int_and(i21, i23)
        i25 = int_sub(i18, i24)
        jump(i7, i25, i8)
        """
        preamble = """
        [i7, i6, i8]
        it1 = int_gt(i7, 0)
        guard_true(it1) []
        it2 = int_gt(i6, 0)
        guard_true(it2) []
        i15 = int_and(i8, i6)
        i17 = int_eq(i15, -1)
        guard_false(i17) []
        i18 = int_floordiv(i7, i6)
        i19 = int_xor(i7, i6)
        i22 = int_mod(i7, i6)
        i23 = int_is_true(i22)
        jump(i7, i18, i8)
        """
        expected = """
        [i7, i6, i8]
        it2 = int_gt(i6, 0)
        guard_true(it2) []
        i15 = int_and(i8, i6)
        i17 = int_eq(i15, -1)
        guard_false(i17) []
        i18 = int_floordiv(i7, i6)
        i19 = int_xor(i7, i6)
        i22 = int_mod(i7, i6)
        i23 = int_is_true(i22)
        jump(i7, i18, i8)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_division_to_rshift(self):
        ops = """
        [i1, i2]
        it = int_gt(i1, 0)
        guard_true(it)[]
        i3 = int_floordiv(i1, i2)
        i4 = int_floordiv(2, i2)
        i5 = int_floordiv(i1, 2)
        i6 = int_floordiv(3, i2)
        i7 = int_floordiv(i1, 3)
        i8 = int_floordiv(4, i2)
        i9 = int_floordiv(i1, 4)
        i10 = int_floordiv(i1, 0)
        i11 = int_floordiv(i1, 1)
        i12 = int_floordiv(i2, 2)
        i13 = int_floordiv(i2, 3)
        i14 = int_floordiv(i2, 4)
        jump(i5, i14)
        """
        expected = """
        [i1, i2]
        it = int_gt(i1, 0)
        guard_true(it)[]
        i3 = int_floordiv(i1, i2)
        i4 = int_floordiv(2, i2)
        i5 = int_rshift(i1, 1)
        i6 = int_floordiv(3, i2)
        i7 = int_floordiv(i1, 3)
        i8 = int_floordiv(4, i2)
        i9 = int_rshift(i1, 2)
        i10 = int_floordiv(i1, 0)
        i12 = int_floordiv(i2, 2)
        i13 = int_floordiv(i2, 3)
        i14 = int_floordiv(i2, 4)
        jump(i5, i14)
        """
        self.optimize_loop(ops, expected)

    def test_mul_to_lshift(self):
        ops = """
        [i1, i2]
        i3 = int_mul(i1, 2)
        i4 = int_mul(2, i2)
        i5 = int_mul(i1, 32)
        i6 = int_mul(i1, i2)
        jump(i5, i6)
        """
        expected = """
        [i1, i2]
        i3 = int_lshift(i1, 1)
        i4 = int_lshift(i2, 1)
        i5 = int_lshift(i1, 5)
        i6 = int_mul(i1, i2)
        jump(i5, i6)
        """
        self.optimize_loop(ops, expected)

    def test_lshift_rshift(self):
        ops = """
        [i1, i2, i2b, i1b]
        i3 = int_lshift(i1, i2)
        i4 = int_rshift(i3, i2)
        i5 = int_lshift(i1, 2)
        i6 = int_rshift(i5, 2)
        i6t= int_eq(i6, i1)
        guard_true(i6t) []
        i7 = int_lshift(i1, 100)
        i8 = int_rshift(i7, 100)
        i9 = int_lt(i1b, 100)
        guard_true(i9) []
        i10 = int_gt(i1b, -100)
        guard_true(i10) []
        i13 = int_lshift(i1b, i2)
        i14 = int_rshift(i13, i2)
        i15 = int_lshift(i1b, 2)
        i16 = int_rshift(i15, 2)
        i17 = int_lshift(i1b, 100)
        i18 = int_rshift(i17, 100)
        i19 = int_eq(i1b, i16)
        guard_true(i19) []
        i20 = int_ne(i1b, i16)
        guard_false(i20) []
        jump(i2, i3, i1b, i2b)
        """
        expected = """
        [i1, i2, i2b, i1b]
        i3 = int_lshift(i1, i2)
        i4 = int_rshift(i3, i2)
        i5 = int_lshift(i1, 2)
        i6 = int_rshift(i5, 2)
        i6t= int_eq(i6, i1)
        guard_true(i6t) []
        i7 = int_lshift(i1, 100)
        i8 = int_rshift(i7, 100)
        i9 = int_lt(i1b, 100)
        guard_true(i9) []
        i10 = int_gt(i1b, -100)
        guard_true(i10) []
        i13 = int_lshift(i1b, i2)
        i14 = int_rshift(i13, i2)
        i15 = int_lshift(i1b, 2)
        i17 = int_lshift(i1b, 100)
        i18 = int_rshift(i17, 100)
        jump(i2, i3, i1b, i2b)
        """
        self.optimize_loop(ops, expected)

    def test_int_div_1(self):
        ops = """
        [i0]
        i1 = int_floordiv(i0, 1)
        jump(i1)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected)

    def test_division_nonneg(self):
        py.test.skip("harder")
        # this is how an app-level division turns into right now
        ops = """
        [i4]
        i1 = int_ge(i4, 0)
        guard_true(i1) []
        i16 = int_floordiv(i4, 3)
        i18 = int_mul(i16, 3)
        i19 = int_sub(i4, i18)
        i21 = int_rshift(i19, %d)
        i22 = int_add(i16, i21)
        finish(i22)
        """ % (LONG_BIT-1)
        expected = """
        [i4]
        i1 = int_ge(i4, 0)
        guard_true(i1) []
        i16 = int_floordiv(i4, 3)
        finish(i16)
        """
        self.optimize_loop(ops, expected)

    def test_division_by_2(self):
        py.test.skip("harder")
        ops = """
        [i4]
        i1 = int_ge(i4, 0)
        guard_true(i1) []
        i16 = int_floordiv(i4, 2)
        i18 = int_mul(i16, 2)
        i19 = int_sub(i4, i18)
        i21 = int_rshift(i19, %d)
        i22 = int_add(i16, i21)
        finish(i22)
        """ % (LONG_BIT-1)
        expected = """
        [i4]
        i1 = int_ge(i4, 0)
        guard_true(i1) []
        i16 = int_rshift(i4, 1)
        finish(i16)
        """
        self.optimize_loop(ops, expected)

    def test_subsub_ovf(self):
        ops = """
        [i0]
        i1 = int_sub_ovf(1, i0)
        guard_no_overflow() []
        i2 = int_gt(i1, 1)
        guard_true(i2) []
        i3 = int_sub_ovf(1, i0)
        guard_no_overflow() []
        i4 = int_gt(i3, 1)
        guard_true(i4) []
        jump(i0)
        """
        preamble = """
        [i0]
        i1 = int_sub_ovf(1, i0)
        guard_no_overflow() []
        i2 = int_gt(i1, 1)
        guard_true(i2) []
        jump(i0)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_bound_eq(self):
        ops = """
        []
        i0 = escape()
        i1 = escape()
        i2 = int_le(i0, 4)
        guard_true(i2) []
        i3 = int_eq(i0, i1)
        guard_true(i3) []
        i4 = int_lt(i1, 5)
        guard_true(i4) []
        jump()
        """
        expected = """
        []
        i0 = escape()
        i1 = escape()
        i2 = int_le(i0, 4)
        guard_true(i2) []
        i3 = int_eq(i0, i1)
        guard_true(i3) []
        jump()
        """
        self.optimize_loop(ops, expected)

    def test_bound_eq_const(self):
        ops = """
        []
        i0 = escape()
        i1 = int_eq(i0, 7)
        guard_true(i1) []
        i2 = int_add(i0, 3)
        escape(i2)
        jump()
        """
        expected = """
        []
        i0 = escape()
        i1 = int_eq(i0, 7)
        guard_true(i1) []
        escape(10)
        jump()
        """
        self.optimize_loop(ops, expected)

    def test_bound_eq_const_not(self):
        ops = """
        [i0]
        i1 = int_eq(i0, 7)
        guard_false(i1) []
        i2 = int_add(i0, 3)
        jump(i2)
        """
        expected = """
        [i0]
        i1 = int_eq(i0, 7)
        guard_false(i1) []
        i2 = int_add(i0, 3)
        jump(i2)

        """
        self.optimize_loop(ops, expected)

    def test_bound_ne_const(self):
        ops = """
        [i0]
        i1 = int_ne(i0, 7)
        guard_false(i1) []
        i2 = int_add(i0, 3)
        jump(i2)
        """
        self.raises(InvalidLoop, self.optimize_loop, ops, ops)

    def test_bound_ne_const_not(self):
        ops = """
        [i0]
        i1 = int_ne(i0, 7)
        guard_true(i1) []
        i2 = int_add(i0, 3)
        jump(i2)
        """
        expected = """
        [i0]
        i1 = int_ne(i0, 7)
        guard_true(i1) []
        i2 = int_add(i0, 3)
        jump(i2)
        """
        self.optimize_loop(ops, expected)

    def test_bound_ltne(self):
        ops = """
        [i0, i1]
        i2 = int_lt(i0, 7)
        guard_true(i2) []
        i3 = int_ne(i0, 10)
        guard_true(i2) []
        jump(i0, i1)
        """
        preamble = """
        [i0, i1]
        i2 = int_lt(i0, 7)
        guard_true(i2) []
        jump(i0, i1)
        """
        expected = """
        [i0, i1]
        jump(i0, i1)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_bound_lege_const(self):
        ops = """
        [i0]
        i1 = int_ge(i0, 7)
        guard_true(i1) []
        i2 = int_le(i0, 7)
        guard_true(i2) []
        i3 = int_add(i0, 3)
        jump(i3)
        """
        self.raises(InvalidLoop, self.optimize_loop, ops, ops)

    def test_bound_lshift(self):
        ops = """
        [i0, i1, i1b, i2, i3]
        i4 = int_lt(i1, 7)
        guard_true(i4) []
        i4b = int_lt(i1b, 7)
        guard_true(i4b) []
        i4c = int_ge(i1b, 0)
        guard_true(i4c) []
        i5 = int_lt(i3, 2)
        guard_true(i5) []
        i6 = int_ge(i3, 0)
        guard_true(i6) []
        i7 = int_lshift(i1, i3)
        i8 = int_le(i7, 14)
        guard_true(i8) []
        i8b = int_lshift(i1, i2)
        i9 = int_le(i8b, 14)
        guard_true(i9) []
        i10 = int_lshift(i0, i3)
        i11 = int_le(i10, 14)
        guard_true(i11) []
        i12 = int_lt(i0, 15)
        guard_true(i12) []
        i13 = int_lshift(i1b, i3)
        i14 = int_le(i13, 14)
        guard_true(i14) []
        i15 = int_lshift(i1b, i2)
        i16 = int_le(i15, 14)
        guard_true(i16) []
        jump(i0, i1, i1b, i2, i3)
        """
        preamble = """
        [i0, i1, i1b, i2, i3]
        i4 = int_lt(i1, 7)
        guard_true(i4) []
        i4b = int_lt(i1b, 7)
        guard_true(i4b) []
        i4c = int_ge(i1b, 0)
        guard_true(i4c) []
        i5 = int_lt(i3, 2)
        guard_true(i5) []
        i6 = int_ge(i3, 0)
        guard_true(i6) []
        i7 = int_lshift(i1, i3)
        i8 = int_le(i7, 14)
        guard_true(i8) []
        i8b = int_lshift(i1, i2)
        i9 = int_le(i8b, 14)
        guard_true(i9) []
        i10 = int_lshift(i0, i3)
        i11 = int_le(i10, 14)
        guard_true(i11) []
        i13 = int_lshift(i1b, i3)
        i15 = int_lshift(i1b, i2)
        i16 = int_le(i15, 14)
        guard_true(i16) []
        jump(i0, i1, i1b, i2, i3)
        """
        expected = """
        [i0, i1, i1b, i2, i3]
        jump(i0, i1, i1b, i2, i3)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_bound_rshift(self):
        ops = """
        [i0, i1, i1b, i2, i3]
        i4 = int_lt(i1, 7)
        guard_true(i4) []
        i4b = int_lt(i1b, 7)
        guard_true(i4b) []
        i4c = int_ge(i1b, 0)
        guard_true(i4c) []
        i5 = int_lt(i3, 2)
        guard_true(i5) []
        i6 = int_ge(i3, 0)
        guard_true(i6) []
        i7 = int_rshift(i1, i3)
        i8 = int_le(i7, 14)
        guard_true(i8) []
        i8b = int_rshift(i1, i2)
        i9 = int_le(i8b, 14)
        guard_true(i9) []
        i10 = int_rshift(i0, i3)
        i11 = int_le(i10, 14)
        guard_true(i11) []
        i12 = int_lt(i0, 25)
        guard_true(i12) []
        i13 = int_rshift(i1b, i3)
        i14 = int_le(i13, 14)
        guard_true(i14) []
        i15 = int_rshift(i1b, i2)
        i16 = int_le(i15, 14)
        guard_true(i16) []
        jump(i0, i1, i1b, i2, i3)
        """
        preamble = """
        [i0, i1, i1b, i2, i3]
        i4 = int_lt(i1, 7)
        guard_true(i4) []
        i4b = int_lt(i1b, 7)
        guard_true(i4b) []
        i4c = int_ge(i1b, 0)
        guard_true(i4c) []
        i5 = int_lt(i3, 2)
        guard_true(i5) []
        i6 = int_ge(i3, 0)
        guard_true(i6) []
        i7 = int_rshift(i1, i3)
        i8b = int_rshift(i1, i2)
        i9 = int_le(i8b, 14)
        guard_true(i9) []
        i10 = int_rshift(i0, i3)
        i11 = int_le(i10, 14)
        guard_true(i11) []
        i12 = int_lt(i0, 25)
        guard_true(i12) []
        i13 = int_rshift(i1b, i3)
        i15 = int_rshift(i1b, i2)
        i16 = int_le(i15, 14)
        guard_true(i16) []
        jump(i0, i1, i1b, i2, i3)
        """
        expected = """
        [i0, i1, i1b, i2, i3]
        jump(i0, i1, i1b, i2, i3)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_bound_dont_backpropagate_rshift(self):
        ops = """
        [i0]
        i3 = int_rshift(i0, 1)
        i5 = int_eq(i3, 1)
        guard_true(i5) []
        i11 = int_add(i0, 1)
        jump(i11)
        """
        self.optimize_loop(ops, ops, ops)

    def test_bound_backpropagate_int_signext(self):
        ops = """
        []
        i0 = escape()
        i1 = int_signext(i0, 1)
        i2 = int_eq(i0, i1)
        guard_true(i2) []
        i3 = int_le(i0, 127)    # implied by equality with int_signext
        guard_true(i3) []
        i5 = int_gt(i0, -129)   # implied by equality with int_signext
        guard_true(i5) []
        jump()
        """
        expected = """
        []
        i0 = escape()
        i1 = int_signext(i0, 1)
        i2 = int_eq(i0, i1)
        guard_true(i2) []
        jump()
        """
        self.optimize_loop(ops, expected)

    def test_bound_backpropagate_int_signext_2(self):
        ops = """
        []
        i0 = escape()
        i1 = int_signext(i0, 1)
        i2 = int_eq(i0, i1)
        guard_true(i2) []
        i3 = int_le(i0, 126)    # false for i1 == 127
        guard_true(i3) []
        i5 = int_gt(i0, -128)   # false for i1 == -128
        guard_true(i5) []
        jump()
        """
        self.optimize_loop(ops, ops)

    def test_mul_ovf(self):
        ops = """
        [i0, i1]
        i2 = int_and(i0, 255)
        i3 = int_lt(i1, 5)
        guard_true(i3) []
        i4 = int_gt(i1, -10)
        guard_true(i4) []
        i5 = int_mul_ovf(i2, i1)
        guard_no_overflow() []
        i6 = int_lt(i5, -2550)
        guard_false(i6) []
        i7 = int_ge(i5, 1276)
        guard_false(i7) []
        i8 = int_gt(i5, 126)
        guard_true(i8) []
        jump(i0, i1)
        """
        preamble = """
        [i0, i1]
        i2 = int_and(i0, 255)
        i3 = int_lt(i1, 5)
        guard_true(i3) []
        i4 = int_gt(i1, -10)
        guard_true(i4) []
        i5 = int_mul(i2, i1)
        i8 = int_gt(i5, 126)
        guard_true(i8) []
        jump(i0, i1)
        """
        expected = """
        [i0, i1]
        jump(i0, i1)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_mul_ovf_before(self):
        ops = """
        [i0, i1]
        i2 = int_and(i0, 255)
        i22 = int_add(i2, 1)
        i3 = int_mul_ovf(i22, i1)
        guard_no_overflow() []
        i4 = int_lt(i3, 10)
        guard_true(i4) []
        i5 = int_gt(i3, 2)
        guard_true(i5) []
        i6 = int_lt(i1, 0)
        guard_false(i6) []
        jump(i0, i1)
        """
        preamble = """
        [i0, i1]
        i2 = int_and(i0, 255)
        i22 = int_add(i2, 1)
        i3 = int_mul_ovf(i22, i1)
        guard_no_overflow() []
        i4 = int_lt(i3, 10)
        guard_true(i4) []
        i5 = int_gt(i3, 2)
        guard_true(i5) []
        jump(i0, i1)
        """
        expected = """
        [i0, i1]
        jump(i0, i1)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_sub_ovf_before(self):
        ops = """
        [i0, i1]
        i2 = int_and(i0, 255)
        i3 = int_sub_ovf(i2, i1)
        guard_no_overflow() []
        i4 = int_le(i3, 10)
        guard_true(i4) []
        i5 = int_ge(i3, 2)
        guard_true(i5) []
        i6 = int_lt(i1, -10)
        guard_false(i6) []
        i7 = int_gt(i1, 253)
        guard_false(i7) []
        jump(i0, i1)
        """
        preamble = """
        [i0, i1]
        i2 = int_and(i0, 255)
        i3 = int_sub_ovf(i2, i1)
        guard_no_overflow() []
        i4 = int_le(i3, 10)
        guard_true(i4) []
        i5 = int_ge(i3, 2)
        guard_true(i5) []
        jump(i0, i1)
        """
        expected = """
        [i0, i1]
        jump(i0, i1)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_invariant_ovf(self):
        ops = """
        [i0, i1, i10, i11, i20, i21]
        i2 = int_add_ovf(i0, i1)
        guard_no_overflow() []
        i3 = int_sub_ovf(i0, i1)
        guard_no_overflow() []
        i4 = int_mul_ovf(i0, i1)
        guard_no_overflow() []
        escape(i2)
        escape(i3)
        escape(i4)
        i24 = int_mul_ovf(i10, i11)
        guard_no_overflow() []
        i23 = int_sub_ovf(i10, i11)
        guard_no_overflow() []
        i22 = int_add_ovf(i10, i11)
        guard_no_overflow() []
        jump(i0, i1, i20, i21, i20, i21)
        """
        expected = """
        [i0, i1, i10, i11, i2, i3, i4]
        escape(i2)
        escape(i3)
        escape(i4)
        i24 = int_mul_ovf(i10, i11)
        guard_no_overflow() []
        i23 = int_sub_ovf(i10, i11)
        guard_no_overflow() []
        i22 = int_add_ovf(i10, i11)
        guard_no_overflow() []
        jump(i0, i1, i10, i11, i2, i3, i4)
        """
        self.optimize_loop(ops, expected)

    def test_value_proven_to_be_constant_after_two_iterations(self):
        class FakeDescr(AbstractDescr):
            def __init__(self, name):
                self.name = name
            def sort_key(self):
                return id(self)
            def is_integer_bounded(self):
                return False

        for n in ('inst_w_seq', 'inst_index', 'inst_w_list', 'inst_length',
                  'inst_start', 'inst_step'):
            self.namespace[n] = FakeDescr(n)
        ops = """
        [p0, p1, p2, p3, i4, p5, i6, p7, p8, p9, p14]
        guard_value(i4, 3) []
        guard_class(p9, ConstClass(node_vtable)) []
        guard_class(p9, ConstClass(node_vtable)) []
        p22 = getfield_gc(p9, descr=inst_w_seq)
        guard_nonnull(p22) []
        i23 = getfield_gc(p9, descr=inst_index)
        p24 = getfield_gc(p22, descr=inst_w_list)
        guard_isnull(p24) []
        i25 = getfield_gc(p22, descr=inst_length)
        i26 = int_ge(i23, i25)
        guard_true(i26) []
        setfield_gc(p9, ConstPtr(myptr), descr=inst_w_seq)

        guard_nonnull(p14)  []
        guard_class(p14, 17273920) []
        guard_class(p14, 17273920) []

        p75 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p75, p14, descr=inst_w_seq)
        setfield_gc(p75, 0, descr=inst_index)
        guard_class(p75, ConstClass(node_vtable)) []
        guard_class(p75, ConstClass(node_vtable)) []
        p79 = getfield_gc(p75, descr=inst_w_seq)
        guard_nonnull(p79) []
        i80 = getfield_gc(p75, descr=inst_index)
        p81 = getfield_gc(p79, descr=inst_w_list)
        guard_isnull(p81) []
        i82 = getfield_gc(p79, descr=inst_length)
        i83 = int_ge(i80, i82)
        guard_false(i83) []
        i84 = getfield_gc(p79, descr=inst_start)
        i85 = getfield_gc(p79, descr=inst_step)
        i86 = int_mul(i80, i85)
        i87 = int_add(i84, i86)
        i91 = int_add(i80, 1)
        setfield_gc(p75, i91, descr=inst_index)

        p110 = same_as(ConstPtr(myptr))
        i112 = same_as(3)
        i114 = same_as(39)
        jump(p0, p1, p110, p3, i112, p5, i114, p7, p8, p75, p14)
        """
        expected = """
        [p0, p1, p3, p5, p7, p8, p14, i82]
        i115 = int_ge(1, i82)
        guard_true(i115) []
        jump(p0, p1, p3, p5, p7, p8, p14, 1)
        """
        self.optimize_loop(ops, expected)

    def test_let_getfield_kill_setfields(self):
        ops = """
        [p0]
        p1 = getfield_gc(p0, descr=valuedescr)
        setfield_gc(p0, p1, descr=valuedescr)
        setfield_gc(p0, p1, descr=valuedescr)
        setfield_gc(p0, p0, descr=valuedescr)
        jump(p0)
        """
        preamble = """
        [p0]
        p1 = getfield_gc(p0, descr=valuedescr)
        setfield_gc(p0, p0, descr=valuedescr)
        p4450 = same_as(p0) # Should be killed by backend
        jump(p0)
        """
        expected = """
        [p0]
        setfield_gc(p0, p0, descr=valuedescr)
        jump(p0)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_let_getfield_kill_chained_setfields(self):
        ops = """
        [p0]
        p1 = getfield_gc(p0, descr=valuedescr)
        setfield_gc(p0, p0, descr=valuedescr)
        setfield_gc(p0, p1, descr=valuedescr)
        setfield_gc(p0, p1, descr=valuedescr)
        jump(p0)
        """
        preamble = """
        [p0]
        p1 = getfield_gc(p0, descr=valuedescr)
        jump(p0)
        """
        expected = """
        [p0]
        jump(p0)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_inputargs_added_by_forcing_jumpargs(self):
        # FXIME: Can this occur?
        ops = """
        [p0, p1, pinv]
        i1 = getfield_gc(pinv, descr=valuedescr)
        p2 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p2, i1, descr=nextdescr)
        """
        py.test.skip("no test here")

    def test_immutable_not(self):
        ops = """
        []
        p0 = new_with_vtable(ConstClass(intobj_noimmut_vtable))
        setfield_gc(p0, 42, descr=noimmut_intval)
        escape(p0)
        jump()
        """
        self.optimize_loop(ops, ops)

    def test_immutable_variable(self):
        ops = """
        [i0]
        p0 = new_with_vtable(ConstClass(intobj_immut_vtable))
        setfield_gc(p0, i0, descr=immut_intval)
        escape(p0)
        jump(i0)
        """
        self.optimize_loop(ops, ops)

    def test_immutable_incomplete(self):
        ops = """
        []
        p0 = new_with_vtable(ConstClass(intobj_immut_vtable))
        escape(p0)
        jump()
        """
        self.optimize_loop(ops, ops)

    def test_immutable_constantfold(self):
        ops = """
        []
        p0 = new_with_vtable(ConstClass(intobj_immut_vtable))
        setfield_gc(p0, 1242, descr=immut_intval)
        escape(p0)
        jump()
        """
        from rpython.rtyper.lltypesystem import lltype, llmemory
        class IntObj1242(object):
            _TYPE = llmemory.GCREF.TO
            def __eq__(self, other):
                return other.container.intval == 1242
        self.namespace['intobj1242'] = lltype._ptr(llmemory.GCREF,
                                                   IntObj1242())
        expected = """
        []
        escape(ConstPtr(intobj1242))
        jump()
        """
        self.optimize_loop(ops, expected)
        # ----------
        ops = """
        [p1]
        p0 = new_with_vtable(ConstClass(ptrobj_immut_vtable))
        setfield_gc(p0, p1, descr=immut_ptrval)
        escape(p0)
        jump(p1)
        """
        self.optimize_loop(ops, ops)
        # ----------
        ops = """
        []
        p0 = new_with_vtable(ConstClass(ptrobj_immut_vtable))
        p1 = new_with_vtable(ConstClass(intobj_immut_vtable))
        setfield_gc(p1, 1242, descr=immut_intval)
        setfield_gc(p0, p1, descr=immut_ptrval)
        escape(p0)
        jump()
        """
        class PtrObj1242(object):
            _TYPE = llmemory.GCREF.TO
            def __eq__(slf, other):
                if slf is other:
                    return 1
                p1 = other.container.ptrval
                p1cast = lltype.cast_pointer(lltype.Ptr(self.INTOBJ_IMMUT), p1)
                return p1cast.intval == 1242
        self.namespace['ptrobj1242'] = lltype._ptr(llmemory.GCREF,
                                                   PtrObj1242())
        expected = """
        []
        escape(ConstPtr(ptrobj1242))
        jump()
        """
        self.optimize_loop(ops, expected)

    def test_bug_unroll_with_immutables(self):
        ops = """
        [p0]
        i2 = getfield_gc_pure(p0, descr=immut_intval)
        p1 = new_with_vtable(ConstClass(intobj_immut_vtable))
        setfield_gc(p1, 1242, descr=immut_intval)
        jump(p1)
        """
        preamble = """
        [p0]
        i2 = getfield_gc_pure(p0, descr=immut_intval)
        jump()
        """
        expected = """
        []
        jump()
        """
        self.optimize_loop(ops, expected, preamble)

    def test_immutable_constantfold_recursive(self):
        ops = """
        []
        p0 = new_with_vtable(ConstClass(ptrobj_immut_vtable))
        setfield_gc(p0, p0, descr=immut_ptrval)
        escape(p0)
        jump()
        """
        from rpython.rtyper.lltypesystem import lltype, llmemory
        class PtrObjSelf(object):
            _TYPE = llmemory.GCREF.TO
            def __eq__(slf, other):
                if slf is other:
                    return 1
                p1 = other.container.ptrval
                p1cast = lltype.cast_pointer(lltype.Ptr(self.PTROBJ_IMMUT), p1)
                return p1cast.ptrval == p1
        self.namespace['ptrobjself'] = lltype._ptr(llmemory.GCREF,
                                                   PtrObjSelf())
        expected = """
        []
        escape(ConstPtr(ptrobjself))
        jump()
        """
        self.optimize_loop(ops, expected)
        #
        ops = """
        []
        p0 = new_with_vtable(ConstClass(ptrobj_immut_vtable))
        p1 = new_with_vtable(ConstClass(ptrobj_immut_vtable))
        setfield_gc(p0, p1, descr=immut_ptrval)
        setfield_gc(p1, p0, descr=immut_ptrval)
        escape(p0)
        jump()
        """
        class PtrObjSelf2(object):
            _TYPE = llmemory.GCREF.TO
            def __eq__(slf, other):
                if slf is other:
                    return 1
                p1 = other.container.ptrval
                p1cast = lltype.cast_pointer(lltype.Ptr(self.PTROBJ_IMMUT), p1)
                p2 = p1cast.ptrval
                assert p2 != p1
                p2cast = lltype.cast_pointer(lltype.Ptr(self.PTROBJ_IMMUT), p2)
                return p2cast.ptrval == p1
        self.namespace['ptrobjself2'] = lltype._ptr(llmemory.GCREF,
                                                    PtrObjSelf2())
        expected = """
        []
        escape(ConstPtr(ptrobjself2))
        jump()
        """
        self.optimize_loop(ops, expected)

    # ----------
    def optimize_strunicode_loop(self, ops, optops, preamble):
        # check with the arguments passed in
        self.optimize_loop(ops, optops, preamble)
        # check with replacing 'str' with 'unicode' everywhere
        def r(s):
            return s.replace('str', 'unicode').replace('s"', 'u"')
        self.optimize_loop(r(ops), r(optops), r(preamble))

    def test_newstr_1(self):
        ops = """
        [i0]
        p1 = newstr(1)
        strsetitem(p1, 0, i0)
        i1 = strgetitem(p1, 0)
        jump(i1)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_strunicode_loop(ops, expected, expected)

    def test_newstr_2(self):
        ops = """
        [i0, i1]
        p1 = newstr(2)
        strsetitem(p1, 0, i0)
        strsetitem(p1, 1, i1)
        i2 = strgetitem(p1, 1)
        i3 = strgetitem(p1, 0)
        jump(i2, i3)
        """
        expected = """
        [i0, i1]
        jump(i1, i0)
        """
        self.optimize_strunicode_loop(ops, expected, expected)

    def test_newstr_toobig(self):
        ops = """
        [i0]
        p1 = newstr(101)
        strsetitem(p1, 0, i0)
        i3 = strgetitem(p1, 0)
        jump(i3)
        """
        self.optimize_strunicode_loop(ops, ops, ops)

    # XXX Should some of the call's below now be call_pure?

    def test_str_concat_1(self):
        ops = """
        [p1, p2]
        p3 = call(0, p1, p2, descr=strconcatdescr)
        jump(p2, p3)
        """
        preamble = """
        [p1, p2]
        i1 = strlen(p1)
        i2 = strlen(p2)
        i3 = int_add(i1, i2)
        p3 = newstr(i3)
        copystrcontent(p1, p3, 0, 0, i1)
        copystrcontent(p2, p3, 0, i1, i2)
        jump(p2, p3, i2)
        """
        expected = """
        [p1, p2, i1]
        i2 = strlen(p2)
        i3 = int_add(i1, i2)
        p3 = newstr(i3)
        copystrcontent(p1, p3, 0, 0, i1)
        copystrcontent(p2, p3, 0, i1, i2)
        jump(p2, p3, i2)
        """
        self.optimize_strunicode_loop(ops, expected, preamble)

    def test_str_concat_vstr2_str(self):
        ops = """
        [i0, i1, p2]
        p1 = newstr(2)
        strsetitem(p1, 0, i0)
        strsetitem(p1, 1, i1)
        p3 = call(0, p1, p2, descr=strconcatdescr)
        jump(i1, i0, p3)
        """
        expected = """
        [i0, i1, p2]
        i2 = strlen(p2)
        i3 = int_add(2, i2)
        p3 = newstr(i3)
        strsetitem(p3, 0, i0)
        strsetitem(p3, 1, i1)
        copystrcontent(p2, p3, 0, 2, i2)
        jump(i1, i0, p3)
        """
        self.optimize_strunicode_loop(ops, expected, expected)

    def test_str_concat_str_vstr2(self):
        ops = """
        [i0, i1, p2]
        p1 = newstr(2)
        strsetitem(p1, 0, i0)
        strsetitem(p1, 1, i1)
        p3 = call(0, p2, p1, descr=strconcatdescr)
        jump(i1, i0, p3)
        """
        expected = """
        [i0, i1, p2]
        i2 = strlen(p2)
        i3 = int_add(i2, 2)
        p3 = newstr(i3)
        copystrcontent(p2, p3, 0, 0, i2)
        strsetitem(p3, i2, i0)
        i5 = int_add(i2, 1)
        strsetitem(p3, i5, i1)
        i6 = int_add(i5, 1)      # will be killed by the backend
        jump(i1, i0, p3)
        """
        self.optimize_strunicode_loop(ops, expected, expected)

    def test_str_concat_str_str_str(self):
        ops = """
        [p1, p2, p3]
        p4 = call(0, p1, p2, descr=strconcatdescr)
        p5 = call(0, p4, p3, descr=strconcatdescr)
        jump(p2, p3, p5)
        """
        preamble = """
        [p1, p2, p3]
        i1 = strlen(p1)
        i2 = strlen(p2)
        i12 = int_add(i1, i2)
        i3 = strlen(p3)
        i123 = int_add(i12, i3)
        p5 = newstr(i123)
        copystrcontent(p1, p5, 0, 0, i1)
        copystrcontent(p2, p5, 0, i1, i2)
        copystrcontent(p3, p5, 0, i12, i3)
        jump(p2, p3, p5, i2, i3)
        """
        expected = """
        [p1, p2, p3, i1, i2]
        i12 = int_add(i1, i2)
        i3 = strlen(p3)
        i123 = int_add(i12, i3)
        p5 = newstr(i123)
        copystrcontent(p1, p5, 0, 0, i1)
        copystrcontent(p2, p5, 0, i1, i2)
        copystrcontent(p3, p5, 0, i12, i3)
        jump(p2, p3, p5, i2, i3)
        """
        self.optimize_strunicode_loop(ops, expected, preamble)

    def test_str_concat_str_cstr1(self):
        ops = """
        [p2]
        p3 = call(0, p2, s"x", descr=strconcatdescr)
        jump(p3)
        """
        expected = """
        [p2]
        i2 = strlen(p2)
        i3 = int_add(i2, 1)
        p3 = newstr(i3)
        copystrcontent(p2, p3, 0, 0, i2)
        strsetitem(p3, i2, 120)     # == ord('x')
        jump(p3)
        """
        self.optimize_strunicode_loop(ops, expected, expected)

    def test_str_concat_consts(self):
        ops = """
        []
        p1 = same_as(s"ab")
        p2 = same_as(s"cde")
        p3 = call(0, p1, p2, descr=strconcatdescr)
        escape(p3)
        jump()
        """
        expected = """
        []
        escape(s"abcde")
        jump()
        """
        self.optimize_strunicode_loop(ops, expected, expected)

    def test_str_slice_len_surviving1(self):
        ops = """
        [p1, i1, i2, i3]
        escape(i3)
        p2 = call(0, p1, i1, i2, descr=strslicedescr)
        i4 = strlen(p2)
        jump(p1, i1, i2, i4)
        """
        preamble = """
        [p1, i1, i2, i3]
        escape(i3)
        i4 = int_sub(i2, i1)
        i5 = same_as(i4)
        jump(p1, i1, i2, i4, i5)
        """
        expected = """
        [p1, i1, i2, i3, i4]
        escape(i3)
        jump(p1, i1, i2, i4, i4)
        """
        self.optimize_strunicode_loop(ops, expected, preamble)

    def test_str_slice_len_surviving2(self):
        ops = """
        [p1, i1, i2, p2]
        i5 = getfield_gc(p2, descr=valuedescr)
        escape(i5)
        p3 = call(0, p1, i1, i2, descr=strslicedescr)
        i4 = strlen(p3)
        setfield_gc(p2, i4, descr=valuedescr)
        jump(p1, i1, i2, p2)
        """
        preamble = """
        [p1, i1, i2, p2]
        i5 = getfield_gc(p2, descr=valuedescr)
        escape(i5)
        i4 = int_sub(i2, i1)
        setfield_gc(p2, i4, descr=valuedescr)
        i8 = same_as(i4)
        jump(p1, i1, i2, p2, i8, i4)
        """
        expected = """
        [p1, i1, i2, p2, i5, i6]
        escape(i5)
        setfield_gc(p2, i6, descr=valuedescr)
        jump(p1, i1, i2, p2, i6, i6)
        """
        self.optimize_strunicode_loop(ops, expected, preamble)

    def test_str_slice_1(self):
        ops = """
        [p1, i1, i2]
        p2 = call(0, p1, i1, i2, descr=strslicedescr)
        jump(p2, i1, i2)
        """
        preamble = """
        [p1, i1, i2]
        i3 = int_sub(i2, i1)
        p2 = newstr(i3)
        copystrcontent(p1, p2, i1, 0, i3)
        jump(p2, i1, i2, i3)
        """
        expected = """
        [p1, i1, i2, i3]
        p2 = newstr(i3)
        copystrcontent(p1, p2, i1, 0, i3)
        jump(p2, i1, i2, i3)
        """
        self.optimize_strunicode_loop(ops, expected, preamble)

    def test_str_slice_2(self):
        ops = """
        [p1, i2]
        p2 = call(0, p1, 0, i2, descr=strslicedescr)
        jump(p2, i2)
        """
        expected = """
        [p1, i2]
        p2 = newstr(i2)
        copystrcontent(p1, p2, 0, 0, i2)
        jump(p2, i2)
        """
        self.optimize_strunicode_loop(ops, expected, expected)

    def test_str_slice_3(self):
        ops = """
        [p1, i1, i2, i3, i4]
        p2 = call(0, p1, i1, i2, descr=strslicedescr)
        p3 = call(0, p2, i3, i4, descr=strslicedescr)
        jump(p3, i1, i2, i3, i4)
        """
        preamble = """
        [p1, i1, i2, i3, i4]
        i0 = int_sub(i2, i1)     # killed by the backend
        i5 = int_sub(i4, i3)
        i6 = int_add(i1, i3)
        p3 = newstr(i5)
        copystrcontent(p1, p3, i6, 0, i5)
        jump(p3, i1, i2, i3, i4, i5, i6)
        """
        expected = """
        [p1, i1, i2, i3, i4, i5, i6]
        p3 = newstr(i5)
        copystrcontent(p1, p3, i6, 0, i5)
        jump(p3, i1, i2, i3, i4, i5, i6)
        """
        self.optimize_strunicode_loop(ops, expected, preamble)

    def test_str_slice_getitem1(self):
        ops = """
        [p1, i1, i2, i3]
        p2 = call(0, p1, i1, i2, descr=strslicedescr)
        i4 = strgetitem(p2, i3)
        escape(i4)
        jump(p1, i1, i2, i3)
        """
        preamble = """
        [p1, i1, i2, i3]
        i6 = int_sub(i2, i1)      # killed by the backend
        i5 = int_add(i1, i3)
        i4 = strgetitem(p1, i5)
        escape(i4)
        jump(p1, i1, i2, i3, i4)
        """
        expected = """
        [p1, i1, i2, i3, i4]
        escape(i4)
        jump(p1, i1, i2, i3, i4)
        """
        self.optimize_strunicode_loop(ops, expected, preamble)

    def test_str_slice_plain(self):
        ops = """
        [i3, i4]
        p1 = newstr(2)
        strsetitem(p1, 0, i3)
        strsetitem(p1, 1, i4)
        p2 = call(0, p1, 1, 2, descr=strslicedescr)
        i5 = strgetitem(p2, 0)
        escape(i5)
        jump(i3, i4)
        """
        expected = """
        [i3, i4]
        escape(i4)
        jump(i3, i4)
        """
        self.optimize_strunicode_loop(ops, expected, expected)

    def test_str_slice_concat(self):
        ops = """
        [p1, i1, i2, p2]
        p3 = call(0, p1, i1, i2, descr=strslicedescr)
        p4 = call(0, p3, p2, descr=strconcatdescr)
        jump(p4, i1, i2, p2)
        """
        preamble = """
        [p1, i1, i2, p2]
        i3 = int_sub(i2, i1)     # length of p3
        i4 = strlen(p2)
        i5 = int_add(i3, i4)
        p4 = newstr(i5)
        copystrcontent(p1, p4, i1, 0, i3)
        copystrcontent(p2, p4, 0, i3, i4)
        jump(p4, i1, i2, p2, i5, i3, i4)
        """
        expected = """
        [p1, i1, i2, p2, i5, i3, i4]
        p4 = newstr(i5)
        copystrcontent(p1, p4, i1, 0, i3)
        copystrcontent(p2, p4, 0, i3, i4)
        jump(p4, i1, i2, p2, i5, i3, i4)
        """
        self.optimize_strunicode_loop(ops, expected, preamble)

    def test_strgetitem_bounds(self):
        ops = """
        [p0, i0]
        i1 = strgetitem(p0, i0)
        i10 = strgetitem(p0, i0)
        i2 = int_lt(i1, 256)
        guard_true(i2) []
        i3 = int_ge(i1, 0)
        guard_true(i3) []
        jump(p0, i0)
        """
        expected = """
        [p0, i0]
        jump(p0, i0)
        """
        self.optimize_loop(ops, expected)

    def test_unicodegetitem_bounds(self):
        ops = """
        [p0, i0]
        i1 = unicodegetitem(p0, i0)
        i10 = unicodegetitem(p0, i0)
        i2 = int_lt(i1, 0)
        guard_false(i2) []
        jump(p0, i0)
        """
        expected = """
        [p0, i0]
        jump(p0, i0)
        """
        self.optimize_loop(ops, expected)

    def test_strlen_positive(self):
        ops = """
        [p0]
        i0 = strlen(p0)
        i1 = int_ge(i0, 0)
        guard_true(i1) []
        i2 = int_gt(i0, -1)
        guard_true(i2) []
        jump(p0)
        """
        expected = """
        [p0]
        jump(p0)
        """
        self.optimize_loop(ops, expected)

    def test_strlen_repeated(self):
        ops = """
        [p0]
        i0 = strlen(p0)
        i1 = strlen(p0)
        i2 = int_eq(i0, i1)
        guard_true(i2) []
        jump(p0)
        """
        expected = """
        [p0]
        jump(p0)
        """
        self.optimize_loop(ops, expected)

    # ----------
    def optimize_strunicode_loop_extradescrs(self, ops, optops, preamble):
        class FakeCallInfoCollection:
            def callinfo_for_oopspec(self, oopspecindex):
                calldescrtype = type(LLtypeMixin.strequaldescr)
                effectinfotype = type(LLtypeMixin.strequaldescr.get_extra_info())
                for value in LLtypeMixin.__dict__.values():
                    if isinstance(value, calldescrtype):
                        extra = value.get_extra_info()
                        if (extra and isinstance(extra, effectinfotype) and
                                extra.oopspecindex == oopspecindex):
                            # returns 0 for 'func' in this test
                            return value, 0
                raise AssertionError("not found: oopspecindex=%d" %
                                     oopspecindex)
        #
        self.callinfocollection = FakeCallInfoCollection()
        self.optimize_strunicode_loop(ops, optops, preamble)

    def test_str_equal_noop1(self):
        ops = """
        [p1, p2]
        i0 = call(0, p1, p2, descr=strequaldescr)
        escape(i0)
        jump(p1, p2)
        """
        self.optimize_strunicode_loop_extradescrs(ops, ops, ops)

    def test_str_equal_noop2(self):
        ops = """
        [p1, p2, p3]
        p4 = call(0, p1, p2, descr=strconcatdescr)
        i0 = call(0, p3, p4, descr=strequaldescr)
        escape(i0)
        jump(p1, p2, p3)
        """
        preamble = """
        [p1, p2, p3]
        i1 = strlen(p1)
        i2 = strlen(p2)
        i3 = int_add(i1, i2)
        p4 = newstr(i3)
        copystrcontent(p1, p4, 0, 0, i1)
        copystrcontent(p2, p4, 0, i1, i2)
        i0 = call(0, p3, p4, descr=strequaldescr)
        escape(i0)
        jump(p1, p2, p3, i3, i1, i2)
        """
        expected = """
        [p1, p2, p3, i3, i1, i2]
        p4 = newstr(i3)
        copystrcontent(p1, p4, 0, 0, i1)
        copystrcontent(p2, p4, 0, i1, i2)
        i0 = call(0, p3, p4, descr=strequaldescr)
        escape(i0)
        jump(p1, p2, p3, i3, i1, i2)
        """
        self.optimize_strunicode_loop_extradescrs(ops, expected,
                                                  preamble)

    def test_str_equal_slice1(self):
        ops = """
        [p1, i1, i2, p3]
        p4 = call(0, p1, i1, i2, descr=strslicedescr)
        i0 = call(0, p4, p3, descr=strequaldescr)
        escape(i0)
        jump(p1, i1, i2, p3)
        """
        preamble = """
        [p1, i1, i2, p3]
        i3 = int_sub(i2, i1)
        i0 = call(0, p1, i1, i3, p3, descr=streq_slice_checknull_descr)
        escape(i0)
        jump(p1, i1, i2, p3, i3)
        """
        expected = """
        [p1, i1, i2, p3, i3]
        i0 = call(0, p1, i1, i3, p3, descr=streq_slice_checknull_descr)
        escape(i0)
        jump(p1, i1, i2, p3, i3)
        """
        self.optimize_strunicode_loop_extradescrs(ops, expected,
                                                  preamble)

    def test_str_equal_slice2(self):
        ops = """
        [p1, i1, i2, p3]
        p4 = call(0, p1, i1, i2, descr=strslicedescr)
        i0 = call(0, p3, p4, descr=strequaldescr)
        escape(i0)
        jump(p1, i1, i2, p3)
        """
        preamble = """
        [p1, i1, i2, p3]
        i4 = int_sub(i2, i1)
        i0 = call(0, p1, i1, i4, p3, descr=streq_slice_checknull_descr)
        escape(i0)
        jump(p1, i1, i2, p3, i4)
        """
        expected = """
        [p1, i1, i2, p3, i4]
        i0 = call(0, p1, i1, i4, p3, descr=streq_slice_checknull_descr)
        escape(i0)
        jump(p1, i1, i2, p3, i4)
        """
        self.optimize_strunicode_loop_extradescrs(ops, expected,
                                                  preamble)

    def test_str_equal_slice3(self):
        ops = """
        [p1, i1, i2, p3]
        guard_nonnull(p3) []
        p4 = call(0, p1, i1, i2, descr=strslicedescr)
        i0 = call(0, p3, p4, descr=strequaldescr)
        escape(i0)
        jump(p1, i1, i2, p3)
        """
        expected = """
        [p1, i1, i2, p3, i4]
        i0 = call(0, p1, i1, i4, p3, descr=streq_slice_nonnull_descr)
        escape(i0)
        jump(p1, i1, i2, p3, i4)
        """
        preamble = """
        [p1, i1, i2, p3]
        guard_nonnull(p3) []
        i4 = int_sub(i2, i1)
        i0 = call(0, p1, i1, i4, p3, descr=streq_slice_nonnull_descr)
        escape(i0)
        jump(p1, i1, i2, p3, i4)
        """
        self.optimize_strunicode_loop_extradescrs(ops,
                                                  expected, preamble)

    def test_str_equal_slice4(self):
        ops = """
        [p1, i1, i2]
        p3 = call(0, p1, i1, i2, descr=strslicedescr)
        i0 = call(0, p3, s"x", descr=strequaldescr)
        escape(i0)
        jump(p1, i1, i2)
        """
        preamble = """
        [p1, i1, i2]
        i3 = int_sub(i2, i1)
        i0 = call(0, p1, i1, i3, 120, descr=streq_slice_char_descr)
        escape(i0)
        jump(p1, i1, i2, i3)
        """
        expected = """
        [p1, i1, i2, i3]
        i0 = call(0, p1, i1, i3, 120, descr=streq_slice_char_descr)
        escape(i0)
        jump(p1, i1, i2, i3)
        """
        self.optimize_strunicode_loop_extradescrs(ops, expected,
                                                  preamble)

    def test_str_equal_slice5(self):
        ops = """
        [p1, i1, i2, i3]
        p4 = call(0, p1, i1, i2, descr=strslicedescr)
        p5 = newstr(1)
        strsetitem(p5, 0, i3)
        i0 = call(0, p5, p4, descr=strequaldescr)
        escape(i0)
        jump(p1, i1, i2, i3)
        """
        preamble = """
        [p1, i1, i2, i3]
        i4 = int_sub(i2, i1)
        i0 = call(0, p1, i1, i4, i3, descr=streq_slice_char_descr)
        escape(i0)
        jump(p1, i1, i2, i3, i4)
        """
        expected = """
        [p1, i1, i2, i3, i4]
        i0 = call(0, p1, i1, i4, i3, descr=streq_slice_char_descr)
        escape(i0)
        jump(p1, i1, i2, i3, i4)
        """
        self.optimize_strunicode_loop_extradescrs(ops, expected,
                                                  preamble)

    def test_str_equal_none1(self):
        ops = """
        [p1]
        i0 = call(0, p1, NULL, descr=strequaldescr)
        escape(i0)
        jump(p1)
        """
        preamble = """
        [p1]
        i0 = ptr_eq(p1, NULL)
        escape(i0)
        jump(p1, i0)
        """
        expected = """
        [p1, i0]
        escape(i0)
        jump(p1, i0)
        """
        self.optimize_strunicode_loop_extradescrs(ops, expected, preamble)

    def test_str_equal_none2(self):
        ops = """
        [p1]
        i0 = call(0, NULL, p1, descr=strequaldescr)
        escape(i0)
        jump(p1)
        """
        preamble = """
        [p1]
        i0 = ptr_eq(p1, NULL)
        escape(i0)
        jump(p1, i0)
        """
        expected = """
        [p1, i0]
        escape(i0)
        jump(p1, i0)
        """
        self.optimize_strunicode_loop_extradescrs(ops, expected, preamble)

    def test_str_equal_nonnull1(self):
        ops = """
        [p1]
        guard_nonnull(p1) []
        i0 = call(0, p1, s"hello world", descr=strequaldescr)
        escape(i0)
        jump(p1)
        """
        expected = """
        [p1]
        i0 = call(0, p1, s"hello world", descr=streq_nonnull_descr)
        escape(i0)
        jump(p1)
        """
        preamble = """
        [p1]
        guard_nonnull(p1) []
        i0 = call(0, p1, s"hello world", descr=streq_nonnull_descr)
        escape(i0)
        jump(p1)
        """
        self.optimize_strunicode_loop_extradescrs(ops, expected, preamble)

    def test_str_equal_nonnull2(self):
        ops = """
        [p1]
        guard_nonnull(p1) []
        i0 = call(0, p1, s"", descr=strequaldescr)
        escape(i0)
        jump(p1)
        """
        expected = """
        [p1, i0]
        escape(i0)
        jump(p1, i0)
        """
        preamble = """
        [p1]
        guard_nonnull(p1) []
        i1 = strlen(p1)
        i0 = int_eq(i1, 0)
        escape(i0)
        jump(p1, i0)
        """
        self.optimize_strunicode_loop_extradescrs(ops, expected, preamble)

    def test_str_equal_nonnull3(self):
        ops = """
        [p1]
        guard_nonnull(p1) []
        i0 = call(0, p1, s"x", descr=strequaldescr)
        escape(i0)
        jump(p1)
        """
        expected = """
        [p1]
        i0 = call(0, p1, 120, descr=streq_nonnull_char_descr)
        escape(i0)
        jump(p1)
        """
        preamble = """
        [p1]
        guard_nonnull(p1) []
        i0 = call(0, p1, 120, descr=streq_nonnull_char_descr)
        escape(i0)
        jump(p1)
        """
        self.optimize_strunicode_loop_extradescrs(ops, expected, preamble)

    def test_str_equal_nonnull4(self):
        ops = """
        [p1, p2]
        p4 = call(0, p1, p2, descr=strconcatdescr)
        i0 = call(0, s"hello world", p4, descr=strequaldescr)
        escape(i0)
        jump(p1, p2)
        """
        preamble = """
        [p1, p2]
        i1 = strlen(p1)
        i2 = strlen(p2)
        i3 = int_add(i1, i2)
        p4 = newstr(i3)
        copystrcontent(p1, p4, 0, 0, i1)
        copystrcontent(p2, p4, 0, i1, i2)
        i0 = call(0, s"hello world", p4, descr=streq_nonnull_descr)
        escape(i0)
        jump(p1, p2, i3, i1, i2)
        """
        expected = """
        [p1, p2, i3, i1, i2]
        p4 = newstr(i3)
        copystrcontent(p1, p4, 0, 0, i1)
        copystrcontent(p2, p4, 0, i1, i2)
        i0 = call(0, s"hello world", p4, descr=streq_nonnull_descr)
        escape(i0)
        jump(p1, p2, i3, i1, i2)
        """
        self.optimize_strunicode_loop_extradescrs(ops, expected, preamble)

    def test_str_equal_chars0(self):
        ops = """
        [i1]
        p1 = newstr(0)
        i0 = call(0, p1, s"", descr=strequaldescr)
        escape(i0)
        jump(i1)
        """
        expected = """
        [i1]
        escape(1)
        jump(i1)
        """
        self.optimize_strunicode_loop_extradescrs(ops, expected, expected)

    def test_str_equal_chars1(self):
        ops = """
        [i1]
        p1 = newstr(1)
        strsetitem(p1, 0, i1)
        i0 = call(0, p1, s"x", descr=strequaldescr)
        escape(i0)
        jump(i1)
        """
        preamble = """
        [i1]
        i0 = int_eq(i1, 120)     # ord('x')
        escape(i0)
        jump(i1, i0)
        """
        expected = """
        [i1, i0]
        escape(i0)
        jump(i1, i0)
        """
        self.optimize_strunicode_loop_extradescrs(ops, expected, preamble)

    def test_str_equal_nonconst(self):
        ops = """
        [i1, i2]
        p1 = newstr(1)
        strsetitem(p1, 0, i1)
        p2 = newstr(1)
        strsetitem(p2, 0, i2)
        i0 = call(0, p1, p2, descr=strequaldescr)
        escape(i0)
        jump(i1, i2)
        """
        preamble = """
        [i1, i2]
        i0 = int_eq(i1, i2)
        escape(i0)
        jump(i1, i2, i0)
        """
        expected = """
        [i1, i2, i0]
        escape(i0)
        jump(i1, i2, i0)
        """
        self.optimize_strunicode_loop_extradescrs(ops, expected, preamble)

    def test_str_equal_chars2(self):
        ops = """
        [i1, i2]
        p1 = newstr(2)
        strsetitem(p1, 0, i1)
        strsetitem(p1, 1, i2)
        i0 = call(0, p1, s"xy", descr=strequaldescr)
        escape(i0)
        jump(i1, i2)
        """
        expected = """
        [i1, i2]
        p1 = newstr(2)
        strsetitem(p1, 0, i1)
        strsetitem(p1, 1, i2)
        i0 = call(0, p1, s"xy", descr=streq_lengthok_descr)
        escape(i0)
        jump(i1, i2)
        """
        self.optimize_strunicode_loop_extradescrs(ops, expected, expected)

    def test_str_equal_chars3(self):
        ops = """
        [p1]
        i0 = call(0, s"x", p1, descr=strequaldescr)
        escape(i0)
        jump(p1)
        """
        expected = """
        [p1]
        i0 = call(0, p1, 120, descr=streq_checknull_char_descr)
        escape(i0)
        jump(p1)
        """
        self.optimize_strunicode_loop_extradescrs(ops, expected, expected)

    def test_str_equal_lengthmismatch1(self):
        ops = """
        [i1]
        p1 = newstr(1)
        strsetitem(p1, 0, i1)
        i0 = call(0, s"xy", p1, descr=strequaldescr)
        escape(i0)
        jump(i1)
        """
        expected = """
        [i1]
        escape(0)
        jump(i1)
        """
        self.optimize_strunicode_loop_extradescrs(ops, expected, expected)

    def test_str2unicode_constant(self):
        ops = """
        []
        escape(1213)
        p0 = call(0, "xy", descr=s2u_descr)      # string -> unicode
        guard_no_exception() []
        escape(p0)
        jump()
        """
        expected = """
        []
        escape(1213)
        escape(u"xy")
        jump()
        """
        self.optimize_strunicode_loop_extradescrs(ops, expected, expected)

    def test_str2unicode_nonconstant(self):
        ops = """
        [p0]
        p1 = call(0, p0, descr=s2u_descr)      # string -> unicode
        guard_no_exception() []
        escape(p1)
        jump(p1)
        """
        self.optimize_strunicode_loop_extradescrs(ops, ops, ops)
        # more generally, supporting non-constant but virtual cases is
        # not obvious, because of the exception UnicodeDecodeError that
        # can be raised by ll_str2unicode()

    def test_record_known_class(self):
        ops = """
        [p0]
        p1 = getfield_gc(p0, descr=nextdescr)
        record_known_class(p1, ConstClass(node_vtable))
        guard_class(p1, ConstClass(node_vtable)) []
        jump(p1)
        """
        expected = """
        [p0]
        p1 = getfield_gc(p0, descr=nextdescr)
        jump(p1)
        """
        self.optimize_loop(ops, expected)

    def test_quasi_immut(self):
        ops = """
        [p0, p1, i0]
        quasiimmut_field(p0, descr=quasiimmutdescr)
        guard_not_invalidated() []
        i1 = getfield_gc_pure(p0, descr=quasifielddescr)
        escape(i1)
        jump(p1, p0, i1)
        """
        expected = """
        [p0, p1, i0]
        i1 = getfield_gc_pure(p0, descr=quasifielddescr)
        escape(i1)
        jump(p1, p0, i1)
        """
        self.optimize_loop(ops, expected)

    def test_quasi_immut_2(self):
        ops = """
        []
        quasiimmut_field(ConstPtr(quasiptr), descr=quasiimmutdescr)
        guard_not_invalidated() []
        i1 = getfield_gc_pure(ConstPtr(quasiptr), descr=quasifielddescr)
        escape(i1)
        jump()
        """
        expected = """
        []
        guard_not_invalidated() []
        escape(-4247)
        jump()
        """
        self.optimize_loop(ops, expected, expected)

    def test_remove_extra_guards_not_invalidated(self):
        ops = """
        [i0]
        guard_not_invalidated() []
        guard_not_invalidated() []
        i1 = int_add(i0, 1)
        guard_not_invalidated() []
        guard_not_invalidated() []
        jump(i1)
        """
        expected = """
        [i0]
        guard_not_invalidated() []
        i1 = int_add(i0, 1)
        jump(i1)
        """
        self.optimize_loop(ops, expected)

    def test_call_may_force_invalidated_guards(self):
        ops = """
        [i0]
        guard_not_invalidated() []
        call_may_force(i0, descr=mayforcevirtdescr)
        guard_not_invalidated() []
        jump(i0)
        """
        expected = """
        [i0]
        guard_not_invalidated() []
        call_may_force(i0, descr=mayforcevirtdescr)
        guard_not_invalidated() []
        jump(i0)
        """
        self.optimize_loop(ops, expected)

    def test_call_may_force_invalidated_guards_reload(self):
        ops = """
        [i0a, i0b]
        quasiimmut_field(ConstPtr(quasiptr), descr=quasiimmutdescr)
        guard_not_invalidated() []
        i1 = getfield_gc_pure(ConstPtr(quasiptr), descr=quasifielddescr)
        call_may_force(i0b, descr=mayforcevirtdescr)
        quasiimmut_field(ConstPtr(quasiptr), descr=quasiimmutdescr)
        guard_not_invalidated() []
        i2 = getfield_gc_pure(ConstPtr(quasiptr), descr=quasifielddescr)
        i3 = escape(i1)
        i4 = escape(i2)
        jump(i3, i4)
        """
        expected = """
        [i0a, i0b]
        guard_not_invalidated() []
        call_may_force(i0b, descr=mayforcevirtdescr)
        guard_not_invalidated() []
        i3 = escape(-4247)
        i4 = escape(-4247)
        jump(i3, i4)
        """
        self.optimize_loop(ops, expected)

    def test_call_may_force_invalidated_guards_virtual(self):
        ops = """
        [i0a, i0b]
        p = new(descr=quasisize)
        setfield_gc(p, 421, descr=quasifielddescr)
        quasiimmut_field(p, descr=quasiimmutdescr)
        guard_not_invalidated() []
        i1 = getfield_gc_pure(p, descr=quasifielddescr)
        call_may_force(i0b, descr=mayforcevirtdescr)
        quasiimmut_field(p, descr=quasiimmutdescr)
        guard_not_invalidated() []
        i2 = getfield_gc_pure(p, descr=quasifielddescr)
        i3 = escape(i1)
        i4 = escape(i2)
        jump(i3, i4)
        """
        expected = """
        [i0a, i0b]
        call_may_force(i0b, descr=mayforcevirtdescr)
        i3 = escape(421)
        i4 = escape(421)
        jump(i3, i4)
        """
        self.optimize_loop(ops, expected)

    def test_constant_getfield1(self):
        ops = """
        [p1, p187, i184]
        p188 = getarrayitem_gc(p187, 42, descr=<GcPtrArrayDescr>)
        guard_value(p188, ConstPtr(myptr)) []
        p25 = getfield_gc(ConstPtr(myptr), descr=otherdescr)
        jump(p25, p187, i184)
        """
        preamble = """
        [p1, p187, i184]
        p188 = getarrayitem_gc(p187, 42, descr=<GcPtrArrayDescr>)
        guard_value(p188, ConstPtr(myptr)) []
        p25 = getfield_gc(ConstPtr(myptr), descr=otherdescr)
        p26 = same_as(p25)
        jump(p25, p187, i184, p26)
        """
        short = """
        [p1, p187, i184]
        p188 = getarrayitem_gc(p187, 42, descr=<GcPtrArrayDescr>)
        guard_value(p188, ConstPtr(myptr)) []
        p25 = getfield_gc(ConstPtr(myptr), descr=otherdescr)
        jump(p1, p187, i184, p25)
        """
        expected = """
        [p25, p187, i184, p189]
        jump(p189, p187, i184, p189)
        """
        self.optimize_loop(ops, expected, preamble, expected_short=short)

    def test_constant_getfield1bis(self):
        ops = """
        [p1, p187, i184]
        p188 = getarrayitem_gc(p187, 42, descr=<GcPtrArrayDescr>)
        guard_value(p188, ConstPtr(myptr)) []
        p25 = getfield_gc(ConstPtr(myptr), descr=otherdescr)
        p26 = call(p25, descr=nonwritedescr)
        jump(p26, p187, i184)
        """
        expected = """
        [p24, p187, i184, p25]
        p26 = call(p25, descr=nonwritedescr)
        jump(p26, p187, i184, p25)
        """
        self.optimize_loop(ops, expected)

    def test_constant_getfield2(self):
        ops = """
        [p19]
        p22 = getfield_gc(p19, descr=otherdescr)
        guard_value(p19, ConstPtr(myptr)) []
        jump(p19)
        """
        expected = """
        []
        jump()
        """
        self.optimize_loop(ops, expected)

    def test_constant_getfield3(self):
        ops = """
        [p19, p20, p21]
        p22 = getfield_gc(p19, descr=otherdescr)
        guard_value(p19, ConstPtr(myptr)) []
        p23 = getfield_gc(ConstPtr(myptr), descr=otherdescr)
        jump(p20, p21, p21)
        """
        expected = """
        [p20, p21]
        p22 = getfield_gc(p20, descr=otherdescr)
        guard_value(p20, ConstPtr(myptr)) []
        jump(p21, p21)
        """
        self.optimize_loop(ops, expected)

    def test_constant_getfield4(self):
        ops = """
        [p19, p20, p21]
        p22 = getfield_gc(p19, descr=otherdescr)
        p23 = getfield_gc(ConstPtr(myptr), descr=otherdescr)
        guard_value(p19, ConstPtr(myptr)) []
        jump(p20, p21, p21)
        """
        expected = """
        [p20, p21]
        p22 = getfield_gc(p20, descr=otherdescr)
        guard_value(p20, ConstPtr(myptr)) []
        jump(p21, p21)
        """
        self.optimize_loop(ops, expected)

    def test_constnats_among_virtual_fileds(self):
        ops = """
        [p19, p20, p21]
        p1 = getfield_gc(p20, descr=valuedescr)
        p2 = getfield_gc(p1, descr=otherdescr)
        pv = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(pv, p19, descr=valuedescr)
        p22 = getfield_gc(p19, descr=otherdescr)
        guard_value(p19, ConstPtr(myptr)) []
        p23 = getfield_gc(ConstPtr(myptr), descr=otherdescr)
        jump(p21, pv, p21)
        """
        expected = """
        [p20]
        p22 = getfield_gc(p20, descr=otherdescr)
        guard_value(p20, ConstPtr(myptr)) []
        jump(ConstPtr(myptr))
        """
        self.optimize_loop(ops, expected)

    def test_dont_cache_setfields(self):
        # Naivly caching the last two getfields here would specialize
        # the loop to the state where the first two getfields return
        # the same value. That state would need to be guarded for
        # in the short preamble. Instead we make sure to keep the
        # results of the two getfields as separate boxes.
        ops = """
        [p0, p1, ii, ii2]
        i1 = getfield_gc(p0, descr=valuedescr)
        i2 = getfield_gc(p1, descr=otherdescr)
        i3 = int_add(i1, i2)
        setfield_gc(p0, ii, descr=valuedescr)
        setfield_gc(p1, ii, descr=otherdescr)
        i4 = getfield_gc(p0, descr=valuedescr)
        i5 = getfield_gc(p1, descr=otherdescr)
        jump(p0, p1, ii2, ii)
        """
        preamble = """
        [p0, p1, ii, ii2]
        i1 = getfield_gc(p0, descr=valuedescr)
        i2 = getfield_gc(p1, descr=otherdescr)
        i3 = int_add(i1, i2)
        setfield_gc(p0, ii, descr=valuedescr)
        setfield_gc(p1, ii, descr=otherdescr)
        jump(p0, p1, ii2, ii, ii, ii)
        """
        expected = """
        [p0, p1, ii, ii2, i1, i2]
        i3 = int_add(i1, i2)
        setfield_gc(p0, ii, descr=valuedescr)
        setfield_gc(p1, ii, descr=otherdescr)
        jump(p0, p1, ii2, ii, ii, ii)
        """
        self.optimize_loop(ops, expected)

    def test_dont_specialize_on_boxes_equal(self):
        ops = """
        [p0, p1, p3, ii, ii2]
        i1 = getfield_gc(p0, descr=valuedescr)
        i2 = getfield_gc(p1, descr=otherdescr)
        setfield_gc(p3, i1, descr=adescr)
        setfield_gc(p3, i2, descr=bdescr)
        i4 = int_eq(i1, i2)
        guard_true(i4) []
        i5 = int_gt(ii, 42)
        guard_true(i5) []
        jump(p0, p1, p3, ii2, ii)
        """
        expected = """
        [p0, p1, p3, ii, ii2, i1, i2]
        setfield_gc(p3, i1, descr=adescr)
        setfield_gc(p3, i2, descr=bdescr)
        i5 = int_gt(ii, 42)
        guard_true(i5) []
        jump(p0, p1, p3, ii2, ii, i1, i2)
        """
        self.optimize_loop(ops, expected)

    def test_lazy_setfield_forced_by_jump_needing_additionall_inputargs(self):
        ops = """
        [p0, p3]
        i1 = getfield_gc(p0, descr=valuedescr)
        setfield_gc(p3, i1, descr=otherdescr)
        jump(p0, p3)
        """
        expected = """
        [p0, p3, i1]
        setfield_gc(p3, i1, descr=otherdescr)
        jump(p0, p3, i1)
        """
        self.optimize_loop(ops, expected)

    def test_guards_before_getfields_in_short_preamble(self):
        ops = """
        [p0]
        guard_nonnull_class(p0, ConstClass(node_vtable)) []
        p1 = getfield_gc(p0, descr=nextdescr)
        guard_nonnull_class(p1, ConstClass(node_vtable)) []
        p2 = getfield_gc(p1, descr=nextdescr)
        guard_nonnull_class(p2, ConstClass(node_vtable)) []
        jump(p0)
        """
        expected = """
        [p0]
        jump(p0)
        """
        short = """
        [p0]
        p1 = getfield_gc(p0, descr=nextdescr)
        guard_nonnull_class(p1, ConstClass(node_vtable)) []
        p2 = getfield_gc(p1, descr=nextdescr)
        guard_nonnull_class(p2, ConstClass(node_vtable)) []
        jump(p0)
        """
        self.optimize_loop(ops, expected, expected_short=short)

    def test_forced_virtual_pure_getfield(self):
        ops = """
        [p0]
        p1 = getfield_gc_pure(p0, descr=valuedescr)
        jump(p1)
        """
        self.optimize_loop(ops, ops)

        ops = """
        [p0]
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, p0, descr=valuedescr)
        escape(p1)
        p2 = getfield_gc_pure(p1, descr=valuedescr)
        escape(p2)
        jump(p0)
        """
        expected = """
        [p0]
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, p0, descr=valuedescr)
        escape(p1)
        escape(p0)
        jump(p0)
        """
        self.optimize_loop(ops, expected)

    def test_setarrayitem_lazy(self):
        ops = """
        [i0, i1]
        p0 = escape()
        i2 = escape()
        p1 = new_with_vtable(ConstClass(node_vtable))
        setarrayitem_gc(p0, 2, p1, descr=arraydescr)
        guard_true(i2) []
        setarrayitem_gc(p0, 2, p0, descr=arraydescr)
        jump(i0, i1)
        """
        expected = """
        [i0, i1]
        p0 = escape()
        i2 = escape()
        guard_true(i2) [p0]
        setarrayitem_gc(p0, 2, p0, descr=arraydescr)
        jump(i0, i1)
        """
        self.optimize_loop(ops, expected)

    def test_force_virtualizable_virtual(self):
        ops = """
        [i0]
        p1 = new_with_vtable(ConstClass(node_vtable))
        cond_call(1, 123, p1, descr=clear_vable)
        jump(i0)
        """
        expected = """
        [i0]
        jump(i0)
        """
        self.optimize_loop(ops, expected)

    def test_setgetfield_counter(self):
        ops = """
        [p1]
        i2 = getfield_gc(p1, descr=valuedescr)
        i3 = int_add(i2, 1)
        setfield_gc(p1, i3, descr=valuedescr)
        jump(p1)
        """
        expected = """
        [p1, i1]
        i2 = int_add(i1, 1)
        setfield_gc(p1, i2, descr=valuedescr)
        jump(p1, i2)
        """
        self.optimize_loop(ops, expected)

    def test_loopinvariant_strlen(self):
        ops = """
        [p9]
        i843 = strlen(p9)
        call(i843, descr=nonwritedescr)
        jump(p9)
        """
        preamble = """
        [p9]
        i843 = strlen(p9)
        call(i843, descr=nonwritedescr)
        jump(p9, i843)
        """
        short = """
        [p9]
        i843 = strlen(p9)
        i848 = int_ge(i843, 0)
        guard_true(i848)[]
        jump(p9, i843)
        """
        expected = """
        [p9, i2]
        call(i2, descr=nonwritedescr)
        jump(p9, i2)
        """
        self.optimize_loop(ops, expected, preamble, expected_short=short)

    def test_loopinvariant_strlen_with_bound(self):
        ops = """
        [p9]
        i843 = strlen(p9)
        i1 = int_gt(i843, 7)
        guard_true(i1) []
        call(i843, descr=nonwritedescr)
        jump(p9)
        """
        expected = """
        [p9, i2]
        call(i2, descr=nonwritedescr)
        jump(p9, i2)
        """
        self.optimize_loop(ops, expected)

    def test_loopinvariant_strgetitem(self):
        ops = """
        [p9, i1]
        i843 = strgetitem(p9, i1)
        call(i843, descr=nonwritedescr)
        jump(p9, i1)
        """
        expected = """
        [p9, i1, i843]
        call(i843, descr=nonwritedescr)
        jump(p9, i1, i843)
        """
        self.optimize_loop(ops, expected)

    def test_loopinvariant_unicodelen(self):
        ops = """
        [p9]
        i843 = unicodelen(p9)
        call(i843, descr=nonwritedescr)
        jump(p9)
        """
        expected = """
        [p9, i2]
        call(i2, descr=nonwritedescr)
        jump(p9, i2)
        """
        self.optimize_loop(ops, expected)

    def test_loopinvariant_unicodegetitem(self):
        ops = """
        [p9, i1]
        i843 = unicodegetitem(p9, i1)
        call(i843, descr=nonwritedescr)
        jump(p9, i1)
        """
        expected = """
        [p9, i1, i843]
        call(i843, descr=nonwritedescr)
        jump(p9, i1, i843)
        """
        self.optimize_loop(ops, expected)

    def test_loopinvariant_arraylen(self):
        ops = """
        [p9]
        i843 = arraylen_gc(p9)
        call(i843, descr=nonwritedescr)
        jump(p9)
        """
        expected = """
        [p9, i2]
        call(i2, descr=nonwritedescr)
        jump(p9, i2)
        """
        self.optimize_loop(ops, expected)

    def test_loopinvariant_getarrayitem(self):
        ops = """
        [p0]
        p1 = getfield_gc(p0, descr=nextdescr)
        p2 = getarrayitem_gc(p1, 7, descr=<GcPtrArrayDescr>)
        call(p2, descr=nonwritedescr)
        jump(p0)
        """
        short = """
        [p0]
        p1 = getfield_gc(p0, descr=nextdescr)
        guard_nonnull(p1) []
        i1 = arraylen_gc(p1)
        i2 = int_ge(i1, 8)
        guard_true(i2) []
        p2 = getarrayitem_gc(p1, 7, descr=<GcPtrArrayDescr>)
        jump(p0, p2, p1)
        """
        expected = """
        [p0, p2, p1]
        call(p2, descr=nonwritedescr)
        i3 = arraylen_gc(p1) # Should be killed by backend
        jump(p0, p2, p1)
        """
        self.optimize_loop(ops, expected, expected_short=short)

    def test_duplicated_virtual(self):
        ops = """
        [p1, p2]
        p3 = new_with_vtable(ConstClass(node_vtable))
        jump(p3, p3)
        """
        expected = """
        []
        jump()
        """
        self.optimize_loop(ops, expected)

    def test_duplicated_aliased_virtual(self):
        ops = """
        [p1, p2]
        p3 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p3, p3, descr=nextdescr)
        p4 = getfield_gc(p3, descr=nextdescr)
        jump(p3, p4)
        """
        expected = """
        []
        jump()
        """
        self.optimize_loop(ops, expected)

    def test_imported_aliased_virtual_in_failargs(self):
        ops = """
        [p1, p2, i0]
        i2 = int_lt(i0, 10)
        guard_true(i2) [p1, p2]
        p3 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p3, p3, descr=nextdescr)
        p4 = getfield_gc(p3, descr=nextdescr)
        i1 = int_add(i0, 1)
        jump(p3, p4, i1)
        """
        expected = """
        [i0]
        i2 = int_lt(i0, 10)
        guard_true(i2) []
        i1 = int_add(i0, 1)
        jump(i1)
        """
        self.optimize_loop(ops, expected)

    def test_chained_virtuals(self):
        ops = """
        [p0, p1]
        p2 = new_with_vtable(ConstClass(node_vtable))
        p3 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p2, p3, descr=nextdescr)
        jump(p2, p3)
        """
        expected = """
        []
        jump()
        """
        self.optimize_loop(ops, expected)

    def test_arraylen_bound(self):
        ops = """
        [p1, i]
        p2 = getarrayitem_gc(p1, 7, descr=<GcPtrArrayDescr>)
        i1 = arraylen_gc(p1)
        i2 = int_ge(i1, 8)
        guard_true(i2) []
        jump(p2, i2)
        """
        expected = """
        [p1]
        p2 = getarrayitem_gc(p1, 7, descr=<GcPtrArrayDescr>)
        i1 = arraylen_gc(p1)
        jump(p2)
        """
        self.optimize_loop(ops, expected)

    def test_loopinvariant_getarrayitem_gc_pure(self):
        ops = """
        [p9, i1]
        i843 = getarrayitem_gc_pure(p9, i1)
        call(i843, descr=nonwritedescr)
        jump(p9, i1)
        """
        expected = """
        [p9, i1, i843]
        call(i843, descr=nonwritedescr)
        jump(p9, i1, i843)
        """
        self.optimize_loop(ops, expected)

    def test_loopinvariant_constant_getarrayitem_pure(self):
        ops = """
        [p0]
        p1 = getfield_gc(p0, descr=nextdescr)
        p2 = getarrayitem_gc_pure(p1, 7, descr=<GcPtrArrayDescr>)
        call(p2, descr=nonwritedescr)
        jump(p0)
        """
        short = """
        [p0]
        p1 = getfield_gc(p0, descr=nextdescr)
        guard_nonnull(p1) []
        i1 = arraylen_gc(p1)
        i2 = int_ge(i1, 8)
        guard_true(i2) []
        p2 = getarrayitem_gc_pure(p1, 7, descr=<GcPtrArrayDescr>)
        jump(p0, p2, p1)
        """
        expected = """
        [p0, p2, p1]
        call(p2, descr=nonwritedescr)
        i3 = arraylen_gc(p1) # Should be killed by backend
        jump(p0, p2, p1)
        """
        self.optimize_loop(ops, expected, expected_short=short)

    def test_loopinvariant_constant_strgetitem(self):
        ops = """
        [p0]
        p1 = getfield_gc(p0, descr=nextdescr)
        i22 = strgetitem(p1, 7)
        call(i22, descr=nonwritedescr)
        jump(p0)
        """
        short = """
        [p0]
        p1 = getfield_gc(p0, descr=nextdescr)
        guard_nonnull(p1) []
        i1 = strlen(p1)
        i2 = int_ge(i1, 8)
        guard_true(i2) []
        i22 = strgetitem(p1, 7, descr=<GcPtrArrayDescr>)
        i8 = int_ge(i22, 0)
        guard_true(i8) []
        i9 = int_le(i22, 255)
        guard_true(i9) []
        jump(p0, i22, p1)
        """
        expected = """
        [p0, i22, p1]
        call(i22, descr=nonwritedescr)
        i3 = strlen(p1) # Should be killed by backend
        jump(p0, i22, p1)
        """
        self.optimize_loop(ops, expected, expected_short=short)

    def test_loopinvariant_constant_unicodegetitem(self):
        ops = """
        [p0]
        p1 = getfield_gc(p0, descr=nextdescr)
        i22 = unicodegetitem(p1, 7)
        call(i22, descr=nonwritedescr)
        jump(p0)
        """
        short = """
        [p0]
        p1 = getfield_gc(p0, descr=nextdescr)
        guard_nonnull(p1) []
        i1 = unicodelen(p1)
        i2 = int_ge(i1, 8)
        guard_true(i2) []
        i22 = unicodegetitem(p1, 7, descr=<GcPtrArrayDescr>)
        i8 = int_ge(i22, 0)
        guard_true(i8) []
        jump(p0, i22, p1)
        """
        expected = """
        [p0, i22, p1]
        call(i22, descr=nonwritedescr)
        i3 = unicodelen(p1) # Should be killed by backend
        jump(p0, i22, p1)
        """
        self.optimize_loop(ops, expected, expected_short=short)

    def test_propagate_virtual_arraylen(self):
        ops = """
        [p0]
        p404 = new_array(2, descr=arraydescr)
        p403 = new_array(3, descr=arraydescr)
        i405 = arraylen_gc(p404, descr=arraydescr)
        i406 = arraylen_gc(p403, descr=arraydescr)
        i407 = int_add_ovf(i405, i406)
        guard_no_overflow() []
        call(i407, descr=nonwritedescr)
        jump(p0)
        """
        expected = """
        [p0]
        call(5, descr=nonwritedescr)
        jump(p0)
        """
        self.optimize_loop(ops, expected)

    def test_propagate_virtual_strunicodelen(self):
        ops = """
        [p0]
        p404 = newstr(2)
        p403 = newunicode(3)
        i405 = strlen(p404)
        i406 = unicodelen(p403)
        i407 = int_add_ovf(i405, i406)
        guard_no_overflow() []
        call(i407, descr=nonwritedescr)
        jump(p0)
        """
        expected = """
        [p0]
        call(5, descr=nonwritedescr)
        jump(p0)
        """
        self.optimize_loop(ops, expected)

    def test_keep_getfields_and_inputargs_separate(self):
        ops = """
        [p0]
        call(p0, descr=nonwritedescr)
        p1 = getfield_gc(ConstPtr(myptr), descr=nextdescr)
        call(p1, descr=writeadescr)
        jump(p1)
        """
        expected = """
        [p0, p1]
        call(p0, descr=nonwritedescr)
        call(p1, descr=writeadescr)
        jump(p1, p1)
        """
        self.optimize_loop(ops, expected)

    def test_value_guard_arraylen_reused(self):
        ops = """
        [p0, p1]
        p10 = getfield_gc(p0, descr=nextdescr)
        p11 = getfield_gc(p1, descr=nextdescr)
        i1 = arraylen_gc(p10, descr=arraydescr)
        getarrayitem_gc(p11, 1, descr=arraydescr)
        call(i1, descr=nonwritedescr)
        jump(p1, p0)
        """
        expected = """
        [p0, p1, p10, p11]
        i1 = arraylen_gc(p10, descr=arraydescr)
        getarrayitem_gc(p11, 1, descr=arraydescr)
        call(i1, descr=nonwritedescr)
        jump(p1, p0, p11, p10)
        """
        self.optimize_loop(ops, expected)

    def test_cache_constant_setfield(self):
        ops = """
        [p5]
        i10 = getfield_gc(p5, descr=valuedescr)
        call(i10, descr=nonwritedescr)
        setfield_gc(p5, 1, descr=valuedescr)
        jump(p5)
        """
        preamble = """
        [p5]
        i10 = getfield_gc(p5, descr=valuedescr)
        call(i10, descr=nonwritedescr)
        setfield_gc(p5, 1, descr=valuedescr)
        jump(p5)
        """
        expected = """
        [p5]
        call(1, descr=nonwritedescr)
        jump(p5)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_dont_mixup_equal_boxes(self):
        ops = """
        [p8]
        i9 = getfield_gc_pure(p8, descr=valuedescr)
        i10 = int_gt(i9, 0)
        guard_true(i10) []
        i29 = int_lshift(i9, 1)
        i30 = int_rshift(i29, 1)
        i40 = int_ne(i30, i9)
        guard_false(i40) []
        jump(p8)
        """
        expected = """
        [p8]
        jump(p8)
        """
        self.optimize_loop(ops, expected)

    def test_specialized_to_cached_constant_guard(self):
        ops = """
        [p9]
        i16 = getfield_gc(p9, descr=valuedescr)
        i17 = int_is_true(i16)
        guard_false(i17) []
        call_assembler(i17, descr=asmdescr)
        i18 = getfield_gc(p9, descr=valuedescr)
        guard_value(i18, 0) []
        jump(p9)
        """
        expected = """
        [p9]
        call_assembler(0, descr=asmdescr)
        i18 = getfield_gc(p9, descr=valuedescr)
        guard_value(i18, 0) []
        jump(p9)
        """
        self.optimize_loop(ops, expected)

    def test_specialized_to_cached_constant_setfield(self):
        ops = """
        [p9]
        i16 = getfield_gc(p9, descr=valuedescr)
        i17 = int_is_true(i16)
        guard_false(i17) []
        call_assembler(i17, descr=asmdescr)
        i18 = setfield_gc(p9, 0, descr=valuedescr)
        jump(p9)
        """
        expected = """
        [p9]
        call_assembler(0, descr=asmdescr)
        i18 = setfield_gc(p9, 0, descr=valuedescr)
        jump(p9)
        """
        self.optimize_loop(ops, expected)

    def test_cached_equal_fields(self):
        ops = """
        [p5, p6]
        i10 = getfield_gc(p5, descr=valuedescr)
        i11 = getfield_gc(p6, descr=nextdescr)
        call(i10, i11, descr=nonwritedescr)
        setfield_gc(p6, i10, descr=nextdescr)
        jump(p5, p6)
        """
        expected = """
        [p5, p6, i10, i11]
        call(i10, i11, descr=nonwritedescr)
        setfield_gc(p6, i10, descr=nextdescr)
        jump(p5, p6, i10, i10)
        """
        self.optimize_loop(ops, expected)

    def test_cached_pure_func_of_equal_fields(self):
        ops = """
        [p5, p6]
        i10 = getfield_gc(p5, descr=valuedescr)
        i11 = getfield_gc(p6, descr=nextdescr)
        i12 = int_add(i10, 7)
        i13 = int_add(i11, 7)
        call(i12, i13, descr=nonwritedescr)
        setfield_gc(p6, i10, descr=nextdescr)
        jump(p5, p6)
        """
        expected = """
        [p5, p6, i14, i12, i10]
        i13 = int_add(i14, 7)
        call(i12, i13, descr=nonwritedescr)
        setfield_gc(p6, i10, descr=nextdescr)
        jump(p5, p6, i10, i12, i10)
        """
        self.optimize_loop(ops, expected)

    def test_forced_counter(self):
        # XXX: VIRTUALHEAP (see above)
        py.test.skip("would be fixed by make heap optimizer aware of virtual setfields")
        ops = """
        [p5, p8]
        i9 = getfield_gc_pure(p5, descr=valuedescr)
        call(i9, descr=nonwritedescr)
        i11 = getfield_gc_pure(p8, descr=valuedescr)
        i13 = int_add_ovf(i11, 1)
        guard_no_overflow() []
        p22 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p22, i13, descr=valuedescr)
        setfield_gc(ConstPtr(myptr), p22, descr=adescr)
        jump(p22, p22)
        """
        expected = """
        [p8, i9]
        call(i9, descr=nonwritedescr)
        i13 = int_add_ovf(i9, 1)
        guard_no_overflow() []
        p22 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p22, i13, descr=valuedescr)
        setfield_gc(ConstPtr(myptr), p22, descr=adescr)
        jump(p22, i13)
        """
        self.optimize_loop(ops, expected)

    def test_constptr_samebox_getfield_setfield(self):
        ops = """
        [p0]
        p10 = getfield_gc(ConstPtr(myptr), descr=valuedescr)
        call(p10, descr=nonwritedescr)
        setfield_gc(ConstPtr(myptr), p10, descr=valuedescr)
        jump(p0)
        """
        expected = """
        [p0, p10]
        call(p10, descr=nonwritedescr)
        jump(p0, p10)
        """
        self.optimize_loop(ops, expected)

    def test_constptr_constptr_getfield_setfield(self):
        ops = """
        [p0]
        p10 = getfield_gc(ConstPtr(myptr), descr=valuedescr)
        guard_value(p10, ConstPtr(myptr2)) []
        call(p10, descr=nonwritedescr)
        setfield_gc(ConstPtr(myptr), ConstPtr(myptr2), descr=valuedescr)
        jump(p0)
        """
        expected = """
        [p0]
        call(ConstPtr(myptr2), descr=nonwritedescr)
        jump(p0)
        """
        self.optimize_loop(ops, expected)

    def test_box_samebox_getfield_setfield(self):
        ops = """
        [p0]
        p10 = getfield_gc(p0, descr=valuedescr)
        call(p10, descr=nonwritedescr)
        setfield_gc(p0, p10, descr=valuedescr)
        jump(p0)
        """
        expected = """
        [p0, p10]
        call(p10, descr=nonwritedescr)
        jump(p0, p10)
        """
        self.optimize_loop(ops, expected)

    def test_box_constptr_getfield_setfield(self):
        ops = """
        [p0]
        p10 = getfield_gc(p0, descr=valuedescr)
        guard_value(p10, ConstPtr(myptr2)) []
        call(p10, descr=nonwritedescr)
        setfield_gc(p0, ConstPtr(myptr2), descr=valuedescr)
        jump(p0)
        """
        expected = """
        [p0]
        call(ConstPtr(myptr2), descr=nonwritedescr)
        jump(p0)
        """
        self.optimize_loop(ops, expected)

    def test_import_constants_when_folding_pure_operations(self):
        ops = """
        [p0]
        f1 = getfield_gc(p0, descr=valuedescr)
        f2 = float_abs(f1)
        call(7.0, descr=nonwritedescr)
        setfield_gc(p0, -7.0, descr=valuedescr)
        jump(p0)
        """
        expected = """
        [p0]
        call(7.0, descr=nonwritedescr)
        jump(p0)
        """
        self.optimize_loop(ops, expected)

    def test_exploding_duplicatipon(self):
        ops = """
        [i1, i2]
        i3 = int_add(i1, i1)
        i4 = int_add(i3, i3)
        i5 = int_add(i4, i4)
        i6 = int_add(i5, i5)
        call(i6, descr=nonwritedescr)
        jump(i1, i3)
        """
        expected = """
        [i1, i2, i6, i3]
        call(i6, descr=nonwritedescr)
        jump(i1, i3, i6, i3)
        """
        short = """
        [i1, i2]
        i3 = int_add(i1, i1)
        i4 = int_add(i3, i3)
        i5 = int_add(i4, i4)
        i6 = int_add(i5, i5)
        jump(i1, i2, i6, i3)
        """
        self.optimize_loop(ops, expected, expected_short=short)

    def test_prioritize_getfield1(self):
        ops = """
        [p1, p2]
        i1 = getfield_gc(p1, descr=valuedescr)
        setfield_gc(p2, i1, descr=nextdescr)
        i2 = int_neg(i1)
        call(i2, descr=nonwritedescr)
        jump(p1, p2)
        """
        expected = """
        [p1, p2, i2, i1]
        call(i2, descr=nonwritedescr)
        setfield_gc(p2, i1, descr=nextdescr)
        jump(p1, p2, i2, i1)
        """
        self.optimize_loop(ops, expected)

    def test_prioritize_getfield2(self):
        # Same as previous, but with descrs intercahnged which means
        # that the getfield is discovered first when looking for
        # potential short boxes during tests
        ops = """
        [p1, p2]
        i1 = getfield_gc(p1, descr=nextdescr)
        setfield_gc(p2, i1, descr=valuedescr)
        i2 = int_neg(i1)
        call(i2, descr=nonwritedescr)
        jump(p1, p2)
        """
        expected = """
        [p1, p2, i2, i1]
        call(i2, descr=nonwritedescr)
        setfield_gc(p2, i1, descr=valuedescr)
        jump(p1, p2, i2, i1)
        """
        self.optimize_loop(ops, expected)

    def test_heap_cache_forced_virtuals(self):
        ops = """
        [i1, i2, p0]
        p1 = new(descr=ssize)
        setfield_gc(p1, i1, descr=adescr)
        setfield_gc(p1, i2, descr=bdescr)
        call(p0, p1, descr=writeadescr)
        i3 = getfield_gc(p1, descr=adescr)
        i4 = getfield_gc(p1, descr=bdescr)
        jump(i3, i4, p0)
        """
        expected = """
        [i1, i2, p0]
        p1 = new(descr=ssize)
        setfield_gc(p1, i1, descr=adescr)
        call(p0, p1, descr=writeadescr)
        i3 = getfield_gc(p1, descr=adescr)
        setfield_gc(p1, i2, descr=bdescr)
        jump(i3, i2, p0)
        """
        self.optimize_loop(ops, expected)

    def test_setarrayitem_followed_by_arraycopy(self):
        ops = """
        [p1, p2]
        setarrayitem_gc(p1, 2, 10, descr=arraydescr)
        setarrayitem_gc(p2, 3, 13, descr=arraydescr)
        call(0, p1, p2, 0, 0, 10, descr=arraycopydescr)
        jump(p1, p2)
        """
        self.optimize_loop(ops, ops)

    def test_setarrayitem_followed_by_arraycopy_2(self):
        ops = """
        [i1, i2]
        p1 = new_array(i1, descr=arraydescr)
        setarrayitem_gc(p1, 0, i2, descr=arraydescr)
        p3 = new_array(5, descr=arraydescr)
        call(0, p1, p3, 0, 1, 1, descr=arraycopydescr)
        i4 = getarrayitem_gc(p3, 1, descr=arraydescr)
        jump(i1, i4)
        """
        expected = """
        [i1, i2]
        # operations are not all removed because this new_array() is var-sized
        p1 = new_array(i1, descr=arraydescr)
        setarrayitem_gc(p1, 0, i2, descr=arraydescr)
        jump(i1, i2)
        """
        self.optimize_loop(ops, expected)

    def test_heap_cache_virtuals_forced_by_delayed_setfield(self):
        py.test.skip('not yet supoprted')
        ops = """
        [i1, p0]
        p1 = new(descr=ssize)
        setfield_gc(p1, i1, descr=valuedescr)
        setfield_gc(p0, p1, descr=adescr)
        call(p0, descr=writeadescr)
        i2 = getfield_gc(p1, descr=valuedescr)
        jump(i2, p0)
        """
        expected = """
        [i1, p0]
        p1 = new(descr=ssize)
        setfield_gc(p1, i1, descr=valuedescr)
        setfield_gc(p0, p1, descr=adescr)
        call(p0, descr=writeadescr)
        jump(i1, p0)
        """
        self.optimize_loop(ops, expected)

    def test_repeated_constant_setfield_mixed_with_guard(self):
        ops = """
        [p22, p18]
        setfield_gc(p22, 2, descr=valuedescr)
        guard_nonnull_class(p18, ConstClass(node_vtable)) []
        setfield_gc(p22, 2, descr=valuedescr)
        jump(p22, p18)
        """
        preamble = """
        [p22, p18]
        setfield_gc(p22, 2, descr=valuedescr)
        guard_nonnull_class(p18, ConstClass(node_vtable)) []
        jump(p22, p18)
        """
        short = """
        [p22, p18]
        i1 = getfield_gc(p22, descr=valuedescr)
        guard_value(i1, 2) []
        jump(p22, p18)
        """
        expected = """
        [p22, p18]
        jump(p22, p18)
        """
        self.optimize_loop(ops, expected, preamble, expected_short=short)

    def test_repeated_setfield_mixed_with_guard(self):
        ops = """
        [p22, p18, i1]
        i2 = getfield_gc(p22, descr=valuedescr)
        call(i2, descr=nonwritedescr)
        setfield_gc(p22, i1, descr=valuedescr)
        guard_nonnull_class(p18, ConstClass(node_vtable)) []
        setfield_gc(p22, i1, descr=valuedescr)
        jump(p22, p18, i1)
        """
        preamble = """
        [p22, p18, i1]
        i2 = getfield_gc(p22, descr=valuedescr)
        call(i2, descr=nonwritedescr)
        setfield_gc(p22, i1, descr=valuedescr)
        guard_nonnull_class(p18, ConstClass(node_vtable)) []
        i10 = same_as(i1)
        jump(p22, p18, i1, i10)
        """
        short = """
        [p22, p18, i1]
        i2 = getfield_gc(p22, descr=valuedescr)
        jump(p22, p18, i1, i2)
        """
        expected = """
        [p22, p18, i1, i2]
        call(i2, descr=nonwritedescr)
        setfield_gc(p22, i1, descr=valuedescr)
        jump(p22, p18, i1, i1)
        """
        self.optimize_loop(ops, expected, preamble, expected_short=short)

    def test_cache_setfield_across_loop_boundaries(self):
        ops = """
        [p1]
        p2 = getfield_gc(p1, descr=valuedescr)
        guard_nonnull_class(p2, ConstClass(node_vtable)) []
        call(p2, descr=nonwritedescr)
        p3 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, p3, descr=valuedescr)
        jump(p1)
        """
        expected = """
        [p1, p2]
        call(p2, descr=nonwritedescr)
        p3 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, p3, descr=valuedescr)
        jump(p1, p3)
        """
        self.optimize_loop(ops, expected)

    def test_cache_setarrayitem_across_loop_boundaries(self):
        ops = """
        [p1]
        p2 = getarrayitem_gc(p1, 3, descr=arraydescr)
        guard_nonnull_class(p2, ConstClass(node_vtable)) []
        call(p2, descr=nonwritedescr)
        p3 = new_with_vtable(ConstClass(node_vtable))
        setarrayitem_gc(p1, 3, p3, descr=arraydescr)
        jump(p1)
        """
        expected = """
        [p1, p2]
        call(p2, descr=nonwritedescr)
        p3 = new_with_vtable(ConstClass(node_vtable))
        setarrayitem_gc(p1, 3, p3, descr=arraydescr)
        jump(p1, p3)
        """
        self.optimize_loop(ops, expected)

    def test_setarrayitem_p0_p0(self):
        ops = """
        [i0, i1]
        p0 = escape()
        setarrayitem_gc(p0, 2, p0, descr=arraydescr)
        jump(i0, i1)
        """
        expected = """
        [i0, i1]
        p0 = escape()
        setarrayitem_gc(p0, 2, p0, descr=arraydescr)
        jump(i0, i1)
        """
        self.optimize_loop(ops, expected)

    def test_setfield_p0_p0(self):
        ops = """
        [i0, i1]
        p0 = escape()
        setfield_gc(p0, p0, descr=arraydescr)
        jump(i0, i1)
        """
        expected = """
        [i0, i1]
        p0 = escape()
        setfield_gc(p0, p0, descr=arraydescr)
        jump(i0, i1)
        """
        self.optimize_loop(ops, expected)

    def test_setfield_p0_p1_p0(self):
        ops = """
        [i0, i1]
        p0 = escape()
        p1 = escape()
        setfield_gc(p0, p1, descr=adescr)
        setfield_gc(p1, p0, descr=bdescr)
        jump(i0, i1)
        """
        expected = """
        [i0, i1]
        p0 = escape()
        p1 = escape()
        setfield_gc(p0, p1, descr=adescr)
        setfield_gc(p1, p0, descr=bdescr)
        jump(i0, i1)
        """
        self.optimize_loop(ops, expected)

    def test_setinteriorfield_should_not_clear_cache(self):
        ops = """
        [i0, p0]
        i2 = getfield_gc(p0, descr=adescr)
        i3 = call(i2, descr=nonwritedescr)
        setinteriorfield_raw(i0, i2, i3)
        jump(i0, p0)
        """
        expected = """
        [i0, p0, i2]
        i3 = call(i2, descr=nonwritedescr)
        setinteriorfield_raw(i0, i2, i3)
        jump(i0, p0, i2)
        """
        self.optimize_loop(ops, expected)

    def test_constant_failargs(self):
        ops = """
        [p1, i2, i3]
        setfield_gc(p1, ConstPtr(myptr), descr=nextdescr)
        p16 = getfield_gc(p1, descr=nextdescr)
        guard_true(i2) [p16, i3]
        jump(p1, i3, i2)
        """
        preamble = """
        [p1, i2, i3]
        setfield_gc(p1, ConstPtr(myptr), descr=nextdescr)
        guard_true(i2) [i3]
        jump(p1, i3)
        """
        expected = """
        [p1, i3]
        guard_true(i3) []
        jump(p1, 1)
        """
        self.optimize_loop(ops, expected, preamble)

    def test_issue1048(self):
        ops = """
        [p1, i2, i3]
        p16 = getfield_gc(p1, descr=nextdescr)
        guard_true(i2) [p16]
        setfield_gc(p1, ConstPtr(myptr), descr=nextdescr)
        jump(p1, i3, i2)
        """
        expected = """
        [p1, i3]
        guard_true(i3) []
        jump(p1, 1)
        """
        self.optimize_loop(ops, expected)

    def test_issue1048_ok(self):
        ops = """
        [p1, i2, i3]
        p16 = getfield_gc(p1, descr=nextdescr)
        call(p16, descr=nonwritedescr)
        guard_true(i2) [p16]
        setfield_gc(p1, ConstPtr(myptr), descr=nextdescr)
        jump(p1, i3, i2)
        """
        expected = """
        [p1, i3]
        call(ConstPtr(myptr), descr=nonwritedescr)
        guard_true(i3) []
        jump(p1, 1)
        """
        self.optimize_loop(ops, expected)

    def test_issue1080_infinitie_loop_virtual(self):
        ops = """
        [p10]
        p52 = getfield_gc(p10, descr=nextdescr) # inst_storage
        p54 = getarrayitem_gc(p52, 0, descr=arraydescr)
        p69 = getfield_gc_pure(p54, descr=otherdescr) # inst_w_function

        quasiimmut_field(p69, descr=quasiimmutdescr)
        guard_not_invalidated() []
        p71 = getfield_gc_pure(p69, descr=quasifielddescr) # inst_code
        guard_value(p71, -4247) []

        p106 = new_with_vtable(ConstClass(node_vtable))
        p108 = new_array(3, descr=arraydescr)
        p110 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p110, ConstPtr(myptr2), descr=otherdescr) # inst_w_function
        setarrayitem_gc(p108, 0, p110, descr=arraydescr)
        setfield_gc(p106, p108, descr=nextdescr) # inst_storage
        jump(p106)
        """
        self.raises(InvalidLoop, self.optimize_loop, ops, ops)

    def test_issue1080_infinitie_loop_simple(self):
        ops = """
        [p69]
        quasiimmut_field(p69, descr=quasiimmutdescr)
        guard_not_invalidated() []
        p71 = getfield_gc_pure(p69, descr=quasifielddescr) # inst_code
        guard_value(p71, -4247) []
        jump(ConstPtr(myptr))
        """
        self.raises(InvalidLoop, self.optimize_loop, ops, ops)

    def test_only_strengthen_guard_if_class_matches(self):
        ops = """
        [p1]
        guard_class(p1, ConstClass(node_vtable2)) []
        guard_value(p1, ConstPtr(myptr)) []
        jump(p1)
        """
        self.raises(InvalidLoop, self.optimize_loop, ops, ops)

    def test_licm_boxed_opaque_getitem(self):
        ops = """
        [p1]
        p2 = getfield_gc(p1, descr=nextdescr)
        mark_opaque_ptr(p2)
        guard_class(p2,  ConstClass(node_vtable)) []
        i3 = getfield_gc(p2, descr=otherdescr)
        i4 = call(i3, descr=nonwritedescr)
        jump(p1)
        """
        expected = """
        [p1, i3]
        i4 = call(i3, descr=nonwritedescr)
        jump(p1, i3)
        """
        self.optimize_loop(ops, expected)

    def test_licm_boxed_opaque_getitem_unknown_class(self):
        # Explanation: the getfield_gc(p2) is done on what starts as
        # an opaque object.  The getfield_gc(p1) is moved out of the
        # (non-preamble) loop.  It looks like the getfield_gc(p2)
        # should also move out.  However, moving the getfield_gc(p2)
        # earlier can be dangerous with opaque pointers: we can't move
        # it before other guards that indirectly check for which type
        # of object is in p2.  (In this simple test there are no guard
        # at all between the start of the loop and the
        # getfield_gc(p2), but in general there are.)
        #
        # There are two cases: (1) moving the getfield_gc(p2) out of
        # the loop into the preamble: this does not look like a
        # problem because we already have a getfield_gc(p2) there, on
        # the same p2.  Case (2) is moving the getfield_gc(p2) into
        # the short preamble: this is more problematic because the
        # short preamble can't do the indirect checking on p1.
        ops = """
        [p1]
        p2 = getfield_gc(p1, descr=nextdescr)
        mark_opaque_ptr(p2)
        i3 = getfield_gc(p2, descr=otherdescr)
        i4 = call(i3, descr=nonwritedescr)
        jump(p1)
        """
        expected = """
        [p1, p2]
        i3 = getfield_gc(p2, descr=otherdescr)
        i4 = call(i3, descr=nonwritedescr)
        jump(p1, p2)
        """
        self.optimize_loop(ops, expected)

    def test_licm_unboxed_opaque_getitem(self):
        ops = """
        [p2]
        mark_opaque_ptr(p2)
        guard_class(p2,  ConstClass(node_vtable)) []
        i3 = getfield_gc(p2, descr=otherdescr)
        i4 = call(i3, descr=nonwritedescr)
        jump(p2)
        """
        expected = """
        [p1, i3]
        i4 = call(i3, descr=nonwritedescr)
        jump(p1, i3)
        """
        self.optimize_loop(ops, expected)

    def test_licm_unboxed_opaque_getitem_unknown_class(self):
        # see test_licm_boxed_opaque_getitem_unknown_class
        ops = """
        [p2]
        mark_opaque_ptr(p2)
        i3 = getfield_gc(p2, descr=otherdescr)
        i4 = call(i3, descr=nonwritedescr)
        jump(p2)
        """
        expected = """
        [p2]
        i3 = getfield_gc(p2, descr=otherdescr)
        i4 = call(i3, descr=nonwritedescr)
        jump(p2)
        """
        self.optimize_loop(ops, expected)

    def test_only_strengthen_guard_if_class_matches_2(self):
        ops = """
        [p1]
        guard_class(p1, ConstClass(node_vtable2)) []
        guard_value(p1, ConstPtr(myptr)) []
        jump(p1)
        """
        self.raises(InvalidLoop, self.optimize_loop, ops, ops)

    def test_cond_call_with_a_constant(self):
        ops = """
        [p1]
        cond_call(1, 123, p1, descr=plaincalldescr)
        jump(p1)
        """
        expected = """
        [p1]
        call(123, p1, descr=plaincalldescr)
        jump(p1)
        """
        self.optimize_loop(ops, expected)

    def test_cond_call_with_a_constant_2(self):
        ops = """
        [p1]
        cond_call(0, 123, p1, descr=plaincalldescr)
        jump(p1)
        """
        expected = """
        [p1]
        jump(p1)
        """
        self.optimize_loop(ops, expected)

    def test_hippyvm_unroll_bug(self):
        ops = """
        [p0, i1, i2]
        i3 = int_add(i1, 1)
        i4 = int_eq(i3, i2)
        setfield_gc(p0, i4, descr=valuedescr)
        jump(p0, i3, i2)
        """
        self.optimize_loop(ops, ops)

    def test_unroll_failargs(self):
        ops = """
        [p0, i1]
        p1 = getfield_gc(p0, descr=valuedescr)
        i2 = int_add(i1, 1)
        i3 = int_le(i2, 13)
        guard_true(i3) [p1]
        jump(p0, i2)      
        """
        expected = """
        [p0, i1, p1]
        i2 = int_add(i1, 1)
        i3 = int_le(i2, 13)
        guard_true(i3) [p1]
        jump(p0, i2, p1)
        """
        preamble = """
        [p0, i1]
        p1 = getfield_gc(p0, descr=valuedescr)
        i2 = int_add(i1, 1)
        i3 = int_le(i2, 13)
        guard_true(i3) [p1]
        jump(p0, i2, p1)        
        """
        self.optimize_loop(ops, expected, preamble)

class TestLLtype(OptimizeOptTest, LLtypeMixin):
    pass
