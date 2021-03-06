from rpython.annotator import model as annmodel
from rpython.rtyper.llannotation import SomePtr
from rpython.rlib.debug import ll_assert
from rpython.rlib.nonconst import NonConstant
from rpython.rlib import rgc
from rpython.rtyper import rmodel
from rpython.rtyper.annlowlevel import llhelper
from rpython.rtyper.lltypesystem import lltype, llmemory
from rpython.rtyper.llannotation import SomeAddress
from rpython.memory.gctransform.framework import (
     BaseFrameworkGCTransformer, BaseRootWalker, sizeofaddr)
from rpython.rtyper.rbuiltin import gen_cast


class ShadowStackFrameworkGCTransformer(BaseFrameworkGCTransformer):
    def annotate_walker_functions(self, getfn):
        self.incr_stack_ptr = getfn(self.root_walker.incr_stack,
                                   [annmodel.SomeInteger()],
                                   SomeAddress(),
                                   inline = True)
        self.decr_stack_ptr = getfn(self.root_walker.decr_stack,
                                   [annmodel.SomeInteger()],
                                   SomeAddress(),
                                   inline = True)

    def build_root_walker(self):
        return ShadowStackRootWalker(self)

    def push_roots(self, hop, keep_current_args=False):
        livevars = self.get_livevars_for_roots(hop, keep_current_args)
        self.num_pushs += len(livevars)
        if not livevars:
            return []
        c_len = rmodel.inputconst(lltype.Signed, len(livevars) )
        base_addr = hop.genop("direct_call", [self.incr_stack_ptr, c_len ],
                              resulttype=llmemory.Address)
        for k,var in enumerate(livevars):
            c_k = rmodel.inputconst(lltype.Signed, k * sizeofaddr)
            v_adr = gen_cast(hop.llops, llmemory.Address, var)
            hop.genop("raw_store", [base_addr, c_k, v_adr])
        return livevars

    def pop_roots(self, hop, livevars):
        if not livevars:
            return
        c_len = rmodel.inputconst(lltype.Signed, len(livevars) )
        base_addr = hop.genop("direct_call", [self.decr_stack_ptr, c_len ],
                              resulttype=llmemory.Address)
        if self.gcdata.gc.moving_gc:
            # for moving collectors, reload the roots into the local variables
            for k,var in enumerate(livevars):
                c_k = rmodel.inputconst(lltype.Signed, k * sizeofaddr)
                v_newaddr = hop.genop("raw_load", [base_addr, c_k],
                                      resulttype=llmemory.Address)
                hop.genop("gc_reload_possibly_moved", [v_newaddr, var])


