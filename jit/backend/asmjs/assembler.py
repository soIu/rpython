
import os
import time

from rpython.rlib import rgc
from rpython.rlib.rarithmetic import r_uint, intmask
from rpython.rlib.objectmodel import we_are_translated
from rpython.jit.backend.llsupport import symbolic, jitframe, rewrite, llerrno
from rpython.jit.backend.llsupport.assembler import BaseAssembler
from rpython.jit.backend.llsupport.regalloc import compute_vars_longevity
from rpython.jit.backend.llsupport.descr import (unpack_fielddescr,
                                                 unpack_arraydescr,
                                                 unpack_interiorfielddescr,
                                                 ArrayDescr, CallDescr,
                                                 FieldDescr)
from rpython.jit.codewriter import longlong
from rpython.jit.codewriter.effectinfo import EffectInfo
from rpython.rtyper.lltypesystem import lltype, llmemory, rffi, rstr
from rpython.rtyper.annlowlevel import cast_instance_to_gcref, llhelper
from rpython.jit.backend.model import CompiledLoopToken
from rpython.jit.metainterp.resoperation import rop, ResOperation
from rpython.jit.metainterp.history import (AbstractFailDescr, ConstInt,
                                            Box, TargetToken, BoxInt,
                                            INT, REF, FLOAT, HOLE,
                                            JitCellToken)

from rpython.jit.backend.asmjs import support
from rpython.jit.backend.asmjs import jsvalue as js
from rpython.jit.backend.asmjs.arch import WORD, SANITYCHECK
from rpython.jit.backend.asmjs.jsbuilder import ASMJSBuilder


GILFUNCPTR = lltype.Ptr(lltype.FuncType([], lltype.Void))


@rgc.no_collect
def release_gil_shadowstack():
    before = rffi.aroundstate.before
    if before:
        before()


@rgc.no_collect
def reacquire_gil_shadowstack():
    after = rffi.aroundstate.after
    if after:
        after()


# A one-word slot in memory that can be written into to invalidate jitted code.
# The code checks this flag in GUARD_NOT_INVALIDATED, in leiu of the ability
# to patch the existing code.
INVALIDATION = lltype.Struct(
    "INVALIDATION",
    ("counter", lltype.Signed)
)
INVALIDATION_PTR = lltype.Ptr(INVALIDATION)


class AssemblerASMJS(BaseAssembler):
    """Class for assembling a Trace into a compiled ASMJS function."""

    def setup_once(self):
        BaseAssembler.setup_once(self)
        release_gil_func = llhelper(GILFUNCPTR,
                                    release_gil_shadowstack)
        self.release_gil_addr = self.cpu.cast_ptr_to_int(release_gil_func)
        reacquire_gil_func = llhelper(GILFUNCPTR,
                                      reacquire_gil_shadowstack)
        self.reacquire_gil_addr = self.cpu.cast_ptr_to_int(reacquire_gil_func)
        gc_ll_descr = self.cpu.gc_ll_descr
        gc_ll_descr.initialize()
        if gc_ll_descr.get_malloc_slowpath_addr is not None:
            d = gc_ll_descr
            self.gc_malloc_nursery_addr = d.get_malloc_slowpath_addr()
            self.gc_malloc_array_addr = d.get_malloc_slowpath_array_addr()
        if hasattr(gc_ll_descr, "malloc_str"):
            nm = "malloc_str"
            self.gc_malloc_str_addr = gc_ll_descr.get_malloc_fn_addr(nm)
        else:
            self.gc_malloc_str_addr = None
        if hasattr(gc_ll_descr, "malloc_unicode"):
            nm = "malloc_unicode"
            self.gc_malloc_unicode_addr = gc_ll_descr.get_malloc_fn_addr(nm)
        else:
            self.gc_malloc_unicode_addr = None

    def finish_once(self):
        pass

    def setup(self, looptoken):
        BaseAssembler.setup(self, looptoken)

    def teardown(self):
        pass

    def _build_failure_recovery(self, exc, withfloats=False):
        # stub method for BaseAssembler interface
        pass

    def _build_wb_slowpath(self, withcards, withfloats=False, for_frame=False):
        # stub method for BaseAssembler interface
        pass

    def _build_malloc_slowpath(self, kind):
        # stub method for BaseAssembler interface
        pass

    def build_frame_realloc_slowpath(self):
        # stub method for BaseAssembler interface
        pass

    def _build_propagate_exception_path(self):
        # stub method for BaseAssembler interface
        pass

    def _build_cond_call_slowpath(self, supports_floats, callee_only):
        # stub method for BaseAssembler interface
        pass

    def _build_stack_check_slowpath(self):
        # stub method for BaseAssembler interface
        pass

    def _build_release_gil(self, gcrootmap):
        # stub method for BaseAssembler interface
        pass

    def assemble_loop(self, jd_id, unique_id, logger, loopname, inputargs,
                      operations, looptoken, log):
        """Assemble and compile a new loop function from the given trace."""
        assert not self.cpu.HAS_CODEMAP
        #os.write(2, "ASSEMBLE LOOP START %f\n" % (time.time(),))
        # Build a new func holding this new loop.
        func = CompiledFuncASMJS(self)
        clt = CompiledLoopTokenASMJS(self, func, looptoken.number)
        looptoken.compiled_loop_token = clt
        self.setup(looptoken)
        clt.add_code_to_loop(operations, inputargs)
        clt._ll_initial_locs = clt.compiled_blocks[0].inputlocs
        self.teardown()
        # If it jumps to an existing loop, merge with that func.
        # If not, compile it afresh.
        final_op = operations[-1]
        if final_op.getopnum() != rop.JUMP:
            func.reassemble()
        else:
            descr = final_op.getdescr()
            assert isinstance(descr, TargetToken)
            target_block = descr._asmjs_block
            if target_block.clt.func is func:
                func.reassemble()
            else:
                target_block.clt.func.merge_with(func)
        #os.write(2, "ASSEMBLE LOOP END %f\n" % (time.time(),))

    def assemble_bridge(self, faildescr, inputargs, operations,
                        original_loop_token, log):
        """Assemble, compile and link a new bridge from the given trace."""
        #os.write(2, "ASSEMBLE BRIDGE START %f\n" % (time.time(),))
        assert isinstance(faildescr, AbstractFailDescr)
        # Merge the new operations into the existing loop.
        self.setup(original_loop_token)
        clt = original_loop_token.compiled_loop_token
        clt.add_code_to_loop(operations, inputargs, faildescr)
        self.teardown()
        # If it jumps to a loop in a different func, merge with that func.
        # If not, recompile just the modified func.
        final_op = operations[-1]
        if final_op.getopnum() != rop.JUMP:
            clt.func.reassemble()
        else:
            descr = final_op.getdescr()
            assert isinstance(descr, TargetToken)
            target_block = descr._asmjs_block
            if target_block.clt.func is clt.func:
                clt.func.reassemble()
            else:
                target_block.clt.func.merge_with(clt.func)
        #os.write(2, "ASSEMBLE BRIDGE END %f\n" % (time.time(),))

    def redirect_call_assembler(self, oldlooptoken, newlooptoken):
        #os.write(2, "ASSEMBLE REDIRECT START %f\n" % (time.time(),))
        oldclt = oldlooptoken.compiled_loop_token
        newclt = newlooptoken.compiled_loop_token
        oldclt.redirect_loop(newclt)
        if oldclt.func is newclt.func:
            oldclt.func.reassemble()
        else:
            newclt.func.merge_with(oldclt.func)
        #os.write(2, "ASSEMBLE REDIRECT END %f\n" % (time.time(),))

    def free_loop_and_bridges(self, compiled_loop_token):
        # All freeing is taken care of in the various destructors.
        # We just need to unlink it from the containing function.
        func = compiled_loop_token.func
        func.remove_loop(compiled_loop_token)

    def invalidate_loop(self, looptoken):
        looptoken.compiled_loop_token.invalidate_loop()


