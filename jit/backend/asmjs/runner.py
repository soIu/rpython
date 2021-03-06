
from rpython.rlib.unroll import unrolling_iterable
from rpython.rtyper.lltypesystem import lltype, llmemory, rffi
from rpython.rtyper.lltypesystem.lloperation import llop
from rpython.rtyper.llinterp import LLInterpreter
from rpython.jit.backend.llsupport.llmodel import AbstractLLCPU
from rpython.jit.metainterp import history

from rpython.jit.backend.asmjs import support
from rpython.jit.backend.asmjs.assembler import (AssemblerASMJS,
                                                 CompiledLoopTokenASMJS)
from rpython.jit.backend.asmjs.arch import (WORD,
                                            SANITYCHECK,
                                            JITFRAME_FIXED_SIZE)


class CPU_ASMJS(AbstractLLCPU):
    """Quote-unquote 'CPU' for emitting and running asmjs javascript.

    This inherits from AbstractLLCPU because it interfaces with the rest of
    the interpreter in the same way, via low-level descrs.  But the details
    of compiling and running a trace are very very different.  Rather than
    mapping executable memory and jumping into it, we build up javascript
    source code and call external functions to compile and invoke it.
    """

    IS_64_BIT = False
    JITFRAME_FIXED_SIZE = JITFRAME_FIXED_SIZE
    supports_floats = True
    supports_singlefloats = False
    supports_longlong = False
    with_threads = False
    backend_name = "asmjs"

    def __init__(self, rtyper, stats, opts=None, translate_support_code=False,
                 gcdescr=None):
        AbstractLLCPU.__init__(self, rtyper, stats, opts,
                               translate_support_code, gcdescr)

    def set_debug(self, flag):
        return self.assembler.set_debug(flag)

    def get_failargs_limit(self):
        if self.opts is not None:
            return self.opts.failargs_limit
        else:
            return 1000

    def setup(self):
        self.assembler = AssemblerASMJS(self)

    def setup_once(self):
        self.assembler.setup_once()

    def finish_once(self):
        self.assembler.finish_once()

    def make_execute_token(self, *ARGS):
        """Build and return a function for executing the given JIT token.

        Each chunk of compiled code is represented by an integer "function id".
        We need to look up the id, build the necessary frame, and then call the
        helper function "jitInvoke" to execute the compiled function.
        """
        #  This is mostly copied from llsupport/llmodel.py, but with changes
        #  to invoke the external javascript helper thingy.
        lst = [(i, history.getkind(ARG)[0]) for i, ARG in enumerate(ARGS)]
        kinds = unrolling_iterable(lst)

        def execute_token(executable_token, *args):
            clt = executable_token.compiled_loop_token
            assert isinstance(clt, CompiledLoopTokenASMJS)
            funcid = clt.func.compiled_funcid
            loopid = clt.compiled_loopid
            frame_info = clt.func.frame_info
            frame = self.gc_ll_descr.malloc_jitframe(frame_info)
            ll_frame = lltype.cast_opaque_ptr(llmemory.GCREF, frame)
            locs = clt._ll_initial_locs
            if SANITYCHECK:
                assert len(locs) == len(args)
            if not self.translate_support_code:
                prev_interpreter = LLInterpreter.current_interpreter
                LLInterpreter.current_interpreter = self.debug_ll_interpreter
            try:
                # Store each argument into the frame.
                for i, kind in kinds:
                    arg = args[i]
                    num = locs[i]
                    if kind == history.INT:
                        self.set_int_value(ll_frame, num, arg)
                    elif kind == history.FLOAT:
                        self.set_float_value(ll_frame, num, arg)
                    else:
                        assert kind == history.REF
                        self.set_ref_value(ll_frame, num, arg)
                llop.gc_writebarrier(lltype.Void, ll_frame)
                # Send the threadlocaladdr.
                if self.translate_support_code:
                    ll_tlref = llop.threadlocalref_addr(
                        llmemory.Address)
                else:
                    ll_tlref = rffi.cast(llmemory.Address,
                        self._debug_errno_container)
                # Invoke it via the helper.
                ll_frameadr = self.cast_ptr_to_int(ll_frame)
                ll_tladdr = self.cast_adr_to_int(ll_tlref)
                ll_frameadr = support.jitInvoke(funcid, ll_frameadr, ll_tladdr, loopid)
                ll_frame = self.cast_int_to_ptr(ll_frameadr, llmemory.GCREF)
            finally:
                if not self.translate_support_code:
                    LLInterpreter.current_interpreter = prev_interpreter
            return ll_frame

        return execute_token

    def compile_bridge(self, faildescr, inputargs, operations,
                       original_loop_token, log=True, logger=None):
        clt = original_loop_token.compiled_loop_token
        clt.compiling_a_bridge()
        return self.assembler.assemble_bridge(faildescr, inputargs, operations,
                                              original_loop_token, log=log)

    def free_loop_and_bridges(self, compiled_loop_token):
        AbstractLLCPU.free_loop_and_bridges(self, compiled_loop_token)
        self.assembler.free_loop_and_bridges(compiled_loop_token)

    def cast_ptr_to_int(x):
        adr = llmemory.cast_ptr_to_adr(x)
        return CPU_ASMJS.cast_adr_to_int(adr)
    cast_ptr_to_int._annspecialcase_ = 'specialize:arglltype(0)'
    cast_ptr_to_int = staticmethod(cast_ptr_to_int)

    def redirect_call_assembler(self, oldlooptoken, newlooptoken):
        self.assembler.redirect_call_assembler(oldlooptoken, newlooptoken)

    def invalidate_loop(self, looptoken):
        self.assembler.invalidate_loop(looptoken)

    def _decode_pos(self, deadframe, index):
        descr = self.get_latest_descr(deadframe)
        if descr.final_descr:
            assert index == 0
            return 0
        return descr._asmjs_faillocs[index]
