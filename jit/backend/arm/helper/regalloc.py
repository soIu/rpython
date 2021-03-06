from rpython.jit.backend.arm import conditions as c
from rpython.jit.backend.arm import registers as r
from rpython.jit.metainterp.history import ConstInt, BoxInt, Box, FLOAT
from rpython.jit.metainterp.history import ConstInt
from rpython.rlib.objectmodel import we_are_translated

VMEM_imm_size=0x3FC
default_imm_size=0xFF

def check_imm_arg(arg, size=default_imm_size, allow_zero=True):
    assert not isinstance(arg, ConstInt)
    if not we_are_translated():
        if not isinstance(arg, int):
            import pdb; pdb.set_trace()
    i = arg
    if allow_zero:
        lower_bound = i >= 0
    else:
        lower_bound = i > 0
    return i <= size and lower_bound

def check_imm_box(arg, size=0xFF, allow_zero=True):
    if isinstance(arg, ConstInt):
        return check_imm_arg(arg.getint(), size, allow_zero)
    return False


def prepare_op_ri(name=None, imm_size=0xFF, commutative=True, allow_zero=True):
    def f(self, op, fcond):
        assert fcond is not None
        a0 = op.getarg(0)
        a1 = op.getarg(1)
        boxes = list(op.getarglist())
        imm_a0 = check_imm_box(a0, imm_size, allow_zero=allow_zero)
        imm_a1 = check_imm_box(a1, imm_size, allow_zero=allow_zero)
        if not imm_a0 and imm_a1:
            l0 = self.make_sure_var_in_reg(a0)
            l1 = self.convert_to_imm(a1)
        elif commutative and imm_a0 and not imm_a1:
            l1 = self.convert_to_imm(a0)
            l0 = self.make_sure_var_in_reg(a1, boxes)
        else:
            l0 = self.make_sure_var_in_reg(a0, boxes)
            l1 = self.make_sure_var_in_reg(a1, boxes)
        self.possibly_free_vars_for_op(op)
        self.free_temp_vars()
        res = self.force_allocate_reg(op.result, boxes)
        return [l0, l1, res]
    if name:
        f.__name__ = name
    return f

def prepare_float_op(name=None, base=True, float_result=True, guard=False):
    if guard:
        def f(self, op, guard_op, fcond):
            locs = []
            loc1 = self.make_sure_var_in_reg(op.getarg(0))
            locs.append(loc1)
            if base:
                loc2 = self.make_sure_var_in_reg(op.getarg(1))
                locs.append(loc2)
            self.possibly_free_vars_for_op(op)
            self.free_temp_vars()
            if guard_op is None:
                res = self.force_allocate_reg(op.result)
                assert float_result == (op.result.type == FLOAT)
                locs.append(res)
                return locs
            else:
                args = self._prepare_guard(guard_op, locs)
                return args
    else:
        def f(self, op, fcond):
            locs = []
            loc1 = self.make_sure_var_in_reg(op.getarg(0))
            locs.append(loc1)
            if base:
                loc2 = self.make_sure_var_in_reg(op.getarg(1))
                locs.append(loc2)
            self.possibly_free_vars_for_op(op)
            self.free_temp_vars()
            res = self.force_allocate_reg(op.result)
            assert float_result == (op.result.type == FLOAT)
            locs.append(res)
            return locs
    if name:
        f.__name__ = name
    return f

def prepare_op_by_helper_call(name):
    def f(self, op, fcond):
        assert fcond is not None
        a0 = op.getarg(0)
        a1 = op.getarg(1)
        arg1 = self.rm.make_sure_var_in_reg(a0, selected_reg=r.r0)
        arg2 = self.rm.make_sure_var_in_reg(a1, selected_reg=r.r1)
        assert arg1 == r.r0
        assert arg2 == r.r1
        if isinstance(a0, Box) and self.stays_alive(a0):
            self.force_spill_var(a0)
        self.possibly_free_vars_for_op(op)
        self.free_temp_vars()
        self.after_call(op.result)
        self.possibly_free_var(op.result)
        return []
    f.__name__ = name
    return f

def prepare_cmp_op(name=None):
    def f(self, op, guard_op, fcond):
        assert fcond is not None
        boxes = list(op.getarglist())
        arg0, arg1 = boxes
        imm_a1 = check_imm_box(arg1)

        l0 = self.make_sure_var_in_reg(arg0, forbidden_vars=boxes)
        if imm_a1:
            l1 = self.convert_to_imm(arg1)
        else:
            l1 = self.make_sure_var_in_reg(arg1, forbidden_vars=boxes)

        self.possibly_free_vars_for_op(op)
        self.free_temp_vars()
        if guard_op is None:
            res = self.force_allocate_reg(op.result)
            return [l0, l1, res]
        else:
            args = self._prepare_guard(guard_op, [l0, l1])
            return args
    if name:
        f.__name__ = name
    return f

def prepare_op_unary_cmp(name=None):
    def f(self, op, guard_op, fcond):
        assert fcond is not None
        a0 = op.getarg(0)
        assert isinstance(a0, Box)
        reg = self.make_sure_var_in_reg(a0)
        self.possibly_free_vars_for_op(op)
        if guard_op is None:
            res = self.force_allocate_reg(op.result, [a0])
            return [reg, res]
        else:
            return self._prepare_guard(guard_op, [reg])
    if name:
        f.__name__ = name
    return f