class ShadowStackRootWalker(BaseRootWalker):
    def __init__(self, gctransformer):
        BaseRootWalker.__init__(self, gctransformer)
        # NB. 'self' is frozen, but we can use self.gcdata to store state
        gcdata = self.gcdata

        def incr_stack(n):
            top = gcdata.root_stack_top
            gcdata.root_stack_top = top + n*sizeofaddr
            return top
        self.incr_stack = incr_stack

        def decr_stack(n):
            top = gcdata.root_stack_top - n*sizeofaddr
            gcdata.root_stack_top = top
            return top
        self.decr_stack = decr_stack

        def walk_stack_root(callback, start, end):
            gc = self.gc
            addr = end
            while addr != start:
                addr -= sizeofaddr
                if gc.points_to_valid_gc_object(addr):
                    callback(gc, addr)
        self.rootstackhook = walk_stack_root

        self.shadow_stack_pool = ShadowStackPool(gcdata)
        rsd = gctransformer.root_stack_depth
        if rsd is not None:
            self.shadow_stack_pool.root_stack_depth = rsd

    def push_stack(self, addr):
        top = self.incr_stack(1)
        top.address[0] = addr

    def pop_stack(self):
        top = self.decr_stack(1)
        return top.address[0]

    def setup_root_walker(self):
        self.shadow_stack_pool.initial_setup()
        BaseRootWalker.setup_root_walker(self)

    def walk_stack_roots(self, collect_stack_root, is_minor=False):
        gcdata = self.gcdata
        self.rootstackhook(collect_stack_root,
                           gcdata.root_stack_base, gcdata.root_stack_top)

    def need_thread_support(self, gctransformer, getfn):
        from rpython.rlib import rthread    # xxx fish
        gcdata = self.gcdata
        # the interfacing between the threads and the GC is done via
        # two completely ad-hoc operations at the moment:
        # gc_thread_run and gc_thread_die.  See docstrings below.

        shadow_stack_pool = self.shadow_stack_pool
        SHADOWSTACKREF = get_shadowstackref(self, gctransformer)

        # this is a dict {tid: SHADOWSTACKREF}, where the tid for the
        # current thread may be missing so far
        gcdata.thread_stacks = None

        # Return the thread identifier, as an integer.
        get_tid = rthread.get_ident

        def thread_setup():
            tid = get_tid()
            gcdata.main_tid = tid
            gcdata.active_tid = tid

        def thread_run():
            """Called whenever the current thread (re-)acquired the GIL.
            This should ensure that the shadow stack installed in
            gcdata.root_stack_top/root_stack_base is the one corresponding
            to the current thread.
            No GC operation here, e.g. no mallocs or storing in a dict!

            Note that here specifically we don't call rthread.get_ident(),
            but rthread.get_or_make_ident().  We are possibly in a fresh
            new thread, so we need to be careful.
            """
            tid = rthread.get_or_make_ident()
            if gcdata.active_tid != tid:
                switch_shadow_stacks(tid)

        def thread_die():
            """Called just before the final GIL release done by a dying
            thread.  After a thread_die(), no more gc operation should
            occur in this thread.
            """
            tid = get_tid()
            if tid == gcdata.main_tid:
                return   # ignore calls to thread_die() in the main thread
                         # (which can occur after a fork()).
            # we need to switch somewhere else, so go to main_tid
            gcdata.active_tid = gcdata.main_tid
            thread_stacks = gcdata.thread_stacks
            new_ref = thread_stacks[gcdata.active_tid]
            try:
                del thread_stacks[tid]
            except KeyError:
                pass
            # no more GC operation from here -- switching shadowstack!
            shadow_stack_pool.forget_current_state()
            shadow_stack_pool.restore_state_from(new_ref)

        def switch_shadow_stacks(new_tid):
            # we have the wrong shadowstack right now, but it should not matter
            thread_stacks = gcdata.thread_stacks
            try:
                if thread_stacks is None:
                    gcdata.thread_stacks = thread_stacks = {}
                    raise KeyError
                new_ref = thread_stacks[new_tid]
            except KeyError:
                new_ref = lltype.nullptr(SHADOWSTACKREF)
            try:
                old_ref = thread_stacks[gcdata.active_tid]
            except KeyError:
                # first time we ask for a SHADOWSTACKREF for this active_tid
                old_ref = shadow_stack_pool.allocate(SHADOWSTACKREF)
                thread_stacks[gcdata.active_tid] = old_ref
            #
            # no GC operation from here -- switching shadowstack!
            shadow_stack_pool.save_current_state_away(old_ref, llmemory.NULL)
            if new_ref:
                shadow_stack_pool.restore_state_from(new_ref)
            else:
                shadow_stack_pool.start_fresh_new_state()
            # done
            #
            gcdata.active_tid = new_tid
        switch_shadow_stacks._dont_inline_ = True

        def thread_after_fork(result_of_fork, opaqueaddr):
            # we don't need a thread_before_fork in this case, so
            # opaqueaddr == NULL.  This is called after fork().
            if result_of_fork == 0:
                # We are in the child process.  Assumes that only the
                # current thread survived, so frees the shadow stacks
                # of all the other ones.
                gcdata.thread_stacks = None
                # Finally, reset the stored thread IDs, in case it
                # changed because of fork().  Also change the main
                # thread to the current one (because there is not any
                # other left).
                tid = get_tid()
                gcdata.main_tid = tid
                gcdata.active_tid = tid

        self.thread_setup = thread_setup
        self.thread_run_ptr = getfn(thread_run, [], annmodel.s_None,
                                    inline=True, minimal_transform=False)
        self.thread_die_ptr = getfn(thread_die, [], annmodel.s_None,
                                    minimal_transform=False)
        # no thread_before_fork_ptr here
        self.thread_after_fork_ptr = getfn(thread_after_fork,
                                           [annmodel.SomeInteger(),
                                            SomeAddress()],
                                           annmodel.s_None,
                                           minimal_transform=False)

    def need_stacklet_support(self, gctransformer, getfn):
        shadow_stack_pool = self.shadow_stack_pool
        SHADOWSTACKREF = get_shadowstackref(self, gctransformer)

        def gc_shadowstackref_new():
            ssref = shadow_stack_pool.allocate(SHADOWSTACKREF)
            return lltype.cast_opaque_ptr(llmemory.GCREF, ssref)

        def gc_shadowstackref_context(gcref):
            ssref = lltype.cast_opaque_ptr(lltype.Ptr(SHADOWSTACKREF), gcref)
            return ssref.context

        def gc_save_current_state_away(gcref, ncontext):
            ssref = lltype.cast_opaque_ptr(lltype.Ptr(SHADOWSTACKREF), gcref)
            shadow_stack_pool.save_current_state_away(ssref, ncontext)

        def gc_forget_current_state():
            shadow_stack_pool.forget_current_state()

        def gc_restore_state_from(gcref):
            ssref = lltype.cast_opaque_ptr(lltype.Ptr(SHADOWSTACKREF), gcref)
            shadow_stack_pool.restore_state_from(ssref)

        def gc_start_fresh_new_state():
            shadow_stack_pool.start_fresh_new_state()

        s_gcref = SomePtr(llmemory.GCREF)
        s_addr = SomeAddress()
        self.gc_shadowstackref_new_ptr = getfn(gc_shadowstackref_new,
                                               [], s_gcref,
                                               minimal_transform=False)
        self.gc_shadowstackref_context_ptr = getfn(gc_shadowstackref_context,
                                                   [s_gcref], s_addr,
                                                   inline=True)
        self.gc_save_current_state_away_ptr = getfn(gc_save_current_state_away,
                                                    [s_gcref, s_addr],
                                                    annmodel.s_None,
                                                    inline=True)
        self.gc_forget_current_state_ptr = getfn(gc_forget_current_state,
                                                 [], annmodel.s_None,
                                                 inline=True)
        self.gc_restore_state_from_ptr = getfn(gc_restore_state_from,
                                               [s_gcref], annmodel.s_None,
                                               inline=True)
        self.gc_start_fresh_new_state_ptr = getfn(gc_start_fresh_new_state,
                                                  [], annmodel.s_None,
                                                  inline=True)

