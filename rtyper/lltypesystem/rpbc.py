import types

from rpython.annotator import description
from rpython.rlib.debug import ll_assert
from rpython.rlib.unroll import unrolling_iterable
from rpython.rtyper import callparse
from rpython.rtyper.lltypesystem import llmemory
from rpython.rtyper.lltypesystem.lltype import (typeOf, Void, ForwardReference,
    Struct, Bool, Char, Ptr, malloc, nullptr, Array, Signed)
from rpython.rtyper.rmodel import Repr, inputconst
from rpython.rtyper.rpbc import (
    AbstractMultipleFrozenPBCRepr,
    AbstractFunctionsPBCRepr, AbstractMultipleUnrelatedFrozenPBCRepr,
    SingleFrozenPBCRepr, get_concrete_calltable)
from rpython.rtyper.typesystem import getfunctionptr
from rpython.tool.pairtype import pairtype


class MultipleFrozenPBCRepr(AbstractMultipleFrozenPBCRepr):
    """Representation selected for multiple non-callable pre-built constants."""
    def __init__(self, rtyper, access_set):
        self.rtyper = rtyper
        self.access_set = access_set
        self.pbc_type = ForwardReference()
        self.lowleveltype = Ptr(self.pbc_type)
        self.pbc_cache = {}

    def _setup_repr(self):
        llfields = self._setup_repr_fields()
        kwds = {'hints': {'immutable': True}}
        self.pbc_type.become(Struct('pbc', *llfields, **kwds))

    def create_instance(self):
        return malloc(self.pbc_type, immortal=True)

    def null_instance(self):
        return nullptr(self.pbc_type)

    def getfield(self, vpbc, attr, llops):
        mangled_name, r_value = self.fieldmap[attr]
        cmangledname = inputconst(Void, mangled_name)
        return llops.genop('getfield', [vpbc, cmangledname],
                           resulttype=r_value)


class MultipleUnrelatedFrozenPBCRepr(AbstractMultipleUnrelatedFrozenPBCRepr):
    """Representation selected for multiple non-callable pre-built constants
    with no common access set."""

    lowleveltype = llmemory.Address
    EMPTY = Struct('pbc', hints={'immutable': True})

    def convert_pbc(self, pbcptr):
        return llmemory.fakeaddress(pbcptr)

    def create_instance(self):
        return malloc(self.EMPTY, immortal=True)

    def null_instance(self):
        return llmemory.Address._defl()


class __extend__(pairtype(MultipleUnrelatedFrozenPBCRepr,
                          MultipleUnrelatedFrozenPBCRepr),
                 pairtype(MultipleUnrelatedFrozenPBCRepr,
                          SingleFrozenPBCRepr),
                 pairtype(SingleFrozenPBCRepr,
                          MultipleUnrelatedFrozenPBCRepr)):
    def rtype_is_((robj1, robj2), hop):
        if isinstance(robj1, MultipleUnrelatedFrozenPBCRepr):
            r = robj1
        else:
            r = robj2
        vlist = hop.inputargs(r, r)
        return hop.genop('adr_eq', vlist, resulttype=Bool)


class __extend__(pairtype(MultipleFrozenPBCRepr,
                          MultipleUnrelatedFrozenPBCRepr)):
    def convert_from_to((robj1, robj2), v, llops):
        return llops.genop('cast_ptr_to_adr', [v], resulttype=llmemory.Address)


# ____________________________________________________________

class FunctionsPBCRepr(AbstractFunctionsPBCRepr):
    """Representation selected for a PBC of function(s)."""

    def setup_specfunc(self):
        fields = []
        for row in self.uniquerows:
            fields.append((row.attrname, row.fntype))
        kwds = {'hints': {'immutable': True}}
        return Ptr(Struct('specfunc', *fields, **kwds))

    def create_specfunc(self):
        return malloc(self.lowleveltype.TO, immortal=True)

    def get_specfunc_row(self, llop, v, c_rowname, resulttype):
        return llop.genop('getfield', [v, c_rowname], resulttype=resulttype)


