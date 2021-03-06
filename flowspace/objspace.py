"""Implements the main interface for flow graph creation: build_flow().
"""

from inspect import CO_NEWLOCALS, isgeneratorfunction

from rpython.flowspace.model import checkgraph
from rpython.flowspace.bytecode import HostCode
from rpython.flowspace.flowcontext import (FlowContext, fixeggblocks)
from rpython.flowspace.generator import (tweak_generator_graph,
        make_generator_entry_graph)
from rpython.flowspace.pygraph import PyGraph


def _assert_rpythonic(func):
    """Raise ValueError if ``func`` is obviously not RPython"""
    if func.func_doc and func.func_doc.lstrip().startswith('NOT_RPYTHON'):
        raise ValueError("%r is tagged as NOT_RPYTHON" % (func,))
    if func.func_code.co_cellvars:
        raise ValueError(
"""RPython functions cannot create closures
Possible casues:
    Function is inner function
    Function uses generator expressions
    Lambda expressions
in %r""" % (func,))
    if not (func.func_code.co_flags & CO_NEWLOCALS):
        raise ValueError("The code object for a RPython function should have "
                         "the flag CO_NEWLOCALS set.")


def build_flow(func):
    """
    Create the flow graph (in SSA form) for the function.
    """
    _assert_rpythonic(func)
    if (isgeneratorfunction(func) and
            not hasattr(func, '_generator_next_method_of_')):
        return make_generator_entry_graph(func)
    code = HostCode._from_code(func.func_code)
    graph = PyGraph(func, code)
    ctx = FlowContext(graph, code)
    ctx.build_flow()
    fixeggblocks(graph)
    if code.is_generator:
        tweak_generator_graph(graph)
    return graph
