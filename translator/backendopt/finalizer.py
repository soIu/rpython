
from rpython.translator.backendopt import graphanalyze
from rpython.rtyper.lltypesystem import lltype

class FinalizerError(Exception):
    """ __del__ marked as lightweight finalizer, but the analyzer did
    not agree
    """

class FinalizerAnalyzer(graphanalyze.BoolGraphAnalyzer):
    """ Analyzer that determines whether a finalizer is lightweight enough
    so it can be called without all the complicated logic in the garbage
    collector. The set of operations here is restrictive for a good reason
    - it's better to be safe. Specifically disallowed operations:

    * anything that escapes self
    * anything that can allocate
    """
    ok_operations = ['ptr_nonzero', 'ptr_eq', 'ptr_ne', 'free', 'same_as',
                     'direct_ptradd', 'force_cast', 'track_alloc_stop',
                     'raw_free']

    def analyze_light_finalizer(self, graph):
        result = self.analyze_direct_call(graph)
        if (result is self.top_result() and
            getattr(graph.func, '_must_be_light_finalizer_', False)):
            raise FinalizerError(FinalizerError.__doc__, graph)
        return result

    def analyze_simple_operation(self, op, graphinfo):
        if op.opname in self.ok_operations:
            return self.bottom_result()
        if (op.opname.startswith('int_') or op.opname.startswith('float_')
            or op.opname.startswith('uint_') or op.opname.startswith('cast_')):
            return self.bottom_result()
        if op.opname == 'setfield' or op.opname == 'bare_setfield':
            TP = op.args[2].concretetype
            if not isinstance(TP, lltype.Ptr) or TP.TO._gckind == 'raw':
                # primitive type
                return self.bottom_result()
        if op.opname == 'getfield':
            TP = op.result.concretetype
            if not isinstance(TP, lltype.Ptr) or TP.TO._gckind == 'raw':
                # primitive type
                return self.bottom_result()
        return self.top_result()