class SmallFunctionSetPBCRepr(Repr):
    def __init__(self, rtyper, s_pbc):
        self.rtyper = rtyper
        self.s_pbc = s_pbc
        self.callfamily = s_pbc.any_description().getcallfamily()
        concretetable, uniquerows = get_concrete_calltable(self.rtyper,
                                                           self.callfamily)
        assert len(uniquerows) == 1
        self.lowleveltype = Char
        self.pointer_repr = FunctionsPBCRepr(rtyper, s_pbc)
        self._conversion_tables = {}
        self._compression_function = None
        self._dispatch_cache = {}

    def _setup_repr(self):
        if self.s_pbc.subset_of:
            assert self.s_pbc.can_be_None == self.s_pbc.subset_of.can_be_None
            r = self.rtyper.getrepr(self.s_pbc.subset_of)
            if r is not self:
                r.setup()
                self.descriptions = r.descriptions
                self.c_pointer_table = r.c_pointer_table
                return
        self.descriptions = list(self.s_pbc.descriptions)
        if self.s_pbc.can_be_None:
            self.descriptions.insert(0, None)
        POINTER_TABLE = Array(self.pointer_repr.lowleveltype,
                              hints={'nolength': True})
        pointer_table = malloc(POINTER_TABLE, len(self.descriptions),
                               immortal=True)
        for i, desc in enumerate(self.descriptions):
            if desc is not None:
                pointer_table[i] = self.pointer_repr.convert_desc(desc)
            else:
                pointer_table[i] = self.pointer_repr.convert_const(None)
        self.c_pointer_table = inputconst(Ptr(POINTER_TABLE), pointer_table)

    def get_s_callable(self):
        return self.s_pbc

    def get_r_implfunc(self):
        return self, 0

    def get_s_signatures(self, shape):
        funcdesc = self.s_pbc.any_description()
        return funcdesc.get_s_signatures(shape)

    def convert_desc(self, funcdesc):
        return chr(self.descriptions.index(funcdesc))

    def convert_const(self, value):
        if isinstance(value, types.MethodType) and value.im_self is None:
            value = value.im_func   # unbound method -> bare function
        if value is None:
            return chr(0)
        funcdesc = self.rtyper.annotator.bookkeeper.getdesc(value)
        return self.convert_desc(funcdesc)

    def rtype_simple_call(self, hop):
        return self.call(hop)

    def rtype_call_args(self, hop):
        return self.call(hop)

    def dispatcher(self, shape, index, argtypes, resulttype):
        key = shape, index, tuple(argtypes), resulttype
        if key in self._dispatch_cache:
            return self._dispatch_cache[key]
        from rpython.translator.unsimplify import varoftype
        from rpython.flowspace.model import FunctionGraph, Link, Block, SpaceOperation
        inputargs = [varoftype(t) for t in [Char] + argtypes]
        startblock = Block(inputargs)
        startblock.exitswitch = inputargs[0]
        graph = FunctionGraph("dispatcher", startblock, varoftype(resulttype))
        row_of_graphs = self.callfamily.calltables[shape][index]
        links = []
        descs = list(self.s_pbc.descriptions)
        if self.s_pbc.can_be_None:
            descs.insert(0, None)
        for desc in descs:
            if desc is None:
                continue
            args_v = [varoftype(t) for t in argtypes]
            b = Block(args_v)
            llfn = self.rtyper.getcallable(row_of_graphs[desc])
            v_fn = inputconst(typeOf(llfn), llfn)
            v_result = varoftype(resulttype)
            b.operations.append(
                SpaceOperation("direct_call", [v_fn] + args_v, v_result))
            b.closeblock(Link([v_result], graph.returnblock))
            i = self.descriptions.index(desc)
            links.append(Link(inputargs[1:], b, chr(i)))
            links[-1].llexitcase = chr(i)
        startblock.closeblock(*links)
        self.rtyper.annotator.translator.graphs.append(graph)
        ll_ret = getfunctionptr(graph)
        #FTYPE = FuncType
        c_ret = self._dispatch_cache[key] = inputconst(typeOf(ll_ret), ll_ret)
        return c_ret

    def call(self, hop):
        bk = self.rtyper.annotator.bookkeeper
        args = hop.spaceop.build_args(hop.args_s[1:])
        s_pbc = hop.args_s[0]   # possibly more precise than self.s_pbc
        descs = list(s_pbc.descriptions)
        vfcs = description.FunctionDesc.variant_for_call_site
        shape, index = vfcs(bk, self.callfamily, descs, args, hop.spaceop)
        row_of_graphs = self.callfamily.calltables[shape][index]
        anygraph = row_of_graphs.itervalues().next()  # pick any witness
        vlist = [hop.inputarg(self, arg=0)]
        vlist += callparse.callparse(self.rtyper, anygraph, hop)
        rresult = callparse.getrresult(self.rtyper, anygraph)
        hop.exception_is_here()
        v_dispatcher = self.dispatcher(shape, index, [v.concretetype for v in vlist[1:]], rresult.lowleveltype)
        v_result = hop.genop('direct_call', [v_dispatcher] + vlist,
                             resulttype=rresult)
        return hop.llops.convertvar(v_result, rresult, hop.r_result)

    def rtype_bool(self, hop):
        if not self.s_pbc.can_be_None:
            return inputconst(Bool, True)
        else:
            v1, = hop.inputargs(self)
            return hop.genop('char_ne', [v1, inputconst(Char, '\000')],
                         resulttype=Bool)