class CompiledFuncASMJS(object):
    """A top-level compiled function for the asmjs backend.

    This object holds the state for a single jit-compiled function, which
    may contain one or more loops that mutually jump between each other.
    Combining all local jumps into a single function reduces the overhead
    of dispatching between loops, at the cost of increased jit-compilation
    overhead.
    """

    def __init__(self, assembler):
        self.assembler = assembler
        self.cpu = assembler.cpu
        self.compiled_funcid = support.jitReserve()
        self.compiled_loops = []
        self.compiled_blocks = []
        self.num_removed_loops = 0
        self.merged_from = None
        self.merged_into = None
        frame_info = lltype.malloc(jitframe.JITFRAMEINFO, flavor="raw")
        self.frame_info = rffi.cast(jitframe.JITFRAMEINFOPTR, frame_info)
        self.frame_info.clear()
        self.ensure_frame_depth(0)

    def free(self):
        lltype.free(self.frame_info, flavor="raw")
        support.jitFree(self.compiled_funcid)

    def merge_with(self, other):
        """Merge loops from two functions into a single unified function.

        This method must be called when we discover two previously-independent
        sets of loops that can now jump to each other.  It combines the loops
        of two existing functions into a single function, and arranges for the
        other function to redirect all its calls into the merged version.
        """
        if SANITYCHECK:
            assert self.merged_into is None
            assert other.merged_into is None
            assert other is not self
        # Merge into the one with the lowest funcid, as it's more likely
        # to have compiled jumps in it that we won't have to redirect.
        if self.compiled_funcid > other.compiled_funcid:
            res_func = other
            src_func = self
        else:
            res_func = self
            src_func = other
        if res_func.merged_from is None:
            res_func.merged_from = []
        # Merge all the loops into the selected result function.
        res_func.merged_from.append(src_func)
        res_func.ensure_frame_depth(src_func.frame_info.jfi_frame_depth * WORD)
        for clt in src_func.compiled_loops:
            clt.func = res_func
            clt.compiled_loopid = len(res_func.compiled_loops)
            clt.frame_info = res_func.frame_info
            res_func.compiled_loops.append(clt)
        for block in src_func.compiled_blocks:
            block.compiled_blockid = len(res_func.compiled_blocks)
            res_func.compiled_blocks.append(block)
        res_func.reassemble()
        # Mark the source function as being merged.
        src_func.merged_into = res_func
        src_func.reassemble()
        return res_func

    def remove_loop(self, clt):
        if SANITYCHECK:
            assert self.compiled_loops[clt.compiled_loopid] is clt
            for block in clt.compiled_blocks:
                assert self.compiled_blocks[block.compiled_blockid] is block
        self.compiled_loops[clt.compiled_loopid] = None
        clt.func = None
        for block in clt.compiled_blocks:
            self.compiled_blocks[block.compiled_blockid] = None
            block.free()
        self.num_removed_loops += 1
        if self.num_removed_loops < len(self.compiled_loops):
            self.reassemble()
        else:
            # Nothing is left referencing this function.
            self.free()

    def ensure_frame_depth(self, required_offset):
        if SANITYCHECK:
            assert required_offset >= 0
            assert required_offset % WORD == 0
        required_depth = intmask(r_uint(required_offset // WORD))
        baseofs = self.cpu.get_baseofs_of_frame_field()
        self.frame_info.update_frame_depth(baseofs, required_depth)

    def reassemble(self):
        """Re-compile the jitted asmjs function for this function.

        The generated code consists of a short header to load input arguments
        and sanity-check a few things, followed by the relooped code for
        all the contained blocks.
        """
        bldr = ASMJSBuilder(self.cpu)
        if self.merged_into is not None:
            # This function has been merged into some other function.
            # Dispatch each loop to its new location via a simple switch.
            bldr.emit_comment("DISPATCH TO MERGED FUNCTION")
            if len(self.compiled_loops) == 1:
                clt = self.compiled_loops[0]
                assert clt is not None
                funcid = js.ConstInt(clt.func.compiled_funcid)
                loopid = js.ConstInt(clt.compiled_loopid)
                callargs = [funcid, js.frame, js.tladdr, loopid]
                call = js.CallFunc("jitInvoke", callargs)
                bldr.emit_assignment(js.frame, call)
                bldr.emit_exit()
            else:
                with bldr.emit_switch_block(js.label):
                    for i in xrange(len(self.compiled_loops)):
                        clt = self.compiled_loops[i]
                        if clt is not None:
                            with bldr.emit_case_block(js.ConstInt(i)):
                                funcid = js.ConstInt(clt.func.compiled_funcid)
                                loopid = js.ConstInt(clt.compiled_loopid)
                                callargs = [funcid, js.frame, js.tladdr, loopid]
                                call = js.CallFunc("jitInvoke", callargs)
                                bldr.emit_assignment(js.frame, call)
                                bldr.emit_exit()
        else:
            # We check the depth of the frame at entry to the function.
            # If it's too small then we rellocate it via a helper.
            req_depth = js.ConstInt(self.frame_info.jfi_frame_depth)
            cur_depth = js.HeapData(js.Int32, js.FrameSizeAddr())
            frame_too_small = js.LessThan(cur_depth, req_depth)
            bldr.emit_comment("CHECK FRAME DEPTH")
            with bldr.emit_if_block(frame_too_small):
                # We must store a gcmap to prevent input args from being gc'd.
                # The layout of input args depends on the target loop.
                with bldr.emit_switch_block(js.label):
                    for clt in self.compiled_loops:
                        loopid = js.ConstInt(clt.compiled_loopid)
                        with bldr.emit_case_block(loopid):
                            clt.emit_store_initial_gcmap(bldr)
                # Now we can call the helper function.
                # There might be an exception active, which must be preserved.
                reallocfn = js.ConstInt(self.cpu.realloc_frame)
                args = [js.frame, req_depth]
                with ctx_preserve_exception(self, bldr):
                    newframe = js.DynCallFunc("iii", reallocfn, args)
                    bldr.emit_assignment(js.frame, newframe)
            # Load input args for the loop being entered,
            # and convert from loopid to blockid
            bldr.emit_comment("LOAD INPUT ARGS")
            if len(self.compiled_loops) == 1:
                clt = self.compiled_loops[0]
                assert clt is not None
                clt.emit_load_arguments(bldr)
                clt.emit_set_initial_blockid(bldr)
            else:
                with bldr.emit_switch_block(js.label):
                    for clt in self.compiled_loops:
                        if clt is not None:
                            loopid = js.ConstInt(clt.compiled_loopid)
                            with bldr.emit_case_block(loopid):
                                clt.emit_load_arguments(bldr)
                                clt.emit_set_initial_blockid(bldr)
            # Generate the relooped body from all loop blocks.
            # XXX TODO: find a way to avoid re-doing all this work
            # each time we add a new block.
            #os.write(2, "ASSEMBLER RELOOP START %f\n" % (time.time(),))
            blocks = {}
            entries = []
            for clt in self.compiled_loops:
                if clt is not None and clt.redirected_to is None:
                    entries.append(clt.compiled_blocks[0].compiled_blockid)
                    for block in clt.compiled_blocks:
                        blocks[block.compiled_blockid] = block
            self.reloop_state = []
            self.emit_relooped_blocks(bldr, entries, blocks)
            self.reloop_state = None
            #os.write(2, "ASSEMBLER RELOOP END %f\n" % (time.time(),))
            # Always exit by returning the frame.
            bldr.emit_exit()

        # Compile the replacement source code for our function.
        jssrc = bldr.finish()
        #os.write(2, "=-=-=-= COMPILED\n")
        #os.write(2, jssrc)
        #os.write(2, "\n=-=-=-=-=-=-=-=-\n")
        #os.write(2, "ASSEMBLER COMPILE START %f\n" % (time.time(),))
        support.jitRecompile(self.compiled_funcid, jssrc)
        #os.write(2, "ASSEMBLER COMPILE END %f\n" % (time.time(),))

    def emit_relooped_blocks(self, bldr, entries, blocks):
        if not entries:
            return
        # See which entry blocks need a path looping back to them.
        needs_loop = {}
        for entry1 in entries:
            for entry2 in entries:
                if self._block_needs_path(entry2, entry1):
                    needs_loop[entry1] = True
        # Create a simple block if we can.
        if len(entries) == 1 and len(needs_loop) == 0:
            self._emit_simple_block(bldr, entries, blocks)
            return
        # Create a multiple block if we can split unique entry subsets.
        unique_entries = {}
        for entry in entries:
            if entry not in needs_loop:
                unique_entries[entry] = {}
        if unique_entries:
            self._emit_multiple_block(bldr, entries, blocks, unique_entries)
            return
        # Create a loop block as a last resort.
        self._emit_loop_block(bldr, entries, blocks)

    def _push_reloop_state(self, here_entries, there_entries, there_blocks):
        label = "L%d" % (len(self.reloop_state),)
        self.reloop_state.append((label, here_entries, there_entries, there_blocks))
        return label

    def _pop_reloop_state(self):
        _l, _e, there_entries, there_blocks = self.reloop_state.pop()
        return there_entries, there_blocks 

    def _emit_simple_block(self, bldr, entries, blocks):
        entry_block = blocks.pop(entries[0])
        label = self._push_reloop_state([], [], blocks)
        with bldr.emit_do_block(js.zero, label):
            entry_block.emit_body(bldr)
        next_entries, next_blocks = self._pop_reloop_state()
        self.emit_relooped_blocks(bldr, next_entries, next_blocks)

    def _emit_multiple_block(self, bldr, entries, blocks, unique_entries):
        # Find the blocks that are unique to each entry.
        for block in blocks.keys():
            unique_entry = -1
            for entry in entries:
                if self._block_needs_path(entry, block):
                    if unique_entry >= 0:
                        unique_entry = -1
                        break
                    unique_entry = entry
            if unique_entry >= 0 and unique_entry in unique_entries:
                unique_entries[unique_entry][block] = blocks.pop(block)
        other_entries = []
        for entry in entries:
            if entry in unique_entries:
                if entry in blocks:
                    unique_entries[entry][entry] = blocks.pop(entry)
            else:
                other_entries.append(entry)
        # Emit code to switch between the unique entries.
        label = self._push_reloop_state([], other_entries, blocks)
        if len(unique_entries) == 1:
            for entry in unique_entries:
                blockid = js.ConstInt(entry)
                unique_blocks = unique_entries[entry]
                with bldr.emit_if_block(js.Equal(js.label, blockid)):
                    with bldr.emit_do_block(js.zero, label):
                        self.emit_relooped_blocks(bldr, [entry], unique_blocks)
        else:
            with bldr.emit_switch_block(js.label, label):
                for entry in unique_entries:
                    with bldr.emit_case_block(js.ConstInt(entry)):
                        unique_blocks = unique_entries[entry]
                        self.emit_relooped_blocks(bldr, [entry], unique_blocks)
        next_entries, next_blocks = self._pop_reloop_state()
        self.emit_relooped_blocks(bldr, next_entries, next_blocks)

    def _emit_loop_block(self, bldr, entries, blocks):
        # Gather all the blocks that must be inside the loop.
        # XXX TODO: somehow do this by traversing forwards from the entries?
        inner_blocks = {}
        for entry in entries:
            inner_blocks[entry] = blocks.pop(entry)
        for block in blocks.keys():
            for entry in entries:
                if self._block_needs_path(block, entry):
                    inner_blocks[block] = blocks.pop(block)
                    break
        # Now we can emit the loop.
        # We've pushed the entries to the reloop state, so the reachability
        # calculation will differ when we recurse into emit_relooped_blocks.
        # This is very important to prevent an infinite loop!
        label = self._push_reloop_state(entries, [], blocks)
        with bldr.emit_while_block(js.true, label):
            self.emit_relooped_blocks(bldr, entries, inner_blocks)
        next_entries, next_blocks = self._pop_reloop_state()
        self.emit_relooped_blocks(bldr, next_entries, next_blocks)

    def emit_jump(self, bldr, blockid):
        i = len(self.reloop_state) - 1
        while i >= 0:
            # XXX TODO: use dicts of entries for faster lookup?
            label, here_entries, there_entries, blocks = self.reloop_state[i]
            if blockid in here_entries:
                bldr.emit_assignment(js.label, js.ConstInt(blockid))
                bldr.emit_continue(label)
                break
            if blockid in blocks:
                block = self.compiled_blocks[blockid]
                # Inline-emit guards, since they have unique predecessor.
                # XXX TODO: clean up this hack by directly attaching them to
                # the owning block, rather than making them a separate block.
                if isinstance(block.intoken, AbstractFailDescr):
                    block.emit_body(bldr)
                    blocks.pop(blockid)
                    break
                if blockid not in there_entries:
                    there_entries.append(blockid)
                bldr.emit_assignment(js.label, js.ConstInt(blockid))
                bldr.emit_break(label)
                break
            i -= 1
        else:
            assert False, "should never reach here"

    def _block_needs_path(self, src_blockid, dst_blockid):
        # XXX TODO: it is tremendously inefficient to recalculate
        # this over and over again, but it needs to be dynamic
        # because of the changing set of blocks that have a path.
        seen = {}
        src_block = self.compiled_blocks[src_blockid]
        queue = []
        for succ in self._block_successors(src_block):
            queue.append(succ)
        while queue:
            block = queue.pop()
            blockid = block.compiled_blockid
            if blockid in seen:
                continue
            seen[blockid] = True
            if self._block_has_path(blockid):
                continue
            if blockid == dst_blockid:
                return True
            for succ in self._block_successors(block):
                queue.append(succ)
        return False

    def _block_has_path(self, blockid):
        i = len(self.reloop_state) - 1
        while i >= 0:
            label, here_entries, there_entries, blocks = self.reloop_state[i]
            if blockid in here_entries:
                return True
            if blockid in there_entries:
                return True
            i -= 1
        return False

    def _block_successors(self, block):
        outtoken = block.outtoken
        if outtoken is not None:
            assert isinstance(outtoken, TargetToken)
            if outtoken._asmjs_block is not None:
                yield outtoken._asmjs_block
        for guardtoken in block.guardtokens:
            if guardtoken._asmjs_block is not None:
                yield guardtoken._asmjs_block


class CompiledLoopTokenASMJS(CompiledLoopToken):
    """CompiledLoopToken with extra fields for asmjs backend."""

    def __init__(self, assembler, func, number):
        CompiledLoopToken.__init__(self, assembler.cpu, number)
        self.assembler = assembler
        self.cpu = assembler.cpu
        self.func = func
        self.compiled_loopid = len(func.compiled_loops)
        self.frame_info = func.frame_info
        func.compiled_loops.append(self)
        self.redirected_to = None
        self.compiled_blocks = []
        self.inlined_gcrefs = []
        invalidationptr = lltype.malloc(INVALIDATION, flavor="raw")
        self.invalidation = rffi.cast(INVALIDATION_PTR, invalidationptr)
        self.invalidation.counter = 0

    def __del__(self):
        CompiledLoopToken.__del__(self)
        lltype.free(self.invalidation, flavor="raw")

    def add_code_to_loop(self, operations, inputargs, intoken=None):
        # Re-write to use lower-level GC operations,
        # and record any inlined GC refs to the CLT.
        gcrefs = self.inlined_gcrefs
        gcdescr = self.cpu.gc_ll_descr
        operations = gcdescr.rewrite_assembler(self.cpu, operations, gcrefs)
        # Split the new operations up into labelled blocks.
        start_op = 0
        first_new_block = len(self.compiled_blocks)
        guardtokens = []
        for i in xrange(len(operations)):
            op = operations[i]
            # Guard descrs need a slot on which to be told the current block.
            # It is initially None but references the block when bridged.
            if op.is_guard():
                faildescr = op.getdescr()
                assert isinstance(faildescr, AbstractFailDescr)
                faildescr._asmjs_block = None
                guardtokens.append(faildescr)
            # Label descrs start a new block.
            elif op.getopnum() == rop.LABEL:
                labeldescr = op.getdescr()
                assert isinstance(labeldescr, TargetToken)
                # Make the preceding operations into a block.
                # NB: if the first op is a label, this makes an empty block.
                # That's OK for now; it might do some arg shuffling etc.
                new_block = CompiledBlockASMJS(
                    self, len(self.func.compiled_blocks),
                    operations[start_op:i], intoken, inputargs,
                    guardtokens, labeldescr, op.getarglist(),
                )
                self.compiled_blocks.append(new_block)
                self.func.compiled_blocks.append(new_block)
                # Start a new block from this label.
                start_op = i
                intoken = labeldescr
                inputargs = op.getarglist()
                guardtokens = []
        # Make the final block, assuming we didn't end at a label.
        if start_op < len(operations):
            if operations[-1].getopnum() == rop.JUMP:
                outtoken = operations[-1].getdescr()
                outputargs = operations[-1].getarglist()
            else:
                outtoken = None
                outputargs = []
            new_block = CompiledBlockASMJS(
                self, len(self.func.compiled_blocks), operations[start_op:],
                intoken, inputargs, guardtokens, outtoken, outputargs
            )
            self.compiled_blocks.append(new_block)
            self.func.compiled_blocks.append(new_block)
        # Generate the new code.
        for i in xrange(first_new_block, len(self.compiled_blocks)):
            self.compiled_blocks[i].generate_code()

    def invalidate_loop(self):
        self.invalidation.counter += 1

    def redirect_loop(self, newclt):
        self.redirected_to = newclt
        # XXX TODO: don't generate code for blocks that are no longer needed
        #for block in self.compiled_blocks:
        #    self.func.compiled_blocks[block.compiled_blockid] = None

    def emit_store_initial_gcmap(self, bldr):
        gcmap = self.compiled_blocks[0].initial_gcmap
        gcmapref = js.ConstInt(self.cpu.cast_ptr_to_int(gcmap))
        gcmapaddr = js.FrameGCMapAddr()
        bldr.emit_store(gcmapref, gcmapaddr, js.Int32)

    def emit_load_arguments(self, bldr):
        block = self.compiled_blocks[0]
        block.emit_load_arguments(bldr)

    def emit_set_initial_blockid(self, bldr):
        target_clt = self
        while target_clt.redirected_to is not None:
            target_clt = target_clt.redirected_to
        blockid = target_clt.compiled_blocks[0].compiled_blockid
        if blockid != self.compiled_loopid:
            bldr.emit_assignment(js.label, js.ConstInt(blockid))


class CompiledBlockASMJS(object):

    def __init__(self, clt, compiled_blockid, operations,
                 intoken, inputargs, guardtokens, outtoken, outputargs):
        self.clt = clt
        self.cpu = clt.cpu
        self.compiled_blockid = compiled_blockid
        self.intoken = intoken
        self.guardtokens = guardtokens
        self.outtoken = outtoken

        # Tell the input token about its owning block.
        # XXX TODO: avoid circular references by using weakrefs?
        if intoken is not None:
            if isinstance(intoken, AbstractFailDescr):
                intoken._asmjs_block = self
            elif isinstance(intoken, TargetToken):
                intoken._asmjs_block = self

        # Remember value of invalidation counter when this block was created.
        # If it goes above this value, then GUARD_NOT_INVALIDATED fails.
        self.initial_invalidation_counter = clt.invalidation.counter

        # Calculate the locations at which our input args will appear
        # on the frame.  In the process, count how many variables of
        # each type we will need when loading them.  Also track which
        # are refs so that we can build a gcmap.
        self.inputlocs = [-1] * len(inputargs)
        self.inputkinds = [HOLE] * len(inputargs)
        reflocs = []
        offset = 0
        baseofs = self.cpu.get_baseofs_of_frame_field()
        # XXX TODO: check that it's float-size-aligned
        for i in xrange(len(inputargs)):
            box = inputargs[i]
            typ = js.HeapType.from_box(box)
            alignment = offset % typ.size
            if alignment:
                offset += typ.size - alignment
            self.inputlocs[i] = offset
            if box:
                self.inputkinds[i] = box.type
                if box.type == REF:
                    reflocs.append(offset)
            offset += typ.size
        self.clt.func.ensure_frame_depth(offset)

        # Calculate a gcmap corresponding to the initial layout of the frame.
        # This will be needed if we ever need to enlarge the frame.
        self.allocated_gcmaps = []
        if len(reflocs) == 0:
            self.initial_gcmap = jitframe.NULLGCMAP
        else:
            gcmap = self.allocate_gcmap(offset)
            for pos in reflocs:
                pos = r_uint(pos // WORD)
                gcmap[pos // WORD // 8] |= r_uint(1) << (pos % (WORD * 8))
            self.initial_gcmap = gcmap

        # Ensure the block ends with an explicit jump or return.
        # This simplifies calculation of box longevity below.
        # Also some tests don't include an explicit FINISH operation.
        FINAL_OPS = (rop.JUMP, rop.FINISH)
        if not operations or operations[-1].getopnum() not in FINAL_OPS:
            if outtoken is not None:
                jump = ResOperation(rop.JUMP, outputargs, None, descr=outtoken)
                operations.append(jump)
            else:
                finish = ResOperation(rop.FINISH, [], None)
                operations.append(finish)

        # The generated code will be an alternating sequence of ASMJSFragment
        # and Descr objects.  The former are static code, the later are either
        # guards or labels for which we need to generate different code each
        # time we're reassembled (e.g. for a new bridge, or a relocated loop).
        self.compiled_fragments = []
        self.compiled_descrs = []

        # Prepare the information we need for code generation.
        self.bldr = ASMJSBuilder(self.cpu)
        self.inputargs = inputargs
        self.operations = operations
        self.longevity, _ = compute_vars_longevity(inputargs, operations)
        self.spilled_frame_locations = {}
        self.spilled_frame_values = {}
        self.spilled_frame_offset = 0
        self.forced_spill_frame_offset = 0
        self.box_variables = {}
        self.box_expressions = {}
        self.box_expression_graveyard = {}
        self.box_variable_refcounts = {}

    def free(self):
        for gcmap in self.allocated_gcmaps:
            lltype.free(gcmap, flavor="raw")

    def allocate_gcmap(self, offset):
        length = offset // WORD
        size = (length // WORD // 8 + 1) + 1
        rawgcmap = lltype.malloc(jitframe.GCMAP, size, flavor="raw")
        self.allocated_gcmaps.append(rawgcmap)
        rffi.cast(rffi.CArrayPtr(lltype.Signed), rawgcmap)[0] = size - 1
        gcmap = rffi.cast(lltype.Ptr(jitframe.GCMAP), rawgcmap)
        for i in xrange(size - 1):
            gcmap[i] = r_uint(0)
        return gcmap

    def allocate_gcmap_from_kinds(self, kinds, framelocs):
        assert len(kinds) == len(framelocs)
        # Check whether a gcmap is actually needed.
        for i in xrange(len(kinds)):
            if kinds[i] == REF:
                break
        else:
            return jitframe.NULLGCMAP
        # Allocate and populate one appropriately.
        gcmap = self.allocate_gcmap(framelocs[-1] + WORD)
        for i in xrange(len(kinds)):
            if kinds[i] == REF:
                pos = framelocs[i] // WORD
                gcmap[pos // WORD // 8] |= r_uint(1) << (pos % (WORD * 8))
        return gcmap

    # Methods for emitting our pre-compiled fragments into the loop.
    # These are called each time the loop is re-assembled, and must not
    # use any references to artifacts from the trace (e.g. boxes).

    def emit_load_arguments(self, bldr):
        bldr.emit_comment("LOAD INPUT ARGS FOR %d" % (self.compiled_blockid,))
        inputvars = self._get_inputvars_from_kinds(self.inputkinds, bldr)
        for i in xrange(len(self.inputkinds)):
            kind = self.inputkinds[i]
            if kind != HOLE:
                pos = self.inputlocs[i]
                typ = js.HeapType.from_kind(kind)
                bldr.emit_load(inputvars[i], js.FrameSlotAddr(pos), typ)
                # XXX TODO: this is a hack to trick tests into passing.
                # Necessary because we have no JITFRAME_FIXED_SIZE.
                # But I don't think it should be needed in real life?
                if not we_are_translated():
                    bldr.emit_store(js.zero, js.FrameSlotAddr(pos), typ)

    def emit_body(self, bldr):
        if SANITYCHECK:
            assert len(self.compiled_fragments) == \
                    len(self.compiled_descrs) + 1
        for i in xrange(len(self.compiled_descrs)):
            bldr.emit_fragment(self.compiled_fragments[i])
            descr = self.compiled_descrs[i]
            if isinstance(descr, TargetToken):
                self.emit_jump_body(bldr, descr)
            elif isinstance(descr, AbstractFailDescr):
                self.emit_guard_body(bldr, descr)
            else:
                assert False, "unexpected compiled descr"
        bldr.emit_fragment(self.compiled_fragments[-1])

    def emit_jump_body(self, bldr, descr):
        assert descr._asmjs_block.clt.func is self.clt.func
        self.clt.func.emit_jump(bldr, descr._asmjs_block.compiled_blockid)

    def emit_guard_body(self, bldr, faildescr):
        faillocs = faildescr._asmjs_faillocs
        failkinds = faildescr._asmjs_failkinds
        failvars = [None] * len(faildescr._asmjs_failvars)
        inputvars = self._get_inputvars_from_kinds(failkinds, bldr)
        bldr.emit_comment("GUARD BODY")
        # Re-allocate the failvars in the current jsbuilder.
        # This ensures they can be looked up by object identity,
        # and prevents them being allocated for tempvars.
        for i in range(len(faildescr._asmjs_failvars)):
            if failkinds[i] == HOLE:
                continue
            varname = faildescr._asmjs_failvars[i].varname
            if failkinds[i] == FLOAT:
                failvar = bldr.allocate_doublevar(int(varname[1:]))
            else:
                failvar = bldr.allocate_intvar(int(varname[1:]))
            failvars[i] = failvar
        # If the guard has been compiled into a bridge, emit a local
        # jump to the appropriate label.  Otherwise, spill to frame.
        target_block = faildescr._asmjs_block
        if target_block is not None:
            # XXX TODO: we know that the guard code will just be inserted
            # inline here.  There's no need for a jump, and we can have it
            # read directly from the failvars rather than inputvars.
            bldr.emit_comment("JUMP TO BRIDGED GUARD")
            self._emit_swap_vars(bldr, failvars, inputvars, failkinds)
            self.clt.func.emit_jump(bldr, target_block.compiled_blockid)
        else:
            # If there might be an exception, capture it to the frame.
            if faildescr._asmjs_hasexc:
                bldr.emit_comment("PRESERVE EXCEPTION INFO")
                pos_exctyp = js.ConstInt(self.cpu.pos_exception())
                pos_excval = js.ConstInt(self.cpu.pos_exc_value())
                exctyp = js.HeapData(js.Int32, pos_exctyp)
                excval = js.HeapData(js.Int32, pos_excval)
                with bldr.emit_if_block(exctyp):
                    addr = js.FrameGuardExcAddr()
                    bldr.emit_store(excval, addr, js.Int32)
                    bldr.emit_store(js.zero, pos_exctyp, js.Int32)
                    bldr.emit_store(js.zero, pos_excval, js.Int32)
            # Call guard failure helper, creating code for it if necessary.
            # The code is uniquely identified by failkinds.
            # XXX TODO: this whole "helper func" thing needs a good refactor.
            # XXX TODO: currently, helper funcs will never be freed.
            helper_argtypes = ["i", "i"]
            helper_args = [
                js.ConstPtr(cast_instance_to_gcref(faildescr)),
                js.ConstInt(self.cpu.cast_ptr_to_int(faildescr._asmjs_gcmap))
            ]
            for i in xrange(len(failkinds)):
                # XXX TODO: are we sure that faillocs[i] is a deterministic
                # function of the failkinds list?
                kind = failkinds[i]
                if kind == HOLE:
                    continue
                helper_args.append(failvars[i])
                if kind == FLOAT:
                    helper_argtypes.append("d")
                else:
                    helper_argtypes.append("i")
            helper_name = "guard_failure_" + "".join(helper_argtypes)
            if not bldr.has_helper_func(helper_name):
                with bldr.make_helper_func(helper_name, helper_argtypes) as hb:
                    # Store the failargs into the frame.
                    hb.emit_comment("SPILL %d FAILARGS" % (len(faillocs),))
                    for i in xrange(len(failkinds)):
                        kind = failkinds[i]
                        if kind == HOLE:
                            continue
                        typ = js.HeapType.from_kind(kind)
                        myvar = inputvars[i]
                        assert isinstance(myvar, js.Variable)
                        if kind == FLOAT:
                            var = myvar
                        else:
                            # +2 for descr and gcmap input args
                            var = hb.allocate_intvar(int(myvar.varname[1:])+2)
                        pos = faillocs[i]
                        hb.emit_store(var, js.FrameSlotAddr(pos), typ)
                    # Write the gcmap from second input arg.
                    self.emit_store_gcmapref(hb, hb.allocate_intvar(1))
                    # Write the faildescr from first input arg.
                    hb.emit_comment("STORE FAILDESCR")
                    descr_var = hb.allocate_intvar(0)
                    hb.emit_store(descr_var, js.FrameDescrAddr(), js.Int32)
            bldr.emit_call_helper_func(helper_name, helper_args)
            # Bail back to the interpreter to deal with the failure.
            bldr.emit_exit()

    def emit_store_gcmap(self, bldr, gcmap, writebarrier=True):
        # Store the appropriate gcmap on the frame.
        comment = "STORE GCMAP"
        if SANITYCHECK:
            if gcmap == jitframe.NULLGCMAP:
                comment += " 0"
            else:
                for i in xrange(len(gcmap)):
                    comment = comment + " %d" % (gcmap[i],)
        bldr.emit_comment(comment)
        gcmapref = js.ConstInt(self.cpu.cast_ptr_to_int(gcmap))
        writebarrier = writebarrier and gcmap != jitframe.NULLGCMAP
        self.emit_store_gcmapref(bldr, gcmapref, writebarrier)

    def emit_store_gcmapref(self, bldr, val, writebarrier=True):
        bldr.emit_store(val, js.FrameGCMapAddr(), js.Int32)
        # We might have just stored some young pointers into the frame.
        # Emit a write barrier just in case.
        if writebarrier:
            with bldr.emit_if_block(val):
                self.emit_write_barrier(bldr, [js.frame])

    def emit_write_barrier(self, bldr, arguments, wbdescr=None, array=False):
        # Decode and grab the necessary function pointer.
        # If it's zero, the GC doesn't need a write barrier here.
        cpu = self.cpu
        if wbdescr is None:
            wbdescr = cpu.gc_ll_descr.write_barrier_descr
        if wbdescr is None:
            return
        if we_are_translated():
            cls = cpu.gc_ll_descr.has_write_barrier_class()
            assert cls is not None and isinstance(wbdescr, cls)
        card_marking = False
        if array and wbdescr.jit_wb_cards_set != 0:
            assert (wbdescr.jit_wb_cards_set_byteofs ==
                    wbdescr.jit_wb_if_flag_byteofs)
            card_marking = True
        if not card_marking:
            wbfunc = wbdescr.get_write_barrier_fn(cpu)
        else:
            wbfunc = wbdescr.get_write_barrier_from_array_fn(cpu)
        wbfunc = rffi.cast(lltype.Signed, wbfunc)
        if wbfunc == 0:
            return
        bldr.emit_comment("WRITE BARRIER")
        # Here we are inlining a bunch of code from the write-barrier,
        # in a similar way to how it gets inlined in the non-jitted code.
        # The structure of the generated code looks like this for plain
        # objects:
        #
        #    if (obj has JIT_WB_IF_FLAG) {
        #      dynCall(write_barrier, obj)
        #    }
        #
        # And like this for arrays with potential card-marking:
        #
        #    if (obj has JIT_WB_IF_FLAG|JIT_WB_CARDS_SET) {
        #      if (not obj has JIT_WB_CARDS_SET) {
        #        dynCall(write_barrier, obj)
        #      }
        #      if (obj has JIT_WB_CARDS_SET) {
        #        do the card marking
        #      }
        #    }
        #
        # XXX TODO: would this be neater if split into separate functions?
        #
        obj = arguments[0]
        flagaddr = js.Plus(obj, js.ConstInt(wbdescr.jit_wb_if_flag_byteofs))
        flagaddrvar = bldr.allocate_intvar()
        bldr.emit_assignment(flagaddrvar, flagaddr)
        flagbyte = js.HeapData(js.UInt8, flagaddrvar)
        flagbytevar = bldr.allocate_intvar()
        chk_flag = js.ConstInt(wbdescr.jit_wb_if_flag_singlebyte)
        chk_flag = js.UnsignedCharCast(chk_flag)
        chk_card = js.zero
        flag_has_cards = js.zero
        if card_marking:
            chk_card = js.ConstInt(wbdescr.jit_wb_cards_set_singlebyte)
            chk_card = js.UnsignedCharCast(chk_card)
            flag_has_cards = js.And(flagbytevar, chk_card)
        flag_needs_wb = js.And(flagbytevar, js.Or(chk_flag, chk_card))
        # Check if we actually need to establish a writebarrier.
        bldr.emit_assignment(flagbytevar, flagbyte)
        with bldr.emit_if_block(flag_needs_wb):
            call = js.DynCallFunc("vi", js.ConstInt(wbfunc), [obj])
            if not card_marking:
                bldr.emit_expr(call)
            else:
                with bldr.emit_if_block(js.UNot(flag_has_cards)):
                    # This might change the GC flags on the object.
                    bldr.emit_expr(call)
                    bldr.emit_assignment(flagbytevar, flagbyte)
                # Check if we need to set a card-marking flag.
                with bldr.emit_if_block(flag_has_cards):
                    # This is how we decode the array index into a card
                    # bit to set.  Logic cargo-culted from x86 backend.
                    which = arguments[1]
                    card_shift = js.ConstInt(wbdescr.jit_wb_card_page_shift)
                    byte_index = js.RShift(which, card_shift)
                    byte_ofs = js.UNeg(js.RShift(byte_index, js.ConstInt(3)))
                    byte_mask = js.LShift(js.ConstInt(1),
                                          js.And(byte_index, js.ConstInt(7)))
                    byte_addr = js.Plus(obj, byte_ofs)
                    with ctx_temp_intvar(bldr, byte_addr) as byte_addr:
                        old_byte_data = js.HeapData(js.UInt8, byte_addr)
                        new_byte_data = js.Or(old_byte_data, byte_mask)
                        bldr.emit_store(new_byte_data, byte_addr, js.UInt8)
        bldr.free_intvar(flagbytevar)
        bldr.free_intvar(flagaddrvar)

    def _get_framelocs_from_kinds(self, kinds, offset=0):
        locations = [-1] * len(kinds)
        baseofs = self.cpu.get_baseofs_of_frame_field()
        for i in xrange(len(kinds)):
            kind = kinds[i]
            if kind == HOLE:
                continue
            typ = js.HeapType.from_kind(kind)
            alignment = offset % typ.size
            if alignment:
                offset += typ.size - alignment
            locations[i] = offset
            offset += typ.size
        if self.clt is not None:
            self.clt.func.ensure_frame_depth(offset)
        return locations

    def _get_inputvars_from_kinds(self, kinds, bldr=None):
        if bldr is None:
            bldr = self.bldr
        num_int_args = 0
        num_double_args = 0
        inputvars = [js.zero] * len(kinds)
        for i in xrange(len(kinds)):
            kind = kinds[i]
            if kind == HOLE:
                continue
            if kind == FLOAT:
                inputvars[i] = bldr.allocate_doublevar(num_double_args)
                num_double_args += 1
            else:
                inputvars[i] = bldr.allocate_intvar(num_int_args)
                num_int_args += 1
        return inputvars

    # Methods for generating the javascript code fragments.
    # These are called once, at block creation.

    def generate_code(self):
        # Allocate variables for the input args.
        # These will be populated by separately-built code.
        inputvars = self._get_inputvars_from_kinds(self.inputkinds)
        for i in xrange(len(self.inputargs)):
            box = self.inputargs[i]
            if box:
                self.box_variables[box] = inputvars[i]
                self.box_variable_refcounts[inputvars[i]] = 1
        # Walk the list of operations, emitting code for each.
        # We expend some modest effort to generate "nice" javascript code,
        # by e.g. folding constant expressions and eliminating temp variables.
        #os.write(2, "====\n")
        #for i in xrange(len(self.inputargs)):
        #    os.write(2, "INPUT %d: %s\n" % (i, self.inputargs[i]))
        #for i in xrange(len(self.operations)):
        #    self._print_op(self.operations[i])
        #os.write(2, "====\n")
        self.pos = 0
        while self.pos < len(self.operations):
            #os.write(2, "  GENERATING OP #%d\n" % (self.pos,))
            op = self.operations[self.pos]
            step = 1
            # Flush any suspended expressions that would be invalidated by this
            # op. XXX TODO: more nuanced detection of what would be invalidated.
            if not (op.has_no_side_effect() or op.is_guard()):
                for box in self.box_expressions.keys():
                    self._genop_flush_box(box)
            # Is it one of the special test-only opcodes?
            if not we_are_translated() and op.getopnum() == -124:
                self.genop_force_spill(op)
            # Can we omit the operation completely?
            elif op.has_no_side_effect() and op.result not in self.longevity:
                self.bldr.emit_comment("OMMITTED USELESS JIT OP: %s" % (op,))
            # Do we need to emit it in conjunction with a guard?
            elif self._op_needs_guard(op):
                if SANITYCHECK:
                    assert self.pos + 1 < len(self.operations)
                step = 2
                guardop = self.operations[self.pos + 1]
                if SANITYCHECK:
                    assert guardop.is_guard()
                self.bldr.emit_comment("BEGIN JIT OP: %s" % (op,))
                self.bldr.emit_comment("  WITH GUARD OP: %s" % (guardop,))
                genop_withguard_list[op.getopnum()](self, op, guardop)
            # Do we need to write complex code for it?
            elif not self._op_is_simple_expr(op):
                self.bldr.emit_comment("BEGIN JIT OP: %s" % (op,))
                genop_list[op.getopnum()](self, op)
            # It's just a simple expression.
            # Maybe we can suspend it and fold it into a later expression?
            else:
                self.bldr.emit_comment("BEGIN JIT EXPR OP: %s" % (op,))
                expr = genop_expr_list[op.getopnum()](self, op)
                if self._can_suspend_box(op.result):
                     self._suspend_box_expression(op.result, expr)
                     self.bldr.emit_comment("SUSPENDED JIT EXPR OP")
                else:
                    boxvar = self._allocate_box_variable(op.result)
                    self.bldr.emit_assignment(boxvar, expr)
            # Free vars for boxes that are no longer needed.
            self.pos += step
            for i in xrange(self.pos - step, self.pos):
                op = self.operations[i]
                for j in range(op.numargs()):
                    self._maybe_free_boxvar(op.getarg(j))
                self._maybe_free_boxvar(op.result)

        # Capture the final fragment.
        fragment = self.bldr.capture_fragment()
        self.compiled_fragments.append(fragment)

        # Clear code-generation info so that we don't hold refs to it.
        self.bldr = None
        self.inputargs = None
        self.operations = None
        self.longevity = {}
        self.spilled_frame_locations = None
        self.spilled_frame_values = None
        self.spilled_frame_offset = 0
        self.forced_spill_frame_offset = 0
        self.box_variables = None
        self.box_expressions = None
        self.box_expression_graveyard = None
        self.box_variable_refcounts = None

    #
    # Methods for dealing with boxes and their values.
    # It's a little complicated because we can "suspend" box expressions
    # in the hopes of folding them into some later expression.
    #

    def _can_suspend_box(self, box):
        # XXX TODO: more general suspension mechanism, rather than
        # just "is used once, immediately in the next op".
        if not self._is_final_use(box, self.pos + 1):
            return False
        count = 0
        next_op = self.operations[self.pos + 1]
        for i in xrange(next_op.numargs()):
            if next_op.getarg(i) == box:
                count += 1
                if count > 1:
                    return False
        if next_op.is_guard():
            for arg in next_op.getfailargs():
                if arg == box:
                    count += 1
                    if count > 1:
                        return False
        return True

    def _suspend_box_expression(self, box, expr):
        if SANITYCHECK:
            assert box not in self.box_variables
            assert box not in self.box_expressions
        for var in self._iter_box_variables(expr):
            self.box_variable_refcounts[var] += 1
        self.box_expressions[box] = expr

    def _iter_box_variables(self, expr):
        for var in js.iter_variables(expr):
            if var == js.frame:
                continue
            assert var in self.box_variable_refcounts
            yield var

    def _get_jsval(self, jitval):
        if isinstance(jitval, Box):
            boxvar = self.box_variables.get(jitval, None)
            if boxvar is not None:
                return boxvar
            boxexpr = self.box_expressions.pop(jitval, None)
            if boxexpr is not None:
                self.box_expression_graveyard[jitval] = boxexpr
                return boxexpr
            if SANITYCHECK:
                os.write(2, "UNINITIALIZED BOX: %s\n" % (jitval,))
                if jitval in self.box_expression_graveyard:
                    os.write(2, "  BOX EXPR WAS ALREADY CONSUMED\n")
                os.write(2, self.bldr.finish() + "\n")
                raise ValueError("Uninitialized box")
            return None
        return jitval

    def _allocate_box_variable(self, box, boxvar=None):
        assert isinstance(box, Box)
        assert box not in self.box_variables
        if boxvar is not None:
            self.box_variable_refcounts[boxvar] += 1
        else:
            if box.type == FLOAT:
                boxvar = self.bldr.allocate_doublevar()
            else:
                boxvar = self.bldr.allocate_intvar()
            self.box_variable_refcounts[boxvar] = 1
        self.box_variables[box] = boxvar
        return boxvar

    def _genop_flush_box(self, box):
        if not isinstance(box, Box):
            return self._get_jsval(box)
        boxvar = self.box_variables.get(box, None)
        if boxvar is None:
            boxexpr = self._get_jsval(box)
            # If it points to some other box, re-use the variable directly.
            if isinstance(boxexpr, js.Variable) and boxexpr in self.box_variable_refcounts:
                self._allocate_box_variable(box, boxexpr)
            else:
                self.bldr.emit_comment("FLUSH SUSPENDED BOX")
                boxvar = self._allocate_box_variable(box)
                self.bldr.emit_assignment(boxvar, boxexpr)
        return boxvar

    def _is_final_use(self, box, i):
        if box is None or not isinstance(box, Box):
            return False
        assert isinstance(box, Box)
        if box not in self.longevity:
            return True
        return self.longevity[box][1] <= i

    def _maybe_free_boxvar(self, box):
        if not isinstance(box, Box):
            return
        if not self._is_final_use(box, self.pos - 1):
            return
        # The box can be freed.  Clean up any suspended expressions,
        # which may in turn free additional variables.
        if SANITYCHECK:
            assert box not in self.box_expressions
        boxvar = self.box_variables.pop(box, None)
        if boxvar is not None:
            self.box_variable_refcounts[boxvar] -= 1
            if self.box_variable_refcounts[boxvar] == 0:
                self._free_boxvar(boxvar)
        boxexpr = self.box_expression_graveyard.pop(box, None)
        if boxexpr is not None:
            for var in self._iter_box_variables(boxexpr):
                self.box_variable_refcounts[var] -= 1
                if self.box_variable_refcounts[var] == 0:
                    self._free_boxvar(var)

    def _free_boxvar(self, var):
        # We must not free the variables for our inputargs,
        # XXX TODO: I no longer recall what badness happens
        # if we free input vars...
        assert isinstance(var, js.Variable)
        refcount = self.box_variable_refcounts.pop(var)
        if SANITYCHECK:
            assert refcount == 0
        if var not in self.inputargs:
            assert var != js.frame
            if isinstance(var, js.IntVar):
                self.bldr.free_intvar(var)
            elif isinstance(var, js.DoubleVar):
                self.bldr.free_doublevar(var)

    #
    # Methods for checking various properties of an opcode.
    #

    @staticmethod
    def _op_needs_guard(op):
        """Check if the given op must be implemented together with a guard.

        Some operations only make sense when followed immediately by a specific
        guard op, and we can only emit correct and efficient code when we
        consider the operation and its guard as a single unit.
        """
        # Possibly-overflowing ops must have an overflow-checking guard.
        if op.is_ovf():
            return True
        # Calls that may force must be followed by a force-checking guard.
        opnum = op.getopnum()
        if opnum == rop.CALL_MAY_FORCE:
            return True
        elif opnum == rop.CALL_ASSEMBLER:
            return True
        elif opnum == rop.CALL_RELEASE_GIL:
            return True
        # All other operations can be implemented stand-alone.
        return False

    @staticmethod
    def _op_is_simple_expr(op):
        """Check if the given op can be implemented as a simple expression.

        This check identifies operations that can be implemented as a simple
        expression, and are therefore easy to fold into other ops if their
        result is only used a single time.
        """
        if not op.is_always_pure():
            return False
        if op.getopnum() == rop.INT_FORCE_GE_ZERO:
            return False
        if op.getopnum() == rop.INT_SIGNEXT:
            return False
        if op.getopnum() == rop.FLOAT_ABS:
            return False
        if op.result.type == FLOAT:
            if op.getopnum() == rop.GETFIELD_GC_PURE:
                return False
            if op.getopnum() == rop.GETFIELD_RAW_PURE:
                return False
            if op.getopnum() == rop.GETARRAYITEM_GC_PURE:
                return False
            if op.getopnum() == rop.GETARRAYITEM_RAW_PURE:
                return False
        return True

    def _get_frame_locations(self, arguments, offset=-1):
        """Allocate locations in the frame for all the given arguments."""
        baseofs = self.cpu.get_baseofs_of_frame_field()
        locations = [-1] * len(arguments)
        if offset < 0:
            offset = self.forced_spill_frame_offset
        for i in xrange(len(arguments)):
            box = arguments[i]
            typ = js.HeapType.from_box(box)
            alignment = offset % typ.size
            if alignment:
                offset += typ.size - alignment
            locations[i] = offset
            offset += typ.size
        if self.clt is not None:
            self.clt.func.ensure_frame_depth(offset)
        return locations

    def _genop_spill_to_frame(self, box, offset=-1):
        baseofs = self.cpu.get_baseofs_of_frame_field()
        typ = js.HeapType.from_box(box)
        # Allocate it a position at the next available offset.
        # Align each value to a multiple of its size.
        if offset == -1:
            offset = self.spilled_frame_offset
            alignment = offset % typ.size
            if alignment:
                offset += typ.size - alignment
        if offset + typ.size > self.spilled_frame_offset:
            self.spilled_frame_offset = offset + typ.size
        # Generate code to write the value into the frame.
        addr = js.FrameSlotAddr(offset)
        boxexpr = self._genop_flush_box(box)
        self.bldr.emit_store(boxexpr, addr, typ)
        # Record where we spilled it.
        if box not in self.spilled_frame_locations:
            self.spilled_frame_locations[box] = []
        self.spilled_frame_locations[box].append(offset)
        self.spilled_frame_values[offset] = box
        return offset

    #
    #  Code-Generating dispatch methods.
    #  There's a method here for every resop we support.
    #  They are built into dispatch tables by code at the end of this file.
    #

    def genop_label(self, op):
        pass

    def genop_expr_strgetitem(self, op):
        base = self._get_jsval(op.getarg(0))
        offset = self._get_jsval(op.getarg(1))
        arraytoken = symbolic.get_array_token(rstr.STR,
                                              self.cpu.translate_support_code)
        basesize, itemsize, len_offset = arraytoken
        assert itemsize == 1
        itemoffset = js.Plus(js.ConstInt(basesize), offset)
        return js.HeapData(js.UInt8, js.Plus(base, itemoffset))

    def genop_strsetitem(self, op):
        base = self._get_jsval(op.getarg(0))
        offset = self._get_jsval(op.getarg(1))
        value = self._get_jsval(op.getarg(2))
        arraytoken = symbolic.get_array_token(rstr.STR,
                                              self.cpu.translate_support_code)
        basesize, itemsize, len_offset = arraytoken
        assert itemsize == 1
        itemoffset = js.Plus(js.ConstInt(basesize), offset)
        self.bldr.emit_store(value, js.Plus(base, itemoffset), js.UInt8)

    def genop_expr_strlen(self, op):
        base = self._get_jsval(op.getarg(0))
        arraytoken = symbolic.get_array_token(rstr.STR,
                                              self.cpu.translate_support_code)
        basesize, itemsize, len_offset = arraytoken
        return js.HeapData(js.Int32, js.Plus(base, js.ConstInt(len_offset)))

    def genop_copystrcontent(self, op):
        arraytoken = symbolic.get_array_token(rstr.STR,
                                              self.cpu.translate_support_code)
        basesize, itemsize, _ = arraytoken
        self._genop_copy_array(op, basesize, itemsize)

    def genop_expr_unicodegetitem(self, op):
        base = self._get_jsval(op.getarg(0))
        offset = self._get_jsval(op.getarg(1))
        arraytoken = symbolic.get_array_token(rstr.UNICODE,
                                              self.cpu.translate_support_code)
        basesize, itemsize, len_offset = arraytoken
        typ = js.HeapType.from_size_and_sign(itemsize, False)
        itemoffset = js.Plus(js.ConstInt(basesize),
                             js.IMul(offset, js.ConstInt(itemsize)))
        return js.HeapData(typ, js.Plus(base, itemoffset))

    def genop_unicodesetitem(self, op):
        base = self._get_jsval(op.getarg(0))
        offset = self._get_jsval(op.getarg(1))
        value = self._get_jsval(op.getarg(2))
        arraytoken = symbolic.get_array_token(rstr.UNICODE,
                                              self.cpu.translate_support_code)
        basesize, itemsize, len_offset = arraytoken
        typ = js.HeapType.from_size_and_sign(itemsize, False)
        itemoffset = js.Plus(js.ConstInt(basesize),
                             js.IMul(offset, js.ConstInt(itemsize)))
        self.bldr.emit_store(value, js.Plus(base, itemoffset), typ)

    def genop_expr_unicodelen(self, op):
        base = self._get_jsval(op.getarg(0))
        arraytoken = symbolic.get_array_token(rstr.UNICODE,
                                              self.cpu.translate_support_code)
        basesize, itemsize, len_offset = arraytoken
        return js.HeapData(js.Int32, js.Plus(base, js.ConstInt(len_offset)))

    def genop_copyunicodecontent(self, op):
        arraytoken = symbolic.get_array_token(rstr.UNICODE,
                                              self.cpu.translate_support_code)
        basesize, itemsize, _ = arraytoken
        self._genop_copy_array(op, basesize, itemsize)

    def _genop_copy_array(self, op, array_basesize, array_itemsize):
        srcbase = self._get_jsval(op.getarg(0))
        dstbase = self._get_jsval(op.getarg(1))
        srcoffset = self._get_jsval(op.getarg(2))
        dstoffset = self._get_jsval(op.getarg(3))
        lengthbox = self._get_jsval(op.getarg(4))
        assert srcbase != dstbase
        basesize = js.ConstInt(array_basesize)
        itemsize = js.ConstInt(array_itemsize)
        # Calculate offset into source array.
        srcaddr = js.Plus(srcbase,
                          js.Plus(basesize, js.IMul(srcoffset, itemsize)))
        # Calculate offset into destination array.
        dstaddr = js.Plus(dstbase,
                          js.Plus(basesize, js.IMul(dstoffset, itemsize)))
        # Memcpy required number of bytes.
        # XXX TODO: large function overhead for this simple call, since we
        # go out into the main module.  Inline it, or use a local copy?
        nbytes = js.IMul(lengthbox, itemsize)
        self.bldr.emit_expr(js.CallFunc("memcpy", [dstaddr, srcaddr, nbytes]))

    def genop_getfield_gc(self, op):
        base = self._get_jsval(op.getarg(0))
        fielddescr = op.getdescr()
        assert isinstance(fielddescr, FieldDescr)
        offset, fieldsize, signed = unpack_fielddescr(op.getdescr())
        addr = js.Plus(base, js.ConstInt(offset))
        typ = js.HeapType.from_size_and_sign(fieldsize, signed)
        boxvar = self._allocate_box_variable(op.result)
        self.bldr.emit_load(boxvar, addr, typ)

    genop_getfield_raw = genop_getfield_gc
    genop_getfield_gc_pure = genop_getfield_gc
    genop_getfield_raw_pure = genop_getfield_gc

    def genop_expr_getfield_gc_pure(self, op):
        base = self._get_jsval(op.getarg(0))
        fielddescr = op.getdescr()
        assert isinstance(fielddescr, FieldDescr)
        offset, fieldsize, signed = unpack_fielddescr(op.getdescr())
        addr = js.Plus(base, js.ConstInt(offset))
        typ = js.HeapType.from_size_and_sign(fieldsize, signed)
        return js.HeapData(typ, addr)

    genop_expr_getfield_raw_pure = genop_expr_getfield_gc_pure

    def genop_setfield_gc(self, op):
        base = self._get_jsval(op.getarg(0))
        value = self._get_jsval(op.getarg(1))
        offset, fieldsize, signed = unpack_fielddescr(op.getdescr())
        addr = js.Plus(base, js.ConstInt(offset))
        typ = js.HeapType.from_size_and_sign(fieldsize, signed)
        self.bldr.emit_store(value, addr, typ)

    genop_setfield_raw = genop_setfield_gc

    def genop_zero_ptr_field(self, op):
        base = self._get_jsval(op.getarg(0))
        offset = self._get_jsval(op.getarg(1))
        addr = js.Plus(base, offset)
        self.bldr.emit_store(js.zero, addr, js.Int32)

    def genop_getinteriorfield_gc(self, op):
        t = unpack_interiorfielddescr(op.getdescr())
        offset, itemsize, fieldsize, signed = t
        base = self._get_jsval(op.getarg(0))
        which = self._get_jsval(op.getarg(1))
        addr = js.Plus(base, js.Plus(js.ConstInt(offset),
                                     js.IMul(which, js.ConstInt(itemsize))))
        typ = js.HeapType.from_size_and_sign(fieldsize, signed)
        boxvar = self._allocate_box_variable(op.result)
        self.bldr.emit_load(boxvar, addr, typ)

    def genop_setinteriorfield_gc(self, op):
        t = unpack_interiorfielddescr(op.getdescr())
        offset, itemsize, fieldsize, signed = t
        base = self._get_jsval(op.getarg(0))
        which = self._get_jsval(op.getarg(1))
        value = self._get_jsval(op.getarg(2))
        addr = js.Plus(base, js.Plus(js.ConstInt(offset),
                                     js.IMul(which, js.ConstInt(itemsize))))
        typ = js.HeapType.from_size_and_sign(fieldsize, signed)
        self.bldr.emit_store(value, addr, typ)

    genop_setinteriorfield_raw = genop_setinteriorfield_gc

    def genop_expr_arraylen_gc(self, op):
        descr = op.getdescr()
        assert isinstance(descr, ArrayDescr)
        len_offset = descr.lendescr.offset
        base = self._get_jsval(op.getarg(0))
        addr = js.Plus(base, js.ConstInt(len_offset))
        return js.HeapData(js.Int32, addr)

    def genop_getarrayitem_gc(self, op):
        itemsize, offset, signed = unpack_arraydescr(op.getdescr())
        base = self._get_jsval(op.getarg(0))
        which = self._get_jsval(op.getarg(1))
        addr = js.Plus(base, js.Plus(js.ConstInt(offset),
                                     js.IMul(which, js.ConstInt(itemsize))))
        typ = js.HeapType.from_size_and_sign(itemsize, signed)
        boxvar = self._allocate_box_variable(op.result)
        self.bldr.emit_load(boxvar, addr, typ)

    genop_getarrayitem_gc_pure = genop_getarrayitem_gc
    genop_getarrayitem_raw = genop_getarrayitem_gc
    genop_getarrayitem_raw_pure = genop_getarrayitem_gc

    def genop_raw_load(self, op):
        itemsize, offset, signed = unpack_arraydescr(op.getdescr())
        base = self._get_jsval(op.getarg(0))
        which = self._get_jsval(op.getarg(1))
        addr = js.Plus(base, js.Plus(js.ConstInt(offset), which))
        typ = js.HeapType.from_size_and_sign(itemsize, signed)
        boxvar = self._allocate_box_variable(op.result)
        self.bldr.emit_load(boxvar, addr, typ)

    def genop_expr_getarrayitem_gc_pure(self, op):
        itemsize, offset, signed = unpack_arraydescr(op.getdescr())
        base = self._get_jsval(op.getarg(0))
        which = self._get_jsval(op.getarg(1))
        addr = js.Plus(base, js.Plus(js.ConstInt(offset),
                                     js.IMul(which, js.ConstInt(itemsize))))
        typ = js.HeapType.from_size_and_sign(itemsize, signed)
        return js.HeapData(typ, addr)

    genop_expr_getarrayitem_raw_pure = genop_expr_getarrayitem_gc_pure

    def genop_setarrayitem_gc(self, op):
        itemsize, offset, signed = unpack_arraydescr(op.getdescr())
        base = self._get_jsval(op.getarg(0))
        where = self._get_jsval(op.getarg(1))
        value = self._get_jsval(op.getarg(2))
        itemoffset = js.Plus(js.ConstInt(offset),
                             js.IMul(where, js.ConstInt(itemsize)))
        addr = js.Plus(base, itemoffset)
        typ = js.HeapType.from_size_and_sign(itemsize, signed)
        self.bldr.emit_store(value, addr, typ)

    genop_setarrayitem_raw = genop_setarrayitem_gc

    def genop_raw_store(self, op):
        itemsize, offset, signed = unpack_arraydescr(op.getdescr())
        base = self._get_jsval(op.getarg(0))
        where = self._get_jsval(op.getarg(1))
        value = self._get_jsval(op.getarg(2))
        itemoffset = js.Plus(js.ConstInt(offset), where)
        addr = js.Plus(base, itemoffset)
        typ = js.HeapType.from_size_and_sign(itemsize, signed)
        self.bldr.emit_store(value, addr, typ)

    def genop_zero_array(self, op):
        itemsize, baseofs, _ = unpack_arraydescr(op.getdescr())
        base = self._get_jsval(op.getarg(0))
        offset = self._get_jsval(op.getarg(1))
        length = self._get_jsval(op.getarg(2))
        # XXX TODO: check correct meaning of fields here
        startaddr = js.Plus(base,
                            js.Plus(js.ConstInt(baseofs),
                                    js.IMul(offset, js.ConstInt(itemsize))))
        nbytes = js.IMul(length, js.ConstInt(itemsize))
        self.bldr.emit_expr(js.CallFunc("memset", [startaddr, js.zero, nbytes]))

    def _genop_expr_int_unaryop(operator):
        def genop_expr_int_unaryop(self, op):
            return operator(self._get_jsval(op.getarg(0)))
        return genop_expr_int_unaryop

    genop_expr_int_is_zero = _genop_expr_int_unaryop(js.UNot)
    genop_expr_int_neg = _genop_expr_int_unaryop(js.UMinus)
    genop_expr_int_invert = _genop_expr_int_unaryop(js.UNeg)

    def genop_expr_int_is_true(self, op):
        return js.UNot(js.UNot(self._get_jsval(op.getarg(0))))

    def _genop_expr_int_binop(binop):
        def genop_expr_int_binop(self, op):
            return js.IntCast(binop(self._get_jsval(op.getarg(0)),
                                    self._get_jsval(op.getarg(1))))
        return genop_expr_int_binop

    genop_expr_int_lt = _genop_expr_int_binop(js.LessThan)
    genop_expr_int_le = _genop_expr_int_binop(js.LessThanEq)
    genop_expr_int_eq = _genop_expr_int_binop(js.Equal)
    genop_expr_int_ne = _genop_expr_int_binop(js.NotEqual)
    genop_expr_int_gt = _genop_expr_int_binop(js.GreaterThan)
    genop_expr_int_ge = _genop_expr_int_binop(js.GreaterThanEq)
    genop_expr_int_add = _genop_expr_int_binop(js.Plus)
    genop_expr_int_sub = _genop_expr_int_binop(js.Minus)
    genop_expr_int_mul = _genop_expr_int_binop(js.IMul)
    genop_expr_int_floordiv = _genop_expr_int_binop(js.Div)
    genop_expr_int_mod = _genop_expr_int_binop(js.Mod)
    genop_expr_int_and = _genop_expr_int_binop(js.And)
    genop_expr_int_or = _genop_expr_int_binop(js.Or)
    genop_expr_int_xor = _genop_expr_int_binop(js.Xor)
    genop_expr_int_lshift = _genop_expr_int_binop(js.LShift)
    genop_expr_int_rshift = _genop_expr_int_binop(js.RShift)
    genop_expr_uint_rshift = _genop_expr_int_binop(js.URShift)

    def _genop_expr_uint_binop(binop):
        def genop_expr_uint_binop(self, op):
            lhs = js.UnsignedCast(self._get_jsval(op.getarg(0)))
            rhs = js.UnsignedCast(self._get_jsval(op.getarg(1)))
            return binop(lhs, rhs)
        return genop_expr_uint_binop

    genop_expr_uint_lt = _genop_expr_uint_binop(js.LessThan)
    genop_expr_uint_le = _genop_expr_uint_binop(js.LessThanEq)
    genop_expr_uint_gt = _genop_expr_uint_binop(js.GreaterThan)
    genop_expr_uint_ge = _genop_expr_uint_binop(js.GreaterThanEq)

    def genop_expr_uint_floordiv(self, op):
        lhs = js.UnsignedCast(self._get_jsval(op.getarg(0)))
        rhs = js.UnsignedCast(self._get_jsval(op.getarg(1)))
        return js.UnsignedCast(js.Div(lhs, rhs))

    def genop_withguard_int_add_ovf(self, op, guardop):
        if SANITYCHECK:
            assert guardop.is_guard_overflow()
        lhs = self._genop_flush_box(self._get_jsval(op.getarg(0)))
        rhs = self._genop_flush_box(self._get_jsval(op.getarg(1)))
        res = self._allocate_box_variable(op.result)
        self.bldr.emit_assignment(res, js.SignedCast(js.Plus(lhs, rhs)))
        did_overflow = js.Or(js.And(js.GreaterThanEq(lhs, js.zero),
                                    js.LessThan(res, rhs)),
                             js.And(js.LessThan(lhs, js.zero),
                                    js.GreaterThan(res, rhs)))
        if guardop.getopnum() == rop.GUARD_NO_OVERFLOW:
            self._genop_guard_failure(did_overflow, guardop)
        else:
            if SANITYCHECK:
                assert guardop.getopnum() == rop.GUARD_OVERFLOW
            self._genop_guard_failure(js.UNot(did_overflow), guardop)

    def genop_withguard_int_sub_ovf(self, op, guardop):
        if SANITYCHECK:
            assert guardop.is_guard_overflow()
        lhs = self._genop_flush_box(self._get_jsval(op.getarg(0)))
        rhs = self._genop_flush_box(self._get_jsval(op.getarg(1)))
        res = self._allocate_box_variable(op.result)
        self.bldr.emit_assignment(res, js.SignedCast(js.Minus(lhs, rhs)))
        did_overflow = js.Or(js.And(js.GreaterThanEq(rhs, js.zero),
                                    js.GreaterThan(res, lhs)),
                             js.And(js.LessThan(rhs, js.zero),
                                    js.LessThan(res, lhs)))
        if guardop.getopnum() == rop.GUARD_NO_OVERFLOW:
            self._genop_guard_failure(did_overflow, guardop)
        else:
            if SANITYCHECK:
                assert guardop.getopnum() == rop.GUARD_OVERFLOW
            self._genop_guard_failure(js.UNot(did_overflow), guardop)

    def genop_withguard_int_mul_ovf(self, op, guardop):
        if SANITYCHECK:
            assert guardop.is_guard_overflow()
        lhs = self._genop_flush_box(self._get_jsval(op.getarg(0)))
        rhs = self._genop_flush_box(self._get_jsval(op.getarg(1)))
        res = self._allocate_box_variable(op.result)
        # To check for overflow in the general case, we have to perform the
        # multiplication twice - once as double and once as an int, then
        # check whether they are equal.
        # XXX TODO  a better way to detect imul overflow?
        self.bldr.emit_assignment(res, js.IMul(lhs, rhs))
        with ctx_temp_doublevar(self.bldr) as resdbl:
            self.bldr.emit_assignment(resdbl, js.Mul(js.DoubleCast(lhs),
                                                     js.DoubleCast(rhs)))
            if guardop.getopnum() == rop.GUARD_NO_OVERFLOW:
                test = js.NotEqual(js.DoubleCast(res), resdbl)
            else:
                if SANITYCHECK:
                    assert guardop.getopnum() == rop.GUARD_OVERFLOW
                test = js.Equal(js.DoubleCast(res), resdbl)
            self._genop_guard_failure(test, guardop)

    def genop_int_force_ge_zero(self, op):
        argbox = op.getarg(0)
        self._genop_flush_box(argbox)
        arg = self._get_jsval(argbox)
        resvar = self._allocate_box_variable(op.result)
        with self.bldr.emit_if_block(js.LessThan(arg, js.zero)):
            self.bldr.emit_assignment(resvar, js.zero)
        with self.bldr.emit_else_block():
            self.bldr.emit_assignment(resvar, arg)

    def genop_int_signext(self, op):
        value = self._get_jsval(op.getarg(0))
        numbytesbox = op.getarg(1)
        assert isinstance(numbytesbox, ConstInt)
        numbytes = numbytesbox.getint()
        resvar = self._allocate_box_variable(op.result)
        # We can only sign-extend for 8-bit or 16-bit into 32-bit.
        if numbytes == 1:
            lowbytes = js.And(value, js.ConstInt(0xFF))
            highbytes = js.ConstInt(-256)
            signbit = js.And(value, js.ConstInt(0x80))
        elif numbytes == 2:
            lowbytes = js.And(value, js.ConstInt(0xFFFF))
            highbytes = js.ConstInt(-65536)
            signbit = js.And(value, js.ConstInt(0x8000))
        else:
            raise ValueError("invalid number of bytes to signext")
        self.bldr.emit_assignment(resvar, lowbytes)
        with self.bldr.emit_if_block(signbit):
            self.bldr.emit_assignment(resvar, js.Plus(resvar, highbytes))

    genop_expr_ptr_eq = genop_expr_int_eq
    genop_expr_ptr_ne = genop_expr_int_ne
    genop_expr_instance_ptr_eq = genop_expr_ptr_eq
    genop_expr_instance_ptr_ne = genop_expr_ptr_ne

    def genop_expr_same_as(self, op):
        return self._get_jsval(op.getarg(0))

    genop_expr_cast_ptr_to_int = genop_expr_same_as
    genop_expr_cast_int_to_ptr = genop_expr_same_as

    def _genop_expr_float_unaryop(operator):
        def genop_expr_float_unaryop(self, op):
            return operator(self._get_jsval(op.getarg(0)))
        return genop_expr_float_unaryop

    genop_expr_float_neg = _genop_expr_float_unaryop(js.UMinus)

    def genop_float_abs(self, op):
        argbox = op.getarg(0)
        arg = self._genop_flush_box(argbox)
        resvar = self._allocate_box_variable(op.result)
        zero = js.ConstFloat(longlong.getfloatstorage(0.0))
        with self.bldr.emit_if_block(js.LessThan(arg, zero)):
            self.bldr.emit_assignment(resvar, js.UMinus(arg))
        with self.bldr.emit_else_block():
            self.bldr.emit_assignment(resvar, arg)

    def _genop_expr_float_binop(binop):
        def genop_expr_float_binop(self, op):
            return js.DoubleCast(binop(self._get_jsval(op.getarg(0)),
                                       self._get_jsval(op.getarg(1))))
        return genop_expr_float_binop

    genop_expr_float_add = _genop_expr_float_binop(js.Plus)
    genop_expr_float_sub = _genop_expr_float_binop(js.Minus)
    genop_expr_float_mul = _genop_expr_float_binop(js.Mul)
    genop_expr_float_truediv = _genop_expr_float_binop(js.Div)

    genop_expr_float_lt = _genop_expr_int_binop(js.LessThan)
    genop_expr_float_le = _genop_expr_int_binop(js.LessThanEq)
    genop_expr_float_eq = _genop_expr_int_binop(js.Equal)
    genop_expr_float_ne = _genop_expr_int_binop(js.NotEqual)
    genop_expr_float_gt = _genop_expr_int_binop(js.GreaterThan)
    genop_expr_float_ge = _genop_expr_int_binop(js.GreaterThanEq)

    def genop_expr_convert_float_bytes_to_longlong(self, op):
        # XXX TODO: we don't have longlongs, but tests require this.
        os.write(2, "WARNING: genop_expr_convert_float_bytes_to_longlong\n")
        return self._get_jsval(op.getarg(0))

    def genop_expr_convert_longlong_bytes_to_float(self, op):
        # XXX TODO: we don't have longlongs, but tests require this.
        os.write(2, "WARNING: genop_expr_convert_longlong_bytes_to_float\n")
        return self._get_jsval(op.getarg(0))

    def genop_expr_cast_float_to_int(self, op):
        return js.SignedCast(self._get_jsval(op.getarg(0)))

    def genop_expr_cast_int_to_float(self, op):
        return js.DoubleCast(self._get_jsval(op.getarg(0)))

    #
    # Calls and Jumps and Exits, Oh My!
    #

    def genop_call(self, op):
        descr = op.getdescr()
        assert isinstance(descr, CallDescr)
        assert op.numargs() == len(descr.arg_classes) + 1
        # See if we can special-case this call with a builtin.
        # XXX TODO: wtf is "oop" anyway?
        effectinfo = descr.get_extra_info()
        oopspecindex = effectinfo.oopspecindex
        if oopspecindex == EffectInfo.OS_MATH_SQRT:
            self._genop_math_sqrt(op)
        elif oopspecindex == EffectInfo.OS_MATH_READ_TIMESTAMP:
            self._genop_math_read_timestamp(op)
        else:
            addr = self._get_jsval(op.getarg(0))
            args = []
            i = 1
            while i < op.numargs():
                args.append(self._get_jsval(op.getarg(i)))
                i += 1
            self._genop_call(op, descr, addr, args)

    def genop_call_malloc_gc(self, op):
        descr = op.getdescr()
        assert isinstance(descr, CallDescr)
        assert op.numargs() == len(descr.arg_classes) + 1
        addr = self._get_jsval(op.getarg(0))
        args = []
        i = 1
        while i < op.numargs():
            args.append(self._get_jsval(op.getarg(i)))
            i += 1
        self._genop_call(op, descr, addr, args)
        resvar = self.box_variables[op.result]
        with self.bldr.emit_if_block(js.Equal(resvar, js.zero)):
            self._genop_propagate_exception()

    def genop_cond_call(self, op):
        descr = op.getdescr()
        assert isinstance(descr, CallDescr)
        assert op.numargs() == len(descr.arg_classes) + 2
        cond = self._get_jsval(op.getarg(0))
        addr = self._get_jsval(op.getarg(1))
        args = []
        i = 2
        while i < op.numargs():
            args.append(self._get_jsval(op.getarg(i)))
            i += 1
        with self.bldr.emit_if_block(cond):
            self._genop_call(op, descr, addr, args)

    def genop_withguard_call_may_force(self, op, guardop):
        descr = op.getdescr()
        assert isinstance(descr, CallDescr)
        assert op.numargs() == len(descr.arg_classes) + 1
        addr = self._get_jsval(op.getarg(0))
        args = []
        i = 1
        while i < op.numargs():
            args.append(self._get_jsval(op.getarg(i)))
            i += 1
        with ctx_guard_not_forced(self, guardop):
            self._genop_call(op, descr, addr, args)

    def genop_withguard_call_release_gil(self, op, guardop):
        descr = op.getdescr()
        assert isinstance(descr, CallDescr)
        assert op.numargs() == len(descr.arg_classes) + 2
        saveerrbox = op.getarg(0)
        assert isinstance(saveerrbox, ConstInt)
        save_err = saveerrbox.getint()
        fnaddr = self._get_jsval(op.getarg(1))
        args = []
        i = 2
        while i < op.numargs():
            args.append(self._get_jsval(op.getarg(i)))
            i += 1
        release_addr = js.ConstInt(self.clt.assembler.release_gil_addr)
        reacquire_addr = js.ConstInt(self.clt.assembler.reacquire_gil_addr)
        with ctx_guard_not_forced(self, guardop):
            with ctx_allow_gc(self):
                # Release the GIL.
                self.bldr.emit_expr(js.DynCallFunc("v", release_addr, []))
                # If necessary, restore our local copy into the real 'errno'.
                if save_err & rffi.RFFI_READSAVED_ERRNO:
                    if save_err & rffi.RFFI_ALT_ERRNO:
                        rpy_errno_ofs = llerrno.get_alt_errno_offset(self.cpu)
                    else:
                        rpy_errno_ofs = llerrno.get_rpy_errno_offset(self.cpu)
                    p_errno_ofs = llerrno.get_p_errno_offset(self.cpu)
                    errno = js.HeapData(
                        js.Int32,
                        js.Plus(js.tladdr, js.ConstInt(rpy_errno_ofs))
                    )
                    errno_addr = js.HeapData(
                        js.Int32,
                        js.Plus(js.tladdr, js.ConstInt(p_errno_ofs))
                    )
                    self.bldr.emit_store(errno, errno_addr, js.Int32)
                # If necessary, zero out the real 'errno'.
                elif save_err & rffi.RFFI_ZERO_ERRNO_BEFORE:
                    p_errno_ofs = llerrno.get_p_errno_offset(self.cpu)
                    errno_addr = js.HeapData(
                        js.Int32,
                        js.Plus(js.tladdr, js.ConstInt(p_errno_ofs))
                    )
                    self.bldr.emit_store(js.zero, errno_addr, js.Int32)
                # Do the actual call.
                self._genop_call(op, descr, fnaddr, args)
                # If necessary, read the real 'errno' and save a local copy.
                if save_err & rffi.RFFI_SAVE_ERRNO:
                    if save_err & rffi.RFFI_ALT_ERRNO:
                        rpy_errno_ofs = llerrno.get_alt_errno_offset(self.cpu)
                    else:
                        rpy_errno_ofs = llerrno.get_rpy_errno_offset(self.cpu)
                    p_errno_ofs = llerrno.get_p_errno_offset(self.cpu)
                    errno = js.HeapData(
                        js.Int32,
                        js.HeapData(js.Int32, js.Plus(
                            js.tladdr, js.ConstInt(p_errno_ofs)
                        ))
                    )
                    save_addr = js.Plus(js.tladdr, js.ConstInt(rpy_errno_ofs))
                    self.bldr.emit_store(errno, save_addr, js.Int32)
                # Re-acquire the GIL.
                self.bldr.emit_expr(js.DynCallFunc("v", reacquire_addr, []))

    def genop_withguard_call_assembler(self, op, guardop):
        cpu = self.cpu
        descr = op.getdescr()
        assert isinstance(descr, JitCellToken)
        frame = self._get_jsval(op.getarg(0))
        if op.numargs() == 2:
            virtref = self._get_jsval(op.getarg(1))
        else:
            virtref = js.zero
        jd = descr.outermost_jitdriver_sd
        assert jd is not None
        with ctx_guard_not_forced(self, guardop):
            target_clt = descr.compiled_loop_token
            assert isinstance(target_clt, CompiledLoopTokenASMJS)
            # jitInvoke the target function.
            # This may produce a new frame object, capture it in a temp box.
            # XXX TODO: fragmentize this, so we can use latest funcid rather
            # than whatever it was at compile-time.
            funcid = js.ConstInt(target_clt.func.compiled_funcid)
            loopid = js.ConstInt(target_clt.compiled_loopid)
            resvar = self.bldr.allocate_intvar()
            with ctx_allow_gc(self):
                callargs = [funcid, frame, js.tladdr, loopid]
                call = js.CallFunc("jitInvoke", callargs)
                self.bldr.emit_assignment(resvar, call)
            # Load the descr resulting from that call.
            addr = js.FrameDescrAddr(resvar)
            resdescr = js.HeapData(js.Int32, addr)
            # Check if it's equal to done-with-this-frame.
            # The particular brand of DWTF depends on the result type.
            if op.result is None:
                dwtf = cpu.done_with_this_frame_descr_void
            else:
                kind = op.result.type
                if kind == INT:
                    dwtf = cpu.done_with_this_frame_descr_int
                elif kind == REF:
                    dwtf = cpu.done_with_this_frame_descr_ref
                elif kind == FLOAT:
                    dwtf = cpu.done_with_this_frame_descr_float
                else:
                    raise AssertionError(kind)
            if not dwtf:
                dwtf = js.zero
            else:
                gcref = cast_instance_to_gcref(dwtf)
                rgc._make_sure_does_not_move(gcref)
                dwtf = js.ConstInt(rffi.cast(lltype.Signed, gcref))
            with self.bldr.emit_if_block(js.Equal(resdescr, dwtf)):
                # If so, then we're on the happy fast path.
                # Reset the vable token  (whatever the hell that means...)
                # and return the result from the frame.
                if jd.index_of_virtualizable >= 0:
                    fielddescr = jd.vable_token_descr
                    assert isinstance(fielddescr, FieldDescr)
                    fieldaddr = js.Plus(virtref,
                                        js.ConstInt(fielddescr.offset))
                    self.bldr.emit_store(js.zero, fieldaddr, js.Int32)
                if op.result is not None:
                    kind = op.result.type
                    descr = cpu.getarraydescr_for_frame(kind)
                    offset = cpu.unpack_arraydescr(descr)
                    addr = js.Plus(resvar, js.ConstInt(offset))
                    typ = js.HeapType.from_kind(kind)
                    boxvar = self._allocate_box_variable(op.result)
                    self.bldr.emit_load(boxvar, addr, typ)
            # If not, then we need to invoke a helper function.
            with self.bldr.emit_else_block():
                if op.result is None:
                    callsig = "vii"
                elif op.result.type == FLOAT:
                    callsig = "dii"
                else:
                    callsig = "iii"
                args = [resvar, virtref]
                helpaddr = cpu.cast_adr_to_int(jd.assembler_helper_adr)
                helpaddr = js.ConstInt(helpaddr)
                with ctx_allow_gc(self, exclude=[op.result]):
                    call = js.DynCallFunc(callsig, helpaddr, args)
                    if op.result is None:
                        self.bldr.emit_expr(call)
                    else:
                        opresvar = self.box_variables[op.result]
                        self.bldr.emit_assignment(opresvar, call)
            # Cleanup.
            self.bldr.free_intvar(resvar)

    def _genop_call(self, op, descr, fnaddr, args):
        assert isinstance(descr, CallDescr)
        assert len(descr.arg_classes) == len(args)
        # Map CallDescr type tags into dynCall type tags.
        sigmap = {"i": "i", "r": "i", "f": "d", "v": "v"}
        callsig = sigmap[descr.result_type]
        i = 0
        while i < len(args):
            callsig += sigmap[descr.arg_classes[i]]
            i += 1
        with ctx_allow_gc(self, exclude=[op.result]):
            call = js.DynCallFunc(callsig, fnaddr, args)
            if op.result is None:
                self.bldr.emit_expr(call)
            else:
                if descr.get_result_type() == "i":
                    # Trim the result if it's a less-than-full-sized integer,
                    result_size = descr.get_result_size()
                    result_sign = descr.is_result_signed()
                    call = js.SignedCast(call)
                    call = js.cast_integer(call, result_size, result_sign)
                boxvar = self._allocate_box_variable(op.result)
                self.bldr.emit_assignment(boxvar, call)

    def _genop_math_sqrt(self, op):
        assert op.numargs() == 2
        arg = js.DoubleCast(self._get_jsval(op.getarg(1)))
        res = self._allocate_box_variable(op.result)
        self.bldr.emit_assignment(res, js.CallFunc("sqrt", [arg]))

    def _genop_math_read_timestamp(self, op):
        # Simulate processor time using gettimeofday().
        # XXX TODO: Probably this is all sorts of technically incorrect.
        # It needs to write into the heap, so we use the frame as scratch.
        os.write(2, "WARNING: genop_read_timestamp probably doesn't work\n")
        self.clt.func.ensure_frame_depth(2*WORD)
        addr = js.FrameSlotAddr(0)
        self.bldr.emit_expr(js.CallFunc("gettimeofday", [addr]))
        secs = js.HeapData(js.Int32, addr)
        micros = js.HeapData(js.Int32, js.FrameSlotAddr(WORD))
        millis = js.Div(micros, js.ConstInt(1000))
        millis = js.Plus(millis, js.IMul(secs, js.ConstInt(1000)))
        boxvar = self._allocate_box_variable(op.result)
        self.bldr.emit_assignment(boxvar, millis)

    def genop_force_token(self, op):
        if op.result is not None:
            if self._can_suspend_box(op.result):
                self._suspend_box_expression(op.result, js.frame)
            else:
                boxvar = self._allocate_box_variable(op.result)
                self.bldr.emit_assignment(boxvar, js.frame)

    def genop_jump(self, op):
        # Generate the final jump, if any.
        # Since all target loops will be in the same compiled function,
        # we can send arguments via boxes.
        argkinds = [box.type if box else HOLE for box in op.getarglist()]
        inputvars = self._get_inputvars_from_kinds(argkinds)
        self._genop_assign_to_vars(op.getarglist(), inputvars, argkinds)
        # The target blockid may change if we're reassembled.
        # Store a fragment and the descr so we can generate it correctly.
        fragment = self.bldr.capture_fragment()
        self.compiled_fragments.append(fragment)
        descr = op.getdescr()
        assert isinstance(descr, TargetToken)
        self.compiled_descrs.append(descr)

    def _genop_assign_to_vars(self, boxes, variables, kinds=None):
        """Atomically swap the given boxes into the given variables.

        This uses temp variables as necessary so that it's possible to e.g.
        switch the contents of two varables as part of this operation.
        """
        self.bldr.emit_comment("ASSIGNING TO %d VARS" % (len(boxes),))
        if kinds is None:
            kinds = [box.type if box else HOLE for box in boxes]
        # Each box may correspond to a suspended expression which in turn uses
        # several other variables that are also being assigned to.
        exprs = [self._get_jsval(box) for box in boxes]
        deps = [{} for expr in exprs]
        for i in range(len(exprs)):
            for var in js.iter_variables(exprs[i]):
                try:
                    j = variables.index(var)
                    if j != i:
                        deps[j][i] = True
                except ValueError:
                    pass
        # Now assign those expressions to the relevant variables, trying to do
        # them in dependency order so that we avoid the need for temporary
        # variables where possible.
        seen = {}
        temp = {}
        for i in range(len(exprs)):
            self._genop_assign_to_vars_in_dep_order(i, variables, kinds, exprs,
                                                    deps, seen, temp)
        if SANITYCHECK:
            assert len(temp) == 0
            assert len(seen) == len(exprs)
            for i in range(len(seen)):
                assert seen[i] == 2

    def _genop_assign_to_vars_in_dep_order(self, i, vars, kinds, exprs, deps,
                                           seen, temp):
        status = seen.get(i, 0)
        # If we've assigned that variable, there's nothing more to do.
        if status == 2:
            return
        # If we've already seen this var, but have not yet assigned to it,
        # then there's a dependency loop and we have to spill to a tempvar.
        if status == 1:
            if kinds[i] == FLOAT:
                temp[i] = self.bldr.allocate_doublevar()
            else:
                temp[i] = self.bldr.allocate_intvar()
            self.bldr.emit_assignment(temp[i], exprs[i])
            return
        # Evaluate anything that depends on this variable before
        # changing its value.
        if kinds[i] != HOLE:
            seen[i] = 1
            for j in deps[i]:
                self._genop_assign_to_vars_in_dep_order(j, vars, kinds, exprs,
                                                        deps, seen, temp)
            # If we spilled it to a tempvar, assign the value from there.
            # Otherwise we can evaluate its expression directly.
            if i not in temp:
                if exprs[i] != vars[i]:
                    self.bldr.emit_assignment(vars[i], exprs[i])
            else:
                self.bldr.emit_assignment(vars[i], temp[i])
                if kinds[i] == FLOAT:
                    self.bldr.free_doublevar(temp.pop(i))
                else:
                    self.bldr.free_intvar(temp.pop(i))
        seen[i] = 2

    def _emit_swap_vars(self, bldr, invars, outvars, kinds):
        """Atomically swap the given invars into the given outvars."""
        assert len(invars) == len(outvars)
        bldr.emit_comment("SWAPPING INTO %d VARS" % (len(invars),))
        swapvars = [None] * len(outvars)
        with ctx_temp_vars(bldr) as tmpvars:
            for i in range(len(invars)):
                if kinds[i] == HOLE:
                    continue
                # XXX TODO: don't use tempvars if we don't have to.
                try:
                    j = outvars.index(invars[i])
                except ValueError:
                    swapvars[i] = invars[i]
                else:
                    if j == i:
                        swapvars[i] = invars[i]
                    else:
                        swapvars[i] = tmpvars.allocate(kinds[i])
                        bldr.emit_assignment(swapvars[i], invars[i])
            for i in range(len(invars)):
                if kinds[i] == HOLE:
                    continue
                if swapvars[i] != outvars[i]:
                    bldr.emit_assignment(outvars[i], swapvars[i])

    def genop_guard_not_forced_2(self, op):
        # This is a special "guard" that is not really a guard, and
        # always appears directly before a FINISH.  It writes some
        # force state to the frame in a similar way to GUARD_NOT_FORCED,
        # but it is returned along with the result of FINISH rather than
        # being checked inline in the trace.
        # Store the force-descr where forcing code can find it.
        descr = op.getdescr()
        faildescr = js.ConstPtr(cast_instance_to_gcref(descr))
        self.bldr.emit_store(faildescr, js.FrameForceDescrAddr(), js.Int32)
        # Write the potential failargs into the frame.
        # We need to leave one slot at the start of the frame for
        # any output value from the subsequent FINISH.
        failargs = op.getfailargs()
        faillocs = self._get_frame_locations(failargs, offset=2*WORD)
        assert len(failargs) == len(faillocs)
        for i in xrange(len(failargs)):
            failarg = failargs[i]
            # Careful, some boxes may not have a value yet.
            if failarg:
                if failarg not in self.box_variables:
                    if failarg not in self.box_expressions:
                        continue
            self._genop_spill_to_frame(failarg, faillocs[i])
        # The subsequent FINISH will store the necessary gcmap.
        self._prepare_guard_op(op, faillocs)

    def genop_finish(self, op):
        # Write return value into the frame.
        if op.numargs() > 0:
            assert op.numargs() == 1
            result = op.getarg(0)
            self.bldr.emit_comment("WRITE RESULT")
            with ctx_spill_to_frame(self):
                # XXX TODO: wtf is up with force_spill anyway?
                offset = 0 #self.forced_spill_frame_offset
                curval = self.spilled_frame_values.get(offset, None)
                if False and curval is not None:
                    if SANITYCHECK:
                        assert curval == result
                else:
                    self._genop_spill_to_frame(result, offset)
                self._genop_store_gcmap()
        # Write the descr into the frame slot.
        descr = op.getdescr()
        addr = js.FrameDescrAddr()
        if descr is None:
            self.bldr.emit_store(js.zero, addr, js.Int32)
        else:
            gcdescr = cast_instance_to_gcref(descr)
            rgc._make_sure_does_not_move(gcdescr)
            self.bldr.emit_store(js.ConstPtr(gcdescr), addr, js.Int32)
        self.bldr.emit_exit()

    #
    # Guard-related things.
    #

    def genop_guard_true(self, op):
        test = js.UNot(self._get_jsval(op.getarg(0)))
        self._genop_guard_failure(test, op)

    def genop_guard_isnull(self, op):
        test = js.NotEqual(self._get_jsval(op.getarg(0)), js.zero)
        self._genop_guard_failure(test, op)

    def genop_guard_nonnull(self, op):
        test = js.UNot(self._get_jsval(op.getarg(0)))
        self._genop_guard_failure(test, op)

    def genop_guard_false(self, op):
        test = self._get_jsval(op.getarg(0))
        self._genop_guard_failure(test, op)

    def genop_guard_value(self, op):
        test = js.NotEqual(self._get_jsval(op.getarg(0)),
                           self._get_jsval(op.getarg(1)))
        self._genop_guard_failure(test, op)

    def genop_guard_class(self, op):
        objptr = self._get_jsval(op.getarg(0))
        clsptr = self._get_jsval(op.getarg(1))
        test = self._genop_expr_not_has_class(objptr, clsptr)
        self._genop_guard_failure(test, op)

    def _genop_expr_not_has_class(self, objptr, clsptr):
        # If compiled without type pointers, we have to read the "typeid"
        # from the first half-word of the object and compare it to the
        # expected typeid for the class.
        offset = self.cpu.vtable_offset
        if offset is not None:
            objcls = js.HeapData(js.Int32, js.Plus(objptr,
                                                   js.ConstInt(offset)))
            test = js.NotEqual(objcls, clsptr)
        else:
            typeid = js.And(js.HeapData(js.Int32, objptr), js.ConstInt(0xFFFF))
            test = js.NotEqual(typeid, js.ClassPtrTypeID(clsptr))
        return test

    def genop_guard_nonnull_class(self, op):
        # This is essentially short-circuiting logical or: the guard must
        # fail if the ref is null or it does not have the appropriate class.
        # Since asmjs doesn't have short-circuiting logical operators, we
        # simulate it with a temporary variable.
        objptr = self._get_jsval(op.getarg(0))
        clsptr = self._get_jsval(op.getarg(1))
        testvar = self.bldr.allocate_intvar()
        self.bldr.emit_assignment(testvar, objptr)
        with self.bldr.emit_if_block(testvar):
            not_has_class = self._genop_expr_not_has_class(objptr, clsptr)
            self.bldr.emit_assignment(testvar, not_has_class)
        with self.bldr.emit_else_block():
            self.bldr.emit_assignment(testvar, js.UNot(testvar))
        self._genop_guard_failure(testvar, op)
        self.bldr.free_intvar(testvar)

    def genop_guard_exception(self, op):
        pos_exctyp = js.ConstInt(self.cpu.pos_exception())
        pos_excval = js.ConstInt(self.cpu.pos_exc_value())
        exctyp = js.HeapData(js.Int32, pos_exctyp)
        excval = js.HeapData(js.Int32, pos_excval)
        test = js.NotEqual(exctyp, self._get_jsval(op.getarg(0)))
        self._genop_guard_failure(test, op)
        if op.result is not None:
            boxvar = self._allocate_box_variable(op.result)
            self.bldr.emit_assignment(boxvar, excval)
        self.bldr.emit_store(js.zero, pos_exctyp, js.Int32)
        self.bldr.emit_store(js.zero, pos_excval, js.Int32)

    def genop_guard_no_exception(self, op):
        pos_exctyp = js.ConstInt(self.cpu.pos_exception())
        exctyp = js.HeapData(js.Int32, pos_exctyp)
        test = js.NotEqual(exctyp, js.zero)
        self._genop_guard_failure(test, op)

    def genop_guard_not_invalidated(self, op):
        translate_support_code = self.cpu.translate_support_code
        offset, size = symbolic.get_field_token(INVALIDATION,
                                                "counter",
                                                translate_support_code)
        assert size == js.Int32.size
        invalidation = rffi.cast(lltype.Signed, self.clt.invalidation)
        cur_val = js.HeapData(js.Int32, js.Plus(js.ConstInt(invalidation),
                                                js.ConstInt(offset)))
        orig_val = js.ConstInt(self.initial_invalidation_counter)
        test = js.NotEqual(cur_val, orig_val)
        self._genop_guard_failure(test, op)

    def _genop_guard_failure(self, test, op, faillocs=None):
        descr = self._prepare_guard_op(op, faillocs)
        failargs = op.getfailargs()
        failkinds = descr._asmjs_failkinds
        for i in range(len(failargs)):
            if isinstance(failargs[i], Box):
                self._genop_flush_box(failargs[i])
        with self.bldr.emit_if_block(test):
            # Place the failargs into a suite of known vars, so that the
            # dynamically-generated code for the guard can find them.
            with ctx_temp_vars(self.bldr) as tmpvars:
                for i in range(len(failargs)):
                    if failargs[i]:
                        failval = self._get_jsval(failargs[i])
                        if isinstance(failval, js.Variable):
                            descr._asmjs_failvars[i] = failval
                        else:
                            failvar = tmpvars.allocate(failkinds[i])
                            self.bldr.emit_assignment(failvar, failval)
                            descr._asmjs_failvars[i] = failvar
                # Now store all code generated so far into a fragment.
                # Then we can easily output it at each re-assembly.
                # This has to be *inside* the if-statement.
                fragment = self.bldr.capture_fragment()
        # Store minimal refs necessary to re-construct the guard failure code.
        self.compiled_fragments.append(fragment)
        self.compiled_descrs.append(descr)

    def _prepare_guard_op(self, op, faillocs=None):
        descr = op.getdescr()
        assert isinstance(descr, AbstractFailDescr)
        failargs = op.getfailargs()
        failkinds = [box.type if box else HOLE for box in failargs]
        if faillocs is None:
            spill_offset = self.forced_spill_frame_offset
            faillocs = self._get_framelocs_from_kinds(failkinds, spill_offset)
        gcmap = self.allocate_gcmap_from_kinds(failkinds, faillocs)
        descr._asmjs_failkinds = failkinds
        descr._asmjs_faillocs = faillocs
        descr._asmjs_failvars = [None] * len(faillocs)
        descr._asmjs_hasexc = self._guard_might_have_exception(op)
        descr._asmjs_gcmap = gcmap
        return descr

    def _guard_might_have_exception(self, op):
        opnum = op.getopnum()
        if opnum == rop.GUARD_EXCEPTION:
            return True
        if opnum == rop.GUARD_NO_EXCEPTION:
            return True
        if opnum == rop.GUARD_NOT_FORCED:
            return True
        return False

    def _genop_check_and_propagate_exception(self):
        pos_exctyp = js.ConstInt(self.cpu.pos_exception())
        exctyp = js.HeapData(js.Int32, pos_exctyp)
        with self.bldr.emit_if_block(exctyp):
            self._genop_propagate_exception()

    def _genop_propagate_exception(self):
        self.bldr.emit_comment("PROPAGATE EXCEPTION")
        cpu = self.cpu
        if not cpu.propagate_exception_descr:
            return
        pos_exctyp = js.ConstInt(cpu.pos_exception())
        pos_excval = js.ConstInt(cpu.pos_exc_value())
        excval = js.HeapData(js.Int32, pos_excval)
        # Store the exception on the frame, and clear it.
        self.bldr.emit_store(excval, js.FrameGuardExcAddr(), js.Int32)
        self.bldr.emit_store(js.zero, pos_exctyp, js.Int32)
        self.bldr.emit_store(js.zero, pos_excval, js.Int32)
        # Store the special propagate-exception descr on the frame.
        descr = cast_instance_to_gcref(cpu.propagate_exception_descr)
        addr = js.FrameDescrAddr()
        self.bldr.emit_store(js.ConstPtr(descr), addr, js.Int32)
        # Bail back to the invoking code to deal with it.
        self._genop_store_gcmap()
        self.bldr.emit_exit()

    #
    #  GC-related things.
    #

    def genop_call_malloc_nursery(self, op):
        sizebox = op.getarg(0)
        # Sanity-check for correct alignment.
        assert isinstance(sizebox, ConstInt)
        size = sizebox.getint()
        assert size & (WORD-1) == 0
        self._genop_malloc_nursery(op, sizebox)

    def genop_call_malloc_nursery_varsize_frame(self, op):
        sizebox = op.getarg(0)
        self._genop_malloc_nursery(op, sizebox)

    def _genop_malloc_nursery(self, op, sizebox):
        gc_ll_descr = self.cpu.gc_ll_descr
        sizevar = self._genop_flush_box(sizebox)
        sizevar = self._emit_round_up_for_allocation(sizevar)
        # This is essentially an in-lining of MiniMark.malloc_fixedsize_clear()
        nfree_addr = js.ConstInt(gc_ll_descr.get_nursery_free_addr())
        ntop_addr = js.ConstInt(gc_ll_descr.get_nursery_top_addr())
        nfree = js.HeapData(js.Int32, nfree_addr)
        ntop = js.HeapData(js.Int32, ntop_addr)
        # Optimistically, we can just use the space at nursery_free.
        resvar = self._allocate_box_variable(op.result)
        self.bldr.emit_assignment(resvar, nfree)
        new_nfree = self.bldr.allocate_intvar()
        self.bldr.emit_assignment(new_nfree, js.Plus(resvar, sizevar))
        # But we have to check whether we overflowed nursery_top.
        with self.bldr.emit_if_block(js.LessThanEq(new_nfree, ntop)):
            # If we didn't, we're all good, just increment nursery_free.
            self.bldr.emit_store(new_nfree, nfree_addr, js.Int32)
        with self.bldr.emit_else_block():
            # If we did, we have to call into the GC for a collection.
            # The tests sometimes require that we pass along the jitfame.
            mallocfnaddr = self.clt.assembler.gc_malloc_nursery_addr
            mallocfn = rffi.cast(lltype.Signed, mallocfnaddr)
            if hasattr(gc_ll_descr, 'passes_frame'):
                callsig = "iii"
                args = [sizevar, js.frame]
            else:
                callsig = "ii"
                args = [sizevar]
            with ctx_allow_gc(self, exclude=[op.result]):
                call = js.DynCallFunc(callsig, js.ConstInt(mallocfn), args)
                self.bldr.emit_assignment(resvar, call)
            self._genop_check_and_propagate_exception()
        # Cleanup temp vars.
        self.bldr.free_intvar(new_nfree)

    def genop_call_malloc_nursery_varsize(self, op):
        gc_ll_descr = self.cpu.gc_ll_descr
        arraydescr = op.getdescr()
        assert isinstance(arraydescr, ArrayDescr)
        if hasattr(gc_ll_descr, 'minimal_size_in_nursery'):
            assert arraydescr.basesize >= gc_ll_descr.minimal_size_in_nursery
        kind = op.getarg(0).getint()
        itemsize = op.getarg(1).getint()
        lengthbox = op.getarg(2)
        assert isinstance(lengthbox, BoxInt)
        lengthvar = self._genop_flush_box(lengthbox)
        # Figure out the total size to be allocated.
        # It's gcheader + basesize + length*itemsize, maybe with some padding.
        if hasattr(gc_ll_descr, 'gcheaderbuilder'):
            size_of_header = gc_ll_descr.gcheaderbuilder.size_gc_header
        else:
            size_of_header = WORD
        constsize = size_of_header + arraydescr.basesize
        calc_totalsize = js.Plus(js.ConstInt(constsize),
                                 js.IMul(lengthvar, js.ConstInt(itemsize)))
        totalsize = self.bldr.allocate_intvar()
        self.bldr.emit_assignment(totalsize, calc_totalsize)
        totalsize = self._emit_round_up_for_allocation(totalsize)
        # This is essentially an in-lining of MiniMark.malloc_varsize_clear()
        nfree_addr = js.ConstInt(gc_ll_descr.get_nursery_free_addr())
        ntop_addr = js.ConstInt(gc_ll_descr.get_nursery_top_addr())
        nfree = js.HeapData(js.Int32, nfree_addr)
        ntop = js.HeapData(js.Int32, ntop_addr)
        maxsize = js.ConstInt(gc_ll_descr.max_size_of_young_obj - WORD * 2)
        # Optimistically, we can just use the space at nursery_free.
        resvar = self._allocate_box_variable(op.result)
        self.bldr.emit_assignment(resvar, nfree)
        new_nfree = self.bldr.allocate_intvar()
        self.bldr.emit_assignment(new_nfree, js.Plus(resvar, totalsize))
        # But we have to check whether we overflowed nursery_top,
        # or created an object too large for the nursery.
        chk_not_overflowed = js.And(js.LessThanEq(new_nfree, ntop),
                                    js.LessThan(totalsize, maxsize))
        with self.bldr.emit_if_block(chk_not_overflowed):
            # If we fit in the nursery, we're all good!
            # Increment nursery_free and set type flags on the object.
            self.bldr.emit_store(new_nfree, nfree_addr, js.Int32)
            self.bldr.emit_store(js.ConstInt(arraydescr.tid), resvar, js.Int32)
        with self.bldr.emit_else_block():
            # If it didn't fit in the nursery, we have to call out to malloc.
            if kind == rewrite.FLAG_ARRAY:
                args = [js.ConstInt(itemsize),
                        js.ConstInt(arraydescr.tid),
                        lengthvar]
                callsig = "iiii"
                mallocfn = self.clt.assembler.gc_malloc_array_addr
            else:
                args = [lengthvar]
                callsig = "ii"
                if kind == rewrite.FLAG_STR:
                    mallocfn = self.clt.assembler.gc_malloc_str_addr
                else:
                    assert kind == rewrite.FLAG_UNICODE
                    mallocfn = self.clt.assembler.gc_malloc_unicode_addr
            mallocfn = js.ConstInt(rffi.cast(lltype.Signed, mallocfn))
            with ctx_allow_gc(self, exclude=[op.result]):
                call = js.DynCallFunc(callsig, mallocfn, args)
                self.bldr.emit_assignment(resvar, call)
            self._genop_check_and_propagate_exception()
        # That's it!  Cleanup temp variables.
        self.bldr.free_intvar(new_nfree)
        self.bldr.free_intvar(totalsize)

    def _emit_round_up_for_allocation(self, sizeval):
        # For proper alignment of doubles, we must always allocate
        # in 8-byte chunks.  Otherwise we'd leave the nursery badly
        # aligned for subsequent allocations.
        if isinstance(sizeval, js.ConstInt):
            size = sizeval.getint()
            sizeval = js.ConstInt((size + (2 * WORD - 1)) & ~(2 * WORD - 1))
        else:
            assert isinstance(sizeval, js.Variable)
            # XXX TODO: can we avoid checking this in some cases?
            # e.g. if itemsize is known to be a multiple of dword
            align = js.Minus(js.dword, js.ConstInt(1))
            self.bldr.emit_assignment(sizeval, js.And(js.Plus(sizeval, align),
                                                      js.UNeg(align)))
        return sizeval

    def genop_cond_call_gc_wb(self, op):
        assert op.result is None
        self._genop_write_barrier(op.getarglist(), op.getdescr())

    def genop_cond_call_gc_wb_array(self, op):
        assert op.result is None
        self._genop_write_barrier(op.getarglist(), op.getdescr(), array=True)

    def _genop_write_barrier(self, arguments, wbdescr=None, array=False):
        jsargs = [self._get_jsval(arg) for arg in arguments]
        self.emit_write_barrier(self.bldr, jsargs, wbdescr, array)

    def _genop_store_gcmap(self, writebarrier=True):
        """Push a gcmap representing current spilled state of frame."""
        # Check if a gcmap is actually needed.
        gcmap = jitframe.NULLGCMAP
        for pos, box in self.spilled_frame_values.iteritems():
            if box and box.type == REF:
                gcmap = self.allocate_gcmap(self.spilled_frame_offset)
        # Set a bit for every REF that has been spilled.
        if gcmap != jitframe.NULLGCMAP:
            for pos, box in self.spilled_frame_values.iteritems():
                if box and box.type == REF:
                    pos = pos // WORD
                    gcmap[pos // WORD // 8] |= r_uint(1) << (pos % (WORD * 8))
        self.emit_store_gcmap(self.bldr, gcmap, writebarrier=writebarrier)

    def genop_jit_debug(self, op):
        pass

    def genop_keepalive(self, op):
        pass

    def genop_force_spill(self, op):
        # This is used by tests.
        # The item will stay spilled to the frame forever.
        self.bldr.emit_comment("FORCE SPILL: %s" % (op,))
        self._genop_spill_to_frame(op.getarg(0))
        self.forced_spill_frame_offset = self.spilled_frame_offset

    def genop_increment_debug_counter(self, op):
        # Argument is an address; increment its contents.
        addr = self._get_jsval(op.getarg(0))
        newval = js.Plus(js.HeapData(js.UInt32, addr), js.ConstInt(1))
        self.bldr.emit_store(newval, addr, js.UInt32)

    def genop_enter_portal_frame(self, op):
        self.clt.assembler.enter_portal_frame(op)

    def genop_leave_portal_frame(self, op):
        self.clt.assembler.leave_portal_frame(op)

    def not_implemented_op_withguard(self, op, guardop):
        self.not_implemented_op(op)
        self.not_implemented_op(guardop)

    def not_implemented_op_expr(self, op):
        self.not_implemented_op(op)
        return js.zero

    def not_implemented_op(self, op):
        self._print_op(op)
        raise NotImplementedError

    def _print_op(self, op):
        os.write(2, "OPERATION: %s\n" % (op,))
        for i in range(op.numargs()):
            os.write(2, "  ARG: %s\n" % (op.getarg(i),))
        os.write(2, "  RES: %s\n" % (op.result,))


class ctx_spill_to_frame(object):

    def __init__(self, block):
        self.block = block
        self.orig_spilled_frame_offset = 0

    def _get_jsval(self, jitval):
        return self.block._get_jsval(jitval)

    def __enter__(self):
        # Remember the current offset, so we can pop all items
        # after this when we exit the context.
        self.orig_spilled_frame_offset = self.block.spilled_frame_offset
        return self

    def __exit__(self, exc_typ, exc_val, exc_tb):
        # Pop any items that were pushed in this context.
        orig_offset = self.orig_spilled_frame_offset
        self.block.clt.func.ensure_frame_depth(self.block.spilled_frame_offset)
        for pos, box in self.block.spilled_frame_values.items():
            if pos >= orig_offset:
                del self.block.spilled_frame_values[pos]
                self.block.spilled_frame_locations[box].remove(pos)
                if not self.block.spilled_frame_locations[box]:
                    del self.block.spilled_frame_locations[box]
        self.block.spilled_frame_offset = orig_offset

    def is_spilled(self, box):
        try:
            self.block.spilled_frame_locations[box]
        except KeyError:
            return False
        else:
            return True

    def genop_spill_to_frame(self, box, offset=-1):
        self.block._genop_spill_to_frame(box, offset)


class ctx_guard_not_forced(ctx_spill_to_frame):

    def __init__(self, block, guardop):
        ctx_spill_to_frame.__init__(self, block)
        self.faillocs = None
        self.guardop = guardop

    def __enter__(self):
        ctx_spill_to_frame.__enter__(self)
        bldr = self.block.bldr
        # Store the force-descr where forcing code can find it.
        descr = self.guardop.getdescr()
        faildescr = js.ConstPtr(cast_instance_to_gcref(descr))
        bldr.emit_store(faildescr, js.FrameForceDescrAddr(), js.Int32)
        # Write the potential failargs into the frame.
        # We have to spill them here because the forcing logic might
        # need to read them out to populate the virtualizable.
        # They must be spilled at their final output location.
        assert self.orig_spilled_frame_offset == 0
        failargs = self.guardop.getfailargs()
        self.faillocs = self.block._get_frame_locations(failargs)
        assert len(failargs) == len(self.faillocs)
        for i in xrange(len(failargs)):
            failarg = failargs[i]
            # Careful, some boxes may not have a value yet.
            if failarg:
                if failarg not in self.block.box_variables:
                    if failarg not in self.block.box_expressions:
                        continue
            self.genop_spill_to_frame(failarg, self.faillocs[i])
        # Note that we don't need to store a gcmap here.
        # That will be taken care of by the enclosed call operation.
        return self

    def __exit__(self, exc_typ, exc_val, exc_tb):
        # Emit the guard check, testing for whether jf_descr has been set.
        descr = js.HeapData(js.Int32, js.FrameDescrAddr())
        test = js.NotEqual(descr, js.zero)
        self.block._genop_guard_failure(test, self.guardop, self.faillocs)
        self.faillocs = None
        # It's now safe to pop from the frame as usual.
        ctx_spill_to_frame.__exit__(self, exc_typ, exc_val, exc_tb)


class ctx_allow_gc(ctx_spill_to_frame):

    def __init__(self, block, exclude=None):
        ctx_spill_to_frame.__init__(self, block)
        self.exclude = exclude

    def __enter__(self):
        ctx_spill_to_frame.__enter__(self)
        bldr = self.block.bldr
        # Spill any active REF boxes into the frame.
        for box, jsval in self._get_live_boxes_in_spill_order():
            if not box or box.type != REF:
                continue
            if self.exclude is not None and box in self.exclude:
                continue
            if jsval == js.frame:
                continue
            if self.block._is_final_use(box, self.block.pos):
                continue
            if not self.is_spilled(box):
                self.genop_spill_to_frame(box)
        self.block._genop_store_gcmap()
        # Push the jitframe itself onto the gc shadowstack.
        # We do the following:
        #   * get a pointer to the pointer to the root-stack top.
        #   * deref it to get the root-stack top, and write the frame there.
        #   * in-place increment root-stack top via its pointer.
        gcrootmap = self.block.cpu.gc_ll_descr.gcrootmap
        if gcrootmap and gcrootmap.is_shadow_stack:
            rstaddr = js.ConstInt(gcrootmap.get_root_stack_top_addr())
            rstexpr = js.HeapData(js.Int32, rstaddr)
            with ctx_temp_intvar(bldr, rstexpr) as rstvar:
                bldr.emit_store(js.frame, rstvar, js.Int32)
                newrst = js.Plus(rstvar, js.word)
                bldr.emit_store(newrst, rstaddr, js.Int32)
        return self

    def __exit__(self, exc_typ, exc_val, exc_tb):
        bldr = self.block.bldr
        # Pop the jitframe from the root-stack.
        # This is an in-place decrement of root-stack top.
        gcrootmap = self.block.cpu.gc_ll_descr.gcrootmap
        if gcrootmap and gcrootmap.is_shadow_stack:
            rstaddr = js.ConstInt(gcrootmap.get_root_stack_top_addr())
            rst = js.HeapData(js.Int32, rstaddr)
            newrst = js.Minus(rst, js.word)
            bldr.emit_store(newrst, rstaddr, js.Int32)
            # For moving GCs, the address of the jitframe may have changed.
            # Read the possibly-updated address out of root-stack top.
            # NB: this instruction re-evaluates the HeapData expression in rst.
            bldr.emit_assignment(js.frame, js.HeapData(js.Int32, rst))
        # Similarly, read potential new addresss of any spilled boxes.
        # XXX TODO: don't double-load boxes that appear multiple times.
        for pos, box in self.block.spilled_frame_values.iteritems():
            if not box or box.type != REF:
                continue
            if self.exclude is not None and box in self.exclude:
                continue
            addr = js.FrameSlotAddr(pos)
            bldr.emit_load(self._get_jsval(box), addr, js.Int32)
        # It's now safe to pop from the frame as usual.
        ctx_spill_to_frame.__exit__(self, exc_typ, exc_val, exc_tb)

    def _get_live_boxes_in_spill_order(self):
        # Some tests expect boxes to be spilled in order of use.
        # We fake it by ordering them lexicographically by name.
        if we_are_translated():
            for item in self.block.box_variables.iteritems():
                yield item
            for item in self.block.box_expressions.iteritems():
                if item[0] not in self.block.box_variables:
                    yield item
        else:
            items = list(self.block.box_variables.iteritems())
            for item in self.block.box_expressions.iteritems():
                if item[0] not in self.block.box_variables:
                    items.append(item)
            items.sort(key=lambda i: str(i[0]))
            for item in items:
                yield item


class ctx_preserve_exception(object):

    def __init__(self, clt, bldr):
        self.clt = clt
        self.bldr = bldr
        self.pos_exctyp = js.ConstInt(clt.cpu.pos_exception())
        self.pos_excval = js.ConstInt(clt.cpu.pos_exc_value())
        self.var_exctyp = self.bldr.allocate_intvar()

    def __enter__(self):
        exctyp = js.HeapData(js.Int32, self.pos_exctyp)
        excval = js.HeapData(js.Int32, self.pos_excval)
        self.bldr.emit_assignment(self.var_exctyp, exctyp)
        with self.bldr.emit_if_block(self.var_exctyp):
            self.bldr.emit_store(excval, js.FrameGuardExcAddr(), js.Int32)
            self.bldr.emit_store(js.zero, self.pos_exctyp, js.Int32)
            self.bldr.emit_store(js.zero, self.pos_excval, js.Int32)

    def __exit__(self, exc_typ, exc_val, exc_tb):
        excval = js.HeapData(js.Int32, js.FrameGuardExcAddr())
        with self.bldr.emit_if_block(self.var_exctyp):
            self.bldr.emit_store(self.var_exctyp, self.pos_exctyp, js.Int32)
            self.bldr.emit_store(excval, self.pos_excval, js.Int32)
            self.bldr.emit_store(js.zero, js.FrameGuardExcAddr(), js.Int32)
        self.bldr.free_intvar(self.var_exctyp)


class ctx_temp_intvar(object):

    def __init__(self, bldr, expr=None):
        self.bldr = bldr
        self.variable = None
        self.expr = expr

    def __enter__(self):
        self.variable = self.bldr.allocate_intvar()
        if self.expr is not None:
            self.bldr.emit_assignment(self.variable, self.expr)
        return self.variable

    def __exit__(self, exc_typ, exc_val, exc_tb):
        self.bldr.free_intvar(self.variable)


class ctx_temp_doublevar(object):

    def __init__(self, bldr, expr=None):
        self.bldr = bldr
        self.variable = None
        self.expr = expr

    def __enter__(self):
        self.variable = self.bldr.allocate_doublevar()
        if self.expr is not None:
            self.bldr.emit_assignment(self.variable, self.expr)
        return self.variable

    def __exit__(self, exc_typ, exc_val, exc_tb):
        self.bldr.free_doublevar(self.variable)


class ctx_temp_vars(object):

    def __init__(self, bldr):
        self.bldr = bldr
        self.intvars = []
        self.doublevars = []

    def __enter__(self):
        return self

    def allocate(self, kind):
        if kind == FLOAT:
            var = self.bldr.allocate_doublevar()
            self.doublevars.append(var)
        else:
            var = self.bldr.allocate_intvar()
            self.intvars.append(var)
        return var

    def __exit__(self, exc_typ, exc_val, exc_tb):
        for var in self.intvars:
            self.bldr.free_intvar(var)
        for var in self.doublevars:
            self.bldr.free_doublevar(var)


# Build a dispatch table mapping opnums to the method that emits code for them.
# There are three different method signatures, depending on whether we can work
# with a single opcode, require an opcode/guard pair, or generate a single
# simple expression.

genop_list = [CompiledBlockASMJS.not_implemented_op] * rop._LAST
genop_expr_list = [CompiledBlockASMJS.not_implemented_op_expr] * rop._LAST
genop_withguard_list = [CompiledBlockASMJS.not_implemented_op_withguard] * rop._LAST

for name, value in CompiledBlockASMJS.__dict__.iteritems():
    if name == "genop_force_spill":
        continue
    elif name.startswith('genop_withguard_'):
        opname = name[len('genop_withguard_'):]
        num = getattr(rop, opname.upper())
        genop_withguard_list[num] = value
    elif name.startswith('genop_expr_'):
        opname = name[len('genop_expr_'):]
        num = getattr(rop, opname.upper())
        genop_expr_list[num] = value
    elif name.startswith('genop_'):
        opname = name[len('genop_'):]
        num = getattr(rop, opname.upper())
        genop_list[num] = value
