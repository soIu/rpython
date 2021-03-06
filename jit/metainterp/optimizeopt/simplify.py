from rpython.jit.metainterp.optimizeopt.optimizer import Optimization
from rpython.jit.metainterp.optimizeopt.util import make_dispatcher_method
from rpython.jit.metainterp.resoperation import ResOperation, rop
from rpython.jit.metainterp.history import TargetToken, JitCellToken

class OptSimplify(Optimization):
    def __init__(self, unroll):
        self.last_label_descr = None
        self.unroll = unroll

    def emit_operation(self, op):
        if op.is_guard():
            if self.optimizer.pendingfields is None:
                self.optimizer.pendingfields = []
        Optimization.emit_operation(self, op)

    def optimize_CALL_PURE(self, op):
        args = op.getarglist()
        self.emit_operation(ResOperation(rop.CALL, args, op.result,
                                         op.getdescr()))

    def optimize_CALL_LOOPINVARIANT(self, op):
        op = op.copy_and_change(rop.CALL)
        self.emit_operation(op)

    def optimize_VIRTUAL_REF_FINISH(self, op):
        pass

    def optimize_VIRTUAL_REF(self, op):
        op = ResOperation(rop.SAME_AS, [op.getarg(0)], op.result)
        self.emit_operation(op)

    def optimize_QUASIIMMUT_FIELD(self, op):
        # xxx ideally we could also kill the following GUARD_NOT_INVALIDATED
        #     but it's a bit hard to implement robustly if heap.py is also run
        pass

    def optimize_RECORD_KNOWN_CLASS(self, op):
        pass

    def optimize_LABEL(self, op):
        if not self.unroll:
            descr = op.getdescr()
            if isinstance(descr, JitCellToken):
                return self.optimize_JUMP(op.copy_and_change(rop.JUMP))
            self.last_label_descr = op.getdescr()
        self.emit_operation(op)

    def optimize_JUMP(self, op):
        if not self.unroll:
            op = op.clone()
            descr = op.getdescr()
            assert isinstance(descr, JitCellToken)
            if not descr.target_tokens:
                assert self.last_label_descr is not None
                target_token = self.last_label_descr
                assert isinstance(target_token, TargetToken)
                assert target_token.targeting_jitcell_token is descr
                op.setdescr(self.last_label_descr)
            else:
                assert len(descr.target_tokens) == 1
                op.setdescr(descr.target_tokens[0])
        self.emit_operation(op)

    def optimize_GUARD_FUTURE_CONDITION(self, op):
        pass

dispatch_opt = make_dispatcher_method(OptSimplify, 'optimize_',
        default=OptSimplify.emit_operation)
OptSimplify.propagate_forward = dispatch_opt