class __extend__(pairtype(SmallFunctionSetPBCRepr, FunctionsPBCRepr)):
    def convert_from_to((r_set, r_ptr), v, llops):
        if r_ptr.lowleveltype is Void:
            return inputconst(Void, None)
        else:
            assert v.concretetype is Char
            v_int = llops.genop('cast_char_to_int', [v],
                                resulttype=Signed)
            return llops.genop('getarrayitem', [r_set.c_pointer_table, v_int],
                               resulttype=r_ptr.lowleveltype)


def compression_function(r_set):
    if r_set._compression_function is None:
        table = []
        for i, p in enumerate(r_set.c_pointer_table.value):
            table.append((chr(i), p))
        last_c, last_p = table[-1]
        unroll_table = unrolling_iterable(table[:-1])

        def ll_compress(fnptr):
            for c, p in unroll_table:
                if fnptr == p:
                    return c
            else:
                ll_assert(fnptr == last_p, "unexpected function pointer")
                return last_c
        r_set._compression_function = ll_compress
    return r_set._compression_function


class __extend__(pairtype(FunctionsPBCRepr, SmallFunctionSetPBCRepr)):
    def convert_from_to((r_ptr, r_set), v, llops):
        if r_ptr.lowleveltype is Void:
            desc, = r_ptr.s_pbc.descriptions
            return inputconst(Char, r_set.convert_desc(desc))
        else:
            ll_compress = compression_function(r_set)
            return llops.gendirectcall(ll_compress, v)


def conversion_table(r_from, r_to):
    if r_to in r_from._conversion_tables:
        return r_from._conversion_tables[r_to]
    else:
        t = malloc(Array(Char, hints={'nolength': True}),
                   len(r_from.descriptions), immortal=True)
        l = []
        for i, d in enumerate(r_from.descriptions):
            if d in r_to.descriptions:
                j = r_to.descriptions.index(d)
                l.append(j)
                t[i] = chr(j)
            else:
                l.append(None)
        if l == range(len(r_from.descriptions)):
            r = None
        else:
            r = inputconst(Ptr(Array(Char, hints={'nolength': True})), t)
        r_from._conversion_tables[r_to] = r
        return r


class __extend__(pairtype(SmallFunctionSetPBCRepr, SmallFunctionSetPBCRepr)):
    def convert_from_to((r_from, r_to), v, llops):
        c_table = conversion_table(r_from, r_to)
        if c_table:
            assert v.concretetype is Char
            v_int = llops.genop('cast_char_to_int', [v],
                                resulttype=Signed)
            return llops.genop('getarrayitem', [c_table, v_int],
                               resulttype=Char)
        else:
            return v
