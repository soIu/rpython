from __future__ import absolute_import

import types

from rpython.tool.ansi_print import ansi_log
from rpython.tool.pairtype import pair
from rpython.tool.error import (format_blocked_annotation_error,
                             gather_error, source_lines)
from rpython.flowspace.model import (
    Variable, Constant, FunctionGraph, checkgraph)
from rpython.translator import simplify, transform
from rpython.annotator import model as annmodel, signature
from rpython.annotator.bookkeeper import Bookkeeper
from rpython.rtyper.normalizecalls import perform_normalizations

import py
log = py.log.Producer("annrpython")
py.log.setconsumer("annrpython", ansi_log)


class RPythonAnnotator(object):
    """Block annotator for RPython.
    See description in doc/translation.txt."""

    def __init__(self, translator=None, policy=None, bookkeeper=None):
        import rpython.rtyper.extfuncregistry # has side effects

        if translator is None:
            # interface for tests
            from rpython.translator.translator import TranslationContext
            translator = TranslationContext()
            translator.annotator = self
        self.translator = translator
        self.pendingblocks = {}  # map {block: graph-containing-it}
        self.annotated = {}      # set of blocks already seen
        self.added_blocks = None # see processblock() below
        self.links_followed = {} # set of links that have ever been followed
        self.notify = {}        # {block: {positions-to-reflow-from-when-done}}
        self.fixed_graphs = {}  # set of graphs not to annotate again
        self.blocked_blocks = {} # set of {blocked_block: (graph, index)}
        # --- the following information is recorded for debugging ---
        self.blocked_graphs = {} # set of graphs that have blocked blocks
        # --- end of debugging information ---
        self.frozen = False
        if policy is None:
            from rpython.annotator.policy import AnnotatorPolicy
            self.policy = AnnotatorPolicy()
        else:
            self.policy = policy
        if bookkeeper is None:
            bookkeeper = Bookkeeper(self)
        self.bookkeeper = bookkeeper

    def __getstate__(self):
        attrs = """translator pendingblocks annotated links_followed
        notify bookkeeper frozen policy added_blocks""".split()
        ret = self.__dict__.copy()
        for key, value in ret.items():
            if key not in attrs:
                assert type(value) is dict, (
                    "%r is not dict. please update %s.__getstate__" %
                    (key, self.__class__.__name__))
                ret[key] = {}
        return ret

    #___ convenience high-level interface __________________

    def build_types(self, function, input_arg_types, complete_now=True,
                    main_entry_point=False):
        """Recursively build annotations about the specific entry point."""
        assert isinstance(function, types.FunctionType), "fix that!"

        from rpython.annotator.policy import AnnotatorPolicy
        policy = AnnotatorPolicy()
        # make input arguments and set their type
        args_s = [self.typeannotation(t) for t in input_arg_types]

        # XXX hack
        annmodel.TLS.check_str_without_nul = (
            self.translator.config.translation.check_str_without_nul)

        flowgraph, inputcells = self.get_call_parameters(function, args_s, policy)
        if not isinstance(flowgraph, FunctionGraph):
            assert isinstance(flowgraph, annmodel.SomeObject)
            return flowgraph

        if main_entry_point:
            self.translator.entry_point_graph = flowgraph
        return self.build_graph_types(flowgraph, inputcells, complete_now=complete_now)

    def get_call_parameters(self, function, args_s, policy):
        desc = self.bookkeeper.getdesc(function)
        prevpolicy = self.policy
        self.policy = policy
        self.bookkeeper.enter(None)
        try:
            return desc.get_call_parameters(args_s)
        finally:
            self.bookkeeper.leave()
            self.policy = prevpolicy

    def annotate_helper(self, function, args_s, policy=None):
        if policy is None:
            from rpython.annotator.policy import AnnotatorPolicy
            policy = AnnotatorPolicy()
            # XXX hack
            annmodel.TLS.check_str_without_nul = (
                self.translator.config.translation.check_str_without_nul)
        graph, inputcells = self.get_call_parameters(function, args_s, policy)
        self.build_graph_types(graph, inputcells, complete_now=False)
        self.complete_helpers(policy)
        return graph

    def complete_helpers(self, policy):
        saved = self.policy, self.added_blocks
        self.policy = policy
        try:
            self.added_blocks = {}
            self.complete()
            # invoke annotation simplifications for the new blocks
            self.simplify(block_subset=self.added_blocks)
        finally:
            self.policy, self.added_blocks = saved

    def build_graph_types(self, flowgraph, inputcells, complete_now=True):
        checkgraph(flowgraph)

        nbarg = len(flowgraph.getargs())
        assert len(inputcells) == nbarg # wrong number of args

        # register the entry point
        self.addpendinggraph(flowgraph, inputcells)
        # recursively proceed until no more pending block is left
        if complete_now:
            self.complete()
        return self.annotation(flowgraph.getreturnvar())

    def gettype(self, variable):
        """Return the known type of a control flow graph variable,
        defaulting to 'object'."""
        if isinstance(variable, Constant):
            return type(variable.value)
        elif isinstance(variable, Variable):
            s_variable = variable.annotation
            if s_variable:
                return s_variable.knowntype
            else:
                return object
        else:
            raise TypeError("Variable or Constant instance expected, "
                              "got %r" % (variable,))

    def getuserclassdefinitions(self):
        """Return a list of ClassDefs."""
        return self.bookkeeper.classdefs

    #___ medium-level interface ____________________________

    def addpendinggraph(self, flowgraph, inputcells):
        self.addpendingblock(flowgraph, flowgraph.startblock, inputcells)

    def addpendingblock(self, graph, block, cells):
        """Register an entry point into block with the given input cells."""
        if graph in self.fixed_graphs:
            # special case for annotating/rtyping in several phases: calling
            # a graph that has already been rtyped.  Safety-check the new
            # annotations that are passed in, and don't annotate the old
            # graph -- it's already low-level operations!
            for a, s_newarg in zip(block.inputargs, cells):
                s_oldarg = self.binding(a)
                assert annmodel.unionof(s_oldarg, s_newarg) == s_oldarg
        else:
            assert not self.frozen
            if block not in self.annotated:
                self.bindinputargs(graph, block, cells)
            else:
                self.mergeinputargs(graph, block, cells)
            if not self.annotated[block]:
                self.pendingblocks[block] = graph

    def complete_pending_blocks(self):
        while self.pendingblocks:
            block, graph = self.pendingblocks.popitem()
            self.processblock(graph, block)

    def complete(self):
        """Process pending blocks until none is left."""
        while True:
            self.complete_pending_blocks()
            self.policy.no_more_blocks_to_annotate(self)
            if not self.pendingblocks:
                break   # finished
        # make sure that the return variables of all graphs is annotated
        if self.added_blocks is not None:
            newgraphs = [self.annotated[block] for block in self.added_blocks]
            newgraphs = dict.fromkeys(newgraphs)
            got_blocked_blocks = False in newgraphs
        else:
            newgraphs = self.translator.graphs  #all of them
            got_blocked_blocks = False in self.annotated.values()
        if got_blocked_blocks:
            for graph in self.blocked_graphs.values():
                self.blocked_graphs[graph] = True

            blocked_blocks = [block for block, done in self.annotated.items()
                                    if done is False]
            assert len(blocked_blocks) == len(self.blocked_blocks)

            text = format_blocked_annotation_error(self, self.blocked_blocks)
            #raise SystemExit()
            raise annmodel.AnnotatorError(text)
        for graph in newgraphs:
            v = graph.getreturnvar()
            if v.annotation is None:
                self.setbinding(v, annmodel.s_ImpossibleValue)
        # policy-dependent computation
        self.bookkeeper.compute_at_fixpoint()

    def validate(self):
        """Check that the annotation results are valid"""
        self.bookkeeper.check_no_flags_on_instances()

    def annotation(self, arg):
        "Gives the SomeValue corresponding to the given Variable or Constant."
        if isinstance(arg, Variable):
            return arg.annotation
        elif isinstance(arg, Constant):
            return self.bookkeeper.immutablevalue(arg.value)
        else:
            raise TypeError('Variable or Constant expected, got %r' % (arg,))

    def binding(self, arg):
        "Gives the SomeValue corresponding to the given Variable or Constant."
        s_arg = self.annotation(arg)
        if s_arg is None:
            raise KeyError
        return s_arg

    def typeannotation(self, t):
        return signature.annotation(t, self.bookkeeper)

    def setbinding(self, arg, s_value):
        s_old = arg.annotation
        if s_old is not None:
            assert s_value.contains(s_old)
        arg.annotation = s_value

    def warning(self, msg, pos=None):
        if pos is None:
            try:
                pos = self.bookkeeper.position_key
            except AttributeError:
                pos = '?'
        if pos != '?':
            pos = self.whereami(pos)

        log.WARNING("%s/ %s" % (pos, msg))


    #___ interface for annotator.bookkeeper _______

    def recursivecall(self, graph, whence, inputcells):
        if isinstance(whence, tuple):
            parent_graph, parent_block, parent_index = whence
            tag = parent_block, parent_index
            self.translator.update_call_graph(parent_graph, graph, tag)
        # self.notify[graph.returnblock] is a dictionary of call
        # points to this func which triggers a reflow whenever the
        # return block of this graph has been analysed.
        callpositions = self.notify.setdefault(graph.returnblock, {})
        if whence is not None:
            if callable(whence):
                def callback():
                    whence(self, graph)
            else:
                callback = whence
            callpositions[callback] = True

        # generalize the function's input arguments
        self.addpendingblock(graph, graph.startblock, inputcells)

        # get the (current) return value
        v = graph.getreturnvar()
        try:
            return self.binding(v)
        except KeyError:
            # the function didn't reach any return statement so far.
            # (some functions actually never do, they always raise exceptions)
            return annmodel.s_ImpossibleValue

    def reflowfromposition(self, position_key):
        graph, block, index = position_key
        self.reflowpendingblock(graph, block)


    #___ simplification (should be moved elsewhere?) _______

    def simplify(self, block_subset=None, extra_passes=None):
        # Generic simplifications
        transform.transform_graph(self, block_subset=block_subset,
                                  extra_passes=extra_passes)
        if block_subset is None:
            graphs = self.translator.graphs
        else:
            graphs = {}
            for block in block_subset:
                graph = self.annotated.get(block)
                if graph:
                    graphs[graph] = True
        for graph in graphs:
            simplify.eliminate_empty_blocks(graph)
        if block_subset is None:
            perform_normalizations(self)


    #___ flowing annotations in blocks _____________________

    def processblock(self, graph, block):
        # Important: this is not called recursively.
        # self.flowin() can only issue calls to self.addpendingblock().
        # The analysis of a block can be in three states:
        #  * block not in self.annotated:
        #      never seen the block.
        #  * self.annotated[block] == False:
        #      the input variables of the block have bindings but we
        #      still have to consider all the operations in the block.
        #  * self.annotated[block] == graph-containing-block:
        #      analysis done (at least until we find we must generalize the
        #      input variables).

        #print '* processblock', block, cells
        self.annotated[block] = graph
        if block in self.blocked_blocks:
            del self.blocked_blocks[block]
        try:
            self.flowin(graph, block)
        except BlockedInference, e:
            self.annotated[block] = False   # failed, hopefully temporarily
            self.blocked_blocks[block] = (graph, e.opindex)
        except Exception, e:
            # hack for debug tools only
            if not hasattr(e, '__annotator_block'):
                setattr(e, '__annotator_block', block)
            raise

        # The dict 'added_blocks' is used by rpython.annlowlevel to
        # detect which are the new blocks that annotating an additional
        # small helper creates.
        if self.added_blocks is not None:
            self.added_blocks[block] = True

    def reflowpendingblock(self, graph, block):
        assert not self.frozen
        assert graph not in self.fixed_graphs
        self.pendingblocks[block] = graph
        assert block in self.annotated
        self.annotated[block] = False  # must re-flow
        self.blocked_blocks[block] = (graph, None)

    def bindinputargs(self, graph, block, inputcells):
        # Create the initial bindings for the input args of a block.
        assert len(block.inputargs) == len(inputcells)
        for a, cell in zip(block.inputargs, inputcells):
            self.setbinding(a, cell)
        self.annotated[block] = False  # must flowin.
        self.blocked_blocks[block] = (graph, None)

    def mergeinputargs(self, graph, block, inputcells):
        # Merge the new 'cells' with each of the block's existing input
        # variables.
        oldcells = [self.binding(a) for a in block.inputargs]
        try:
            unions = [annmodel.unionof(c1,c2) for c1, c2 in zip(oldcells,inputcells)]
        except annmodel.UnionError, e:
            # Add source code to the UnionError
            e.source = '\n'.join(source_lines(graph, block, None, long=True))
            raise
        # if the merged cells changed, we must redo the analysis
        if unions != oldcells:
            self.bindinputargs(graph, block, unions)

    def whereami(self, position_key):
        graph, block, i = position_key
        blk = ""
        if block:
            at = block.at()
            if at:
                blk = " block"+at
        opid=""
        if i is not None:
            opid = " op=%d" % i
        return repr(graph) + blk + opid

    def flowin(self, graph, block):
        try:
            i = 0
            while i < len(block.operations):
                op = block.operations[i]
                self.bookkeeper.enter((graph, block, i))
                try:
                    new_ops = op.transform(self)
                    if new_ops is not None:
                        block.operations[i:i+1] = new_ops
                        if not new_ops:
                            continue
                        new_ops[-1].result = op.result
                        op = new_ops[0]
                    self.consider_op(op)
                finally:
                    self.bookkeeper.leave()
                i += 1

        except BlockedInference as e:
            if e.op is block.raising_op:
                # this is the case where the last operation of the block will
                # always raise an exception which is immediately caught by
                # an exception handler.  We then only follow the exceptional
                # branches.
                exits = [link for link in block.exits
                              if link.exitcase is not None]

            elif e.op.opname in ('simple_call', 'call_args', 'next'):
                # XXX warning, keep the name of the call operations in sync
                # with the flow object space.  These are the operations for
                # which it is fine to always raise an exception.  We then
                # swallow the BlockedInference and that's it.
                # About 'next': see test_annotate_iter_empty_container().
                return

            else:
                # other cases are problematic (but will hopefully be solved
                # later by reflowing).  Throw the BlockedInference up to
                # processblock().
                e.opindex = i
                raise

        except annmodel.HarmlesslyBlocked:
            return

        except annmodel.AnnotatorError as e: # note that UnionError is a subclass
            e.source = gather_error(self, graph, block, i)
            raise

        else:
            # dead code removal: don't follow all exits if the exitswitch
            # is known
            exits = block.exits
            if isinstance(block.exitswitch, Variable):
                s_exitswitch = self.binding(block.exitswitch)
                if s_exitswitch.is_constant():
                    exits = [link for link in exits
                                  if link.exitcase == s_exitswitch.const]

        # filter out those exceptions which cannot
        # occour for this specific, typed operation.
        if block.canraise:
            op = block.raising_op
            can_only_throw = op.get_can_only_throw(self)
            if can_only_throw is not None:
                candidates = can_only_throw
                candidate_exits = exits
                exits = []
                for link in candidate_exits:
                    case = link.exitcase
                    if case is None:
                        exits.append(link)
                        continue
                    covered = [c for c in candidates if issubclass(c, case)]
                    if covered:
                        exits.append(link)
                        candidates = [c for c in candidates if c not in covered]

        # mapping (exitcase, variable) -> s_annotation
        # that can be attached to booleans, exitswitches
        knowntypedata = {}
        if isinstance(block.exitswitch, Variable):
            knowntypedata = getattr(self.binding(block.exitswitch),
                                    "knowntypedata", {})
        for link in exits:
            self.follow_link(graph, link, knowntypedata)
        if block in self.notify:
            # reflow from certain positions when this block is done
            for callback in self.notify[block]:
                if isinstance(callback, tuple):
                    self.reflowfromposition(callback) # callback is a position
                else:
                    callback()

    def follow_link(self, graph, link, knowntypedata):
        in_except_block = False
        v_last_exc_type = link.last_exception  # may be None for non-exception link
        v_last_exc_value = link.last_exc_value  # may be None for non-exception link

        if (isinstance(link.exitcase, (types.ClassType, type)) and
                issubclass(link.exitcase, BaseException)):
            assert v_last_exc_type and v_last_exc_value
            s_last_exc_value = self.bookkeeper.valueoftype(link.exitcase)
            s_last_exc_type = annmodel.SomeType()
            if isinstance(v_last_exc_type, Constant):
                s_last_exc_type.const = v_last_exc_type.value
            s_last_exc_type.is_type_of = [v_last_exc_value]

            if isinstance(v_last_exc_type, Variable):
                self.setbinding(v_last_exc_type, s_last_exc_type)
            if isinstance(v_last_exc_value, Variable):
                self.setbinding(v_last_exc_value, s_last_exc_value)

            s_last_exc_type = annmodel.SomeType()
            if isinstance(v_last_exc_type, Constant):
                s_last_exc_type.const = v_last_exc_type.value
            last_exc_value_vars = []
            in_except_block = True

        ignore_link = False
        inputs_s = []
        renaming = {}
        for v_out, v_input in zip(link.args, link.target.inputargs):
            renaming.setdefault(v_out, []).append(v_input)
        for v_out, v_input in zip(link.args, link.target.inputargs):
            if v_out == v_last_exc_type:
                assert in_except_block
                inputs_s.append(s_last_exc_type)
            elif v_out == v_last_exc_value:
                assert in_except_block
                inputs_s.append(s_last_exc_value)
                last_exc_value_vars.append(v_input)
            else:
                s_out = self.annotation(v_out)
                if (link.exitcase, v_out) in knowntypedata:
                    knownvarvalue = knowntypedata[(link.exitcase, v_out)]
                    s_out = pair(s_out, knownvarvalue).improve()
                    # ignore links that try to pass impossible values
                    if s_out == annmodel.s_ImpossibleValue:
                        ignore_link = True

                if hasattr(s_out,'is_type_of'):
                    renamed_is_type_of = []
                    for v in s_out.is_type_of:
                        new_vs = renaming.get(v, [])
                        renamed_is_type_of += new_vs
                    assert s_out.knowntype is type
                    newcell = annmodel.SomeType()
                    if s_out.is_constant():
                        newcell.const = s_out.const
                    s_out = newcell
                    s_out.is_type_of = renamed_is_type_of

                if hasattr(s_out, 'knowntypedata'):
                    renamed_knowntypedata = {}
                    for (value, v), s in s_out.knowntypedata.items():
                        new_vs = renaming.get(v, [])
                        for new_v in new_vs:
                            renamed_knowntypedata[value, new_v] = s
                    assert isinstance(s_out, annmodel.SomeBool)
                    newcell = annmodel.SomeBool()
                    if s_out.is_constant():
                        newcell.const = s_out.const
                    s_out = newcell
                    s_out.set_knowntypedata(renamed_knowntypedata)

                inputs_s.append(s_out)
        if ignore_link:
            return

        if in_except_block:
            s_last_exc_type.is_type_of = last_exc_value_vars
        self.links_followed[link] = True
        self.addpendingblock(graph, link.target, inputs_s)

    #___ creating the annotations based on operations ______

    def consider_op(self, op):
        # let's be careful about avoiding propagated SomeImpossibleValues
        # to enter an op; the latter can result in violations of the
        # more general results invariant: e.g. if SomeImpossibleValue enters is_
        #  is_(SomeImpossibleValue, None) -> SomeBool
        #  is_(SomeInstance(not None), None) -> SomeBool(const=False) ...
        # boom -- in the assert of setbinding()
        for arg in op.args:
            if isinstance(self.annotation(arg), annmodel.SomeImpossibleValue):
                raise BlockedInference(self, op, -1)
        resultcell = op.consider(self)
        if resultcell is None:
            resultcell = annmodel.s_ImpossibleValue
        elif resultcell == annmodel.s_ImpossibleValue:
            raise BlockedInference(self, op, -1) # the operation cannot succeed
        assert isinstance(resultcell, annmodel.SomeObject)
        assert isinstance(op.result, Variable)
        self.setbinding(op.result, resultcell)  # bind resultcell to op.result


class BlockedInference(Exception):
    """This exception signals the type inference engine that the situation
    is currently blocked, and that it should try to progress elsewhere."""

    def __init__(self, annotator, op, opindex):
        self.annotator = annotator
        try:
            self.break_at = annotator.bookkeeper.position_key
        except AttributeError:
            self.break_at = None
        self.op = op
        self.opindex = opindex

    def __repr__(self):
        if not self.break_at:
            break_at = "?"
        else:
            break_at = self.annotator.whereami(self.break_at)
        return "<BlockedInference break_at %s [%s]>" %(break_at, self.op)

    __str__ = __repr__
