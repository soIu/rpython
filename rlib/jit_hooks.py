from rpython.annotator import model as annmodel
from rpython.rtyper.llannotation import SomePtr, lltype_to_annotation
from rpython.rlib.objectmodel import specialize
from rpython.rtyper.annlowlevel import (cast_instance_to_base_ptr,
    cast_base_ptr_to_instance, llstr)
from rpython.rtyper.extregistry import ExtRegistryEntry
from rpython.rtyper.lltypesystem import llmemory, lltype
from rpython.rtyper import rclass


def register_helper(s_result):
    def wrapper(helper):
        class Entry(ExtRegistryEntry):
            _about_ = helper

            def compute_result_annotation(self, *args):
                if (isinstance(s_result, annmodel.SomeObject) or
                    s_result is None):
                    return s_result
                return lltype_to_annotation(s_result)

            def specialize_call(self, hop):
                from rpython.rtyper.lltypesystem import lltype

                c_func = hop.inputconst(lltype.Void, helper)
                c_name = hop.inputconst(lltype.Void, 'access_helper')
                args_v = [hop.inputarg(arg, arg=i)
                          for i, arg in enumerate(hop.args_r)]
                hop.exception_cannot_occur()
                return hop.genop('jit_marker', [c_name, c_func] + args_v,
                                 resulttype=hop.r_result)
        return helper
    return wrapper

def _cast_to_box(llref):
    from rpython.jit.metainterp.history import AbstractValue

    ptr = lltype.cast_opaque_ptr(rclass.OBJECTPTR, llref)
    return cast_base_ptr_to_instance(AbstractValue, ptr)

def _cast_to_resop(llref):
    from rpython.jit.metainterp.resoperation import AbstractResOp

    ptr = lltype.cast_opaque_ptr(rclass.OBJECTPTR, llref)
    return cast_base_ptr_to_instance(AbstractResOp, ptr)

@specialize.argtype(0)
def _cast_to_gcref(obj):
    return lltype.cast_opaque_ptr(llmemory.GCREF,
                                  cast_instance_to_base_ptr(obj))

def emptyval():
    return lltype.nullptr(llmemory.GCREF.TO)

@register_helper(SomePtr(llmemory.GCREF))
def resop_new(no, llargs, llres):
    from rpython.jit.metainterp.history import ResOperation

    args = [_cast_to_box(llargs[i]) for i in range(len(llargs))]
    if llres:
        res = _cast_to_box(llres)
    else:
        res = None
    return _cast_to_gcref(ResOperation(no, args, res))

@register_helper(SomePtr(llmemory.GCREF))
def boxint_new(no):
    from rpython.jit.metainterp.history import BoxInt
    return _cast_to_gcref(BoxInt(no))

@register_helper(annmodel.SomeInteger())
def resop_getopnum(llop):
    return _cast_to_resop(llop).getopnum()

@register_helper(annmodel.SomeString(can_be_None=True))
def resop_getopname(llop):
    return llstr(_cast_to_resop(llop).getopname())

@register_helper(SomePtr(llmemory.GCREF))
def resop_getarg(llop, no):
    return _cast_to_gcref(_cast_to_resop(llop).getarg(no))

@register_helper(annmodel.s_None)
def resop_setarg(llop, no, llbox):
    _cast_to_resop(llop).setarg(no, _cast_to_box(llbox))

@register_helper(SomePtr(llmemory.GCREF))
def resop_getresult(llop):
    return _cast_to_gcref(_cast_to_resop(llop).result)

@register_helper(annmodel.s_None)
def resop_setresult(llop, llbox):
    _cast_to_resop(llop).result = _cast_to_box(llbox)

@register_helper(annmodel.SomeInteger())
def box_getint(llbox):
    return _cast_to_box(llbox).getint()

@register_helper(SomePtr(llmemory.GCREF))
def box_clone(llbox):
    return _cast_to_gcref(_cast_to_box(llbox).clonebox())

@register_helper(SomePtr(llmemory.GCREF))
def box_constbox(llbox):
    return _cast_to_gcref(_cast_to_box(llbox).constbox())

@register_helper(SomePtr(llmemory.GCREF))
def box_nonconstbox(llbox):
    return _cast_to_gcref(_cast_to_box(llbox).nonconstbox())

@register_helper(annmodel.SomeBool())
def box_isconst(llbox):
    from rpython.jit.metainterp.history import Const
    return isinstance(_cast_to_box(llbox), Const)

@register_helper(annmodel.SomeBool())
def box_isint(llbox):
    from rpython.jit.metainterp.history import INT
    return _cast_to_box(llbox).type == INT

# ------------------------- stats interface ---------------------------

@register_helper(annmodel.SomeBool())
def stats_set_debug(warmrunnerdesc, flag):
    return warmrunnerdesc.metainterp_sd.cpu.set_debug(flag)

@register_helper(annmodel.SomeInteger())
def stats_get_counter_value(warmrunnerdesc, no):
    return warmrunnerdesc.metainterp_sd.profiler.get_counter(no)

@register_helper(annmodel.SomeFloat())
def stats_get_times_value(warmrunnerdesc, no):
    return warmrunnerdesc.metainterp_sd.profiler.get_times(no)

LOOP_RUN_CONTAINER = lltype.GcArray(lltype.Struct('elem',
                                                  ('type', lltype.Char),
                                                  ('number', lltype.Signed),
                                                  ('counter', lltype.Signed)))

@register_helper(lltype.Ptr(LOOP_RUN_CONTAINER))
def stats_get_loop_run_times(warmrunnerdesc):
    return warmrunnerdesc.metainterp_sd.cpu.get_all_loop_runs()