# ____________________________________________________________

class ShadowStackPool(object):
    """Manages a pool of shadowstacks.  The MAX most recently used
    shadowstacks are fully allocated and can be directly jumped into
    (called "full stacks" below).
    The rest are stored in a more virtual-memory-friendly way, i.e.
    with just the right amount malloced.  Before they can run, they
    must be copied into a full shadowstack.
    """
    _alloc_flavor_ = "raw"
    root_stack_depth = 163840

    MAX = 20

    def __init__(self, gcdata):
        self.unused_full_stack = llmemory.NULL
        self.gcdata = gcdata

    def initial_setup(self):
        self._prepare_unused_stack()
        self.start_fresh_new_state()

    def allocate(self, SHADOWSTACKREF):
        """Allocate an empty SHADOWSTACKREF object."""
        return lltype.malloc(SHADOWSTACKREF, zero=True)

    def save_current_state_away(self, shadowstackref, ncontext):
        """Save the current state away into 'shadowstackref'.
        This either works, or raise MemoryError and nothing is done.
        To do a switch, first call save_current_state_away() or
        forget_current_state(), and then call restore_state_from()
        or start_fresh_new_state().
        """
        fresh_free_fullstack = shadowstackref.prepare_free_slot()
        if self.unused_full_stack:
            if fresh_free_fullstack:
                llmemory.raw_free(fresh_free_fullstack)
        elif fresh_free_fullstack:
            self.unused_full_stack = fresh_free_fullstack
        else:
            self._prepare_unused_stack()
        #
        shadowstackref.base = self.gcdata.root_stack_base
        shadowstackref.top  = self.gcdata.root_stack_top
        shadowstackref.context = ncontext
        ll_assert(shadowstackref.base <= shadowstackref.top,
                  "save_current_state_away: broken shadowstack")
        shadowstackref.attach()
        #
        # cannot use llop.gc_writebarrier() here, because
        # we are in a minimally-transformed GC helper :-/
        gc = self.gcdata.gc
        if hasattr(gc.__class__, 'write_barrier'):
            shadowstackadr = llmemory.cast_ptr_to_adr(shadowstackref)
            gc.write_barrier(shadowstackadr)
        #
        self.gcdata.root_stack_top = llmemory.NULL  # to detect missing restore

    def forget_current_state(self):
        ll_assert(self.gcdata.root_stack_base == self.gcdata.root_stack_top,
                  "forget_current_state: shadowstack not empty!")
        if self.unused_full_stack:
            llmemory.raw_free(self.unused_full_stack)
        self.unused_full_stack = self.gcdata.root_stack_base
        self.gcdata.root_stack_top = llmemory.NULL  # to detect missing restore

    def restore_state_from(self, shadowstackref):
        ll_assert(bool(shadowstackref.base), "empty shadowstackref!")
        ll_assert(shadowstackref.base <= shadowstackref.top,
                  "restore_state_from: broken shadowstack")
        self.unused_full_stack = shadowstackref.rebuild(self.unused_full_stack)
        self.gcdata.root_stack_base = shadowstackref.base
        self.gcdata.root_stack_top  = shadowstackref.top
        self._cleanup(shadowstackref)

    def start_fresh_new_state(self):
        self.gcdata.root_stack_base = self.unused_full_stack
        self.gcdata.root_stack_top  = self.unused_full_stack
        self.unused_full_stack = llmemory.NULL

    def _cleanup(self, shadowstackref):
        shadowstackref.base = llmemory.NULL
        shadowstackref.top = llmemory.NULL
        shadowstackref.context = llmemory.NULL

    def _prepare_unused_stack(self):
        ll_assert(self.unused_full_stack == llmemory.NULL,
                  "already an unused_full_stack")
        root_stack_size = sizeofaddr * self.root_stack_depth
        self.unused_full_stack = llmemory.raw_malloc(root_stack_size)
        if self.unused_full_stack == llmemory.NULL:
            raise MemoryError


def get_shadowstackref(root_walker, gctransformer):
    if hasattr(gctransformer, '_SHADOWSTACKREF'):
        return gctransformer._SHADOWSTACKREF

    # Helpers to same virtual address space by limiting to MAX the
    # number of full shadow stacks.  If there are more, we compact
    # them into a separately-allocated zone of memory of just the right
    # size.  See the comments in the definition of fullstack_cache below.

    def ll_prepare_free_slot(_unused):
        """Free up a slot in the array of MAX entries, ready for storing
        a new shadowstackref.  Return the memory of the now-unused full
        shadowstack.
        """
        index = fullstack_cache[0]
        if index > 0:
            return llmemory.NULL     # there is already at least one free slot
        #
        # make a compact copy in one old entry and return the
        # original full-sized memory
        index = -index
        ll_assert(index > 0, "prepare_free_slot: cache[0] == 0")
        compacting = lltype.cast_int_to_ptr(SHADOWSTACKREFPTR,
                                            fullstack_cache[index])
        index += 1
        if index >= ShadowStackPool.MAX:
            index = 1
        fullstack_cache[0] = -index    # update to the next value in order
        #
        compacting.detach()
        original = compacting.base
        size = compacting.top - original
        new = llmemory.raw_malloc(size)
        if new == llmemory.NULL:
            return llmemory.NULL
        llmemory.raw_memcopy(original, new, size)
        compacting.base = new
        compacting.top = new + size
        return original

    def ll_attach(shadowstackref):
        """After prepare_free_slot(), store a shadowstackref in that slot."""
        index = fullstack_cache[0]
        ll_assert(index > 0, "fullstack attach: no free slot")
        fullstack_cache[0] = fullstack_cache[index]
        fullstack_cache[index] = lltype.cast_ptr_to_int(shadowstackref)
        ll_assert(shadowstackref.fsindex == 0, "fullstack attach: already one?")
        shadowstackref.fsindex = index    # > 0

    def ll_detach(shadowstackref):
        """Detach a shadowstackref from the array of MAX entries."""
        index = shadowstackref.fsindex
        ll_assert(index > 0, "detach: unattached shadowstackref")
        ll_assert(fullstack_cache[index] ==
                  lltype.cast_ptr_to_int(shadowstackref),
                  "detach: bad fullstack_cache")
        shadowstackref.fsindex = 0
        fullstack_cache[index] = fullstack_cache[0]
        fullstack_cache[0] = index

    def ll_rebuild(shadowstackref, fullstack_base):
        if shadowstackref.fsindex > 0:
            shadowstackref.detach()
            return fullstack_base
        else:
            # make an expanded copy of the compact shadowstack stored in
            # 'shadowstackref' and free that
            compact = shadowstackref.base
            size = shadowstackref.top - compact
            shadowstackref.base = fullstack_base
            shadowstackref.top = fullstack_base + size
            llmemory.raw_memcopy(compact, fullstack_base, size)
            llmemory.raw_free(compact)
            return llmemory.NULL

    SHADOWSTACKREFPTR = lltype.Ptr(lltype.GcForwardReference())
    SHADOWSTACKREF = lltype.GcStruct('ShadowStackRef',
        ('base', llmemory.Address),
        ('top', llmemory.Address),
        ('context', llmemory.Address),
        ('fsindex', lltype.Signed),
        rtti=True,
        adtmeths={'prepare_free_slot': ll_prepare_free_slot,
                  'attach': ll_attach,
                  'detach': ll_detach,
                  'rebuild': ll_rebuild})
    SHADOWSTACKREFPTR.TO.become(SHADOWSTACKREF)

    # Items 1..MAX-1 of the following array can be SHADOWSTACKREF
    # addresses cast to integer.  Or, they are small numbers and they
    # make up a free list, rooted in item 0, which goes on until
    # terminated with a negative item.  This negative item gives (the
    # opposite of) the index of the entry we try to remove next.
    # Initially all items are in this free list and the end is '-1'.
    fullstack_cache = lltype.malloc(lltype.Array(lltype.Signed),
                                    ShadowStackPool.MAX,
                                    flavor='raw', immortal=True)
    for i in range(len(fullstack_cache) - 1):
        fullstack_cache[i] = i + 1
    fullstack_cache[len(fullstack_cache) - 1] = -1

    def customtrace(gc, obj, callback, arg):
        obj = llmemory.cast_adr_to_ptr(obj, SHADOWSTACKREFPTR)
        index = obj.fsindex
        if index > 0:
            # Haaaaaaack: fullstack_cache[] is just an integer, so it
            # doesn't follow the SHADOWSTACKREF when it moves.  But we
            # know this customtrace() will be called just after the
            # move.  So we fix the fullstack_cache[] now... :-/
            fullstack_cache[index] = lltype.cast_ptr_to_int(obj)
        addr = obj.top
        start = obj.base
        while addr != start:
            addr -= sizeofaddr
            gc._trace_callback(callback, arg, addr)

    gc = gctransformer.gcdata.gc
    assert not hasattr(gc, 'custom_trace_dispatcher')
    # ^^^ create_custom_trace_funcs() must not run before this
    gctransformer.translator.rtyper.custom_trace_funcs.append(
        (SHADOWSTACKREF, customtrace))

    def shadowstack_destructor(shadowstackref):
        if root_walker.stacklet_support:
            from rpython.rlib import _rffi_stacklet as _c
            h = shadowstackref.context
            h = llmemory.cast_adr_to_ptr(h, _c.handle)
            shadowstackref.context = llmemory.NULL
        #
        if shadowstackref.fsindex > 0:
            shadowstackref.detach()
        base = shadowstackref.base
        shadowstackref.base    = llmemory.NULL
        shadowstackref.top     = llmemory.NULL
        llmemory.raw_free(base)
        #
        if root_walker.stacklet_support:
            if h:
                _c.destroy(h)

    destrptr = gctransformer.annotate_helper(shadowstack_destructor,
                                             [SHADOWSTACKREFPTR], lltype.Void)

    lltype.attachRuntimeTypeInfo(SHADOWSTACKREF, destrptr=destrptr)

    gctransformer._SHADOWSTACKREF = SHADOWSTACKREF
    return SHADOWSTACKREF
