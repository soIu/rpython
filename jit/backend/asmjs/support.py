"""

Support functions for implementing and testing the asmjs JIT backend.

This module defines the C interfaces for the asmjs JIT supporting functions,
such as jitCompile() and jitInvoke().

It also implements a pure-python version of these functions that can be used
when running untranslated, e.g. for running the test suite.  This includes
an asmjs-to-python converter so that the full round-trip through javascript
can be tested.  It is very slow and depends on the generated asmjs code being
"well-formed" in various undocumented ways.

"""

import sys
import math
import ctypes
import struct
import subprocess
import contextlib

from rpython.rlib.parsing.tree import RPythonVisitor
from rpython.rlib.parsing.ebnfparse import parse_ebnf, make_parse_function
from rpython.rtyper.lltypesystem import lltype, rffi, ll2ctypes
from rpython.translator.tool.cbuild import ExternalCompilationInfo

# First, we have the definitions of the javascript JIT helper functions.
# These have a native javascript implementation when translated; when
# untranslated they are implemented atop a rather nasty asmjs-to-python
# transplier.

compilation_info = ExternalCompilationInfo(includes=['library_jit.h'])

def jsexternal(args, result, **kwds):
    """Decorator to define stubbed-out external javascript functions.

    This decorator can be applied to a python function to register it as
    the stubbed-out implementation of an external javascript function.
    The llinterpreter will run the python code, the compiled interpreter
    will link to the javascript function of the same name.
    """
    def do_register(func):
        kwds.setdefault('_callable', func)
        kwds.setdefault('random_effects_on_gcobjs', False)
        kwds.setdefault('compilation_info', compilation_info)
        return rffi.llexternal(func.__name__, args, result, **kwds)
    return do_register


_jitCompiledFunctions = { 0: None }
_jitNextFuncId = 1


@jsexternal([rffi.CCHARP], rffi.INT)
def jitCompile(jssource):
    funcid = jitReserve()
    jitRecompile(funcid, jssource)
    return funcid


@jsexternal([], rffi.INT)
def jitReserve():
    global _jitNextFuncId
    funcid = _jitNextFuncId
    _jitNextFuncId += 1
    _jitCompiledFunctions[funcid] = None
    return funcid


@jsexternal([rffi.INT], rffi.INT)
def jitExists(funcid):
    if _jitCompiledFunctions.get(funcid, None) is not None:
        return 1
    return 0


@jsexternal([rffi.INT, rffi.CCHARP], lltype.Void)
def jitRecompile(funcid, jssource):
    jssource_str = "".join(jssource)[:-1]
    _jitCompiledFunctions[funcid] = load_asmjs(jssource_str)


@jsexternal([rffi.INT, rffi.INT], lltype.Void)
def jitCopy(srcId, dstId):
    _jitCompiledFunctions[dstId] = _jitCompiledFunctions[srcId]


@jsexternal([rffi.INT, rffi.INT, rffi.INT, rffi.INT], rffi.INT,
            _nowrapper=True, random_effects_on_gcobjs=True)
def jitInvoke(funcid, frame, tladdr, label):
    func = _jitCompiledFunctions.get(funcid, None)
    if func is None:
        res = 0
    else:
        res = int(func(frame, tladdr, label))
    return res


@jsexternal([rffi.INT], lltype.Void)
def jitFree(funcid):
    _jitCompiledFunctions.pop(funcid, None)


# Here we have a simple and slow asmjs-to-python converter.
# It allows us to re-interpret the JIT-compiled asmjs code in the context
# of the untranslated interpreter, which is very handy for testing purposes.

def load_asmjs(jssource, stdlib=None, foreign=None, heap=None):
    """Load asmjs source code as an equivalent python function."""
    #validate_asmjs(jssource)
    func = compile_asmjs(jssource)
    if heap is None:
        heap = NativeHeap()
    if foreign is None:
        foreign = FOREIGN(heap)
    if stdlib is None:
        stdlib = STDLIB(heap)
    return func(stdlib, foreign, heap)


def compile_asmjs(jssource):
    """Compile asmjs module code into an equivalent python factory function."""
    print jssource
    ast = parse_asmjs(jssource)
    visitor = CompileASMJSVisitor()
    visitor.dispatch(ast)
    pysource = visitor.getpysource()
    print pysource
    ns = {}
    pycode = compile(pysource, "<pyasmjs>", "exec")
    exec pycode in globals(), ns
    assert len(ns) == 1
    for nm, func in ns.iteritems():
        return func


_parse_asmjs = None


def parse_asmjs(jssource):
    """Parse asmjs module code into an abstract syntax tree."""
    global _parse_asmjs
    if _parse_asmjs is None:
        regexes, rules, ToAST = parse_ebnf(JSGRAMMAR)
        parse = make_parse_function(regexes, rules)

        def _parse_asmjs(jssource):
            return parse(jssource).visit(ToAST())[0]

    return _parse_asmjs(jssource)


counter = 0


def validate_asmjs(jssource):
    global counter
    tf = open("/tmp/jit.asm.%d.js" % (counter,), "w")
    counter += 1
    tf.write("var test = " + jssource)
    tf.flush()
    p = subprocess.Popen(["js", tf.name],
                         stderr=subprocess.STDOUT, stdout=subprocess.PIPE)
    if p.wait() == 0:
        stdout = p.stdout.read()
        if "successfully compiled asm.js code" not in stdout:
            print jssource
            raise ValueError(stdout)


def _int(v):
    try:
        return int(v)
    except (OverflowError, ValueError):
        if str(v) not in ('inf', '-inf', 'nan'):
            raise
        return 0


def ToInt32(v):
    return ctypes.c_int32(_int(v)).value


def ToInt16(v):
    return ctypes.c_int16(_int(v)).value


def ToInt8(v):
    return ctypes.c_int8(_int(v)).value


def ToUInt32(v):
    return ctypes.c_uint32(_int(v)).value


def ToUInt16(v):
    return ctypes.c_uint16(_int(v)).value


def ToUInt8(v):
    return ctypes.c_uint8(_int(v)).value


def NUM(v):
    return float(v)


def log(*args):
    print>>sys.stderr, " ".join(map(str, args))


def jsmod(lhs, rhs):
    # The modulo operator in javascript takes the sign of the numerator.
    # The modulo operator in python takes the sign of the denominator.
    # The below implements the former in terms of the later.
    sign = 1 if lhs >= 0 else -1
    try:
        return sign * (abs(lhs) % abs(rhs))
    except ZeroDivisionError:
        return float('NaN')


def jsdiv(lhs, rhs):
    # Doing x/0 in javascript produces infinity.
    try:
        return lhs / rhs
    except ZeroDivisionError:
        return float('inf')


class LoopException(Exception):
    def __init__(self, label=""):
        self.label = label


class ContinueLoop(LoopException):
    pass


class BreakLoop(LoopException):
    pass


class CompileASMJSVisitor(RPythonVisitor):
    """An AST visitor to translate asmjs into python source code.

    Applied to a parse tree, this visitor will generate python source code
    implementing the same semantics as the parsed asmjs code.  It depends on
    helper functions from this module (e.g. ToInt32) and so should be executed
    with this module as its global context.
    """

    def __init__(self):
        self._chunks = []
        self._indent = ""
        self._scope_stack = []
        self._next_label = ""

    def getpysource(self):
        return "".join(self._chunks)

    def emit(self, chunk):
        self._chunks.append(chunk)

    def newline(self):
        self.emit("\n")
        self.emit(self._indent)

    @contextlib.contextmanager
    def indent(self):
        self._indent = self._indent + "    "
        self.newline()
        yield None
        self._indent = self._indent[:-4]
        self.newline()

    @contextlib.contextmanager
    def emit_while(self, *conds):
        self.emit("while ")
        for cond in conds:
            if isinstance(cond, basestring):
                self.emit(cond)
            else:
                self.dispatch(cond)
        self.emit(":")
        with self.indent():
            yield None

    @contextlib.contextmanager
    def emit_if(self, *conds):
        self.emit("if ")
        for cond in conds:
            if isinstance(cond, basestring):
                self.emit(cond)
            else:
                self.dispatch(cond)
        self.emit(":")
        with self.indent():
            yield None

    @contextlib.contextmanager
    def emit_elif(self, *conds):
        self.emit("elif ")
        for cond in conds:
            if isinstance(cond, basestring):
                self.emit(cond)
            else:
                self.dispatch(cond)
        self.emit(":")
        with self.indent():
            yield None

    @contextlib.contextmanager
    def emit_else(self):
        self.emit("else:")
        with self.indent():
            yield None

    @contextlib.contextmanager
    def emit_try(self):
        self.emit("try:")
        with self.indent():
            yield None

    @contextlib.contextmanager
    def emit_except(self, types):
        self.emit("except ")
        self.emit(types)
        self.emit(":")
        with self.indent():
            yield None

    @contextlib.contextmanager
    def emit_loop(self, label):
        with self.emit_while("True"):
            with self.emit_try():
                yield None
            with self.emit_except("BreakLoop, e"):
                with self.emit_if("e.label not in ('', %r)" % (label,)):
                    self.emit("raise")
                    self.newline()
                self.emit("break")
                self.newline()
            with self.emit_except("ContinueLoop, e"):
                with self.emit_if("e.label not in ('', %r)" % (label,)):
                    self.emit("raise")
                    self.newline()
                self.emit("continue")
                self.newline()

    def visit_module(self, node):
        if node.children[0].symbol == "IDENTIFIER":
            name = node.children[0].additional_info
            declargs = node.children[1]
        else:
            name = "PYASMJS"
            declargs = node.children[0]
        self.emit("def ")
        self.emit(name)
        self.emit("(")
        self.dispatch(declargs)
        self.emit("):")
        with self.indent():
            self.dispatch(node.children[-1])

    def visit_declargs(self, node):
        if node.children:
            self.dispatch(node.children[0])
            for child in node.children[1:]:
                self.emit(", ")
                self.dispatch(child)

    def visit_modbody(self, node):
        for child in node.children:
            self.dispatch(child)

    def visit_gvardecl(self, node):
        self.dispatch(node.children[0])
        self.emit(" = ")
        self.dispatch(node.children[1])
        self.newline()

    def visit_funcdecl(self, node):
        self.emit("def ")
        self.dispatch(node.children[0])
        self.emit("(")
        self.dispatch(node.children[1])
        self.emit("):")
        with self.indent():
            self.dispatch(node.children[2])

    def visit_stmt_block(self, node):
        if not node.children:
            self.emit("pass")
            self.newline()
        else:
            for child in node.children:
                self.dispatch(child)

    def visit_stmt_if(self, node):
        with self.emit_if(node.children[0]):
            self.dispatch(node.children[1])
        if len(node.children) == 3:
            with self.emit_else():
                self.dispatch(node.children[2])

    def visit_stmt_labelled(self, node):
        self._next_label = node.children[0].additional_info
        self.dispatch(node.children[1])

    def visit_stmt_while(self, node):
        label = self._next_label
        self._next_label = ""
        self._scope_stack.append(("loop", label))
        with self.emit_loop(label):
            with self.emit_if("not (", node.children[0], ")"):
                self.emit("raise BreakLoop()")
                self.newline()
            self.dispatch(node.children[1])
        self._scope_stack.pop()

    def visit_stmt_do(self, node):
        label = self._next_label
        self._next_label = ""
        self._scope_stack.append(("loop", label))
        with self.emit_loop(label):
            self.dispatch(node.children[0])
            with self.emit_if("not (", node.children[1], ")"):
                self.emit("raise BreakLoop()")
                self.newline()
        self._scope_stack.pop()

    def visit_stmt_switch(self, node):
        label = self._next_label
        self._next_label = ""
        self._scope_stack.append(("switch", label))
        self.emit("__switchvar = ")
        self.dispatch(node.children[0])
        self.newline()
        with self.emit_try():
            with self.emit_if("False"):
                self.emit("pass")
            for child in node.children[1:]:
                self.dispatch(child)
        with self.emit_except("BreakLoop, e"):
            with self.emit_if("e.label not in ('', %r)" % (label,)):
                self.emit("raise")
                self.newline()
        self._scope_stack.pop()

    def visit_stmt_case(self, node):
        assert self._scope_stack[-1][0] == "switch"
        with self.emit_elif("__switchvar == ", node.children[0]):
            self.emit("pass")
            self.newline()
            self.dispatch(node.children[1])

    def visit_stmt_default(self, node):
        assert self._scope_stack[-1][0] == "switch"
        with self.emit_else():
            self.emit("pass")
            self.newline()
            self.dispatch(node.children[0])

    def visit_stmt_expr(self, node):
        self.dispatch(node.children[0])
        self.newline()

    def visit_stmt_var(self, node):
        self.dispatch(node.children[0])
        self.emit(" = ")
        self.dispatch(node.children[1])
        self.newline()

    def visit_stmt_assign(self, node):
        self.dispatch(node.children[0])
        self.emit(" = ")
        self.dispatch(node.children[1])
        self.newline()

    def visit_stmt_return(self, node):
        self.emit("return")
        if node.children:
            self.emit(" ")
            self.dispatch(node.children[0])
        self.newline()

    def visit_stmt_break(self, node):
        if node.children:
            label = node.children[0].additional_info
        else:
            label = ""
        self.emit("raise BreakLoop(%r)" % (label,))
        self.newline()

    def visit_stmt_continue(self, node):
        if node.children:
            label = node.children[0].additional_info
        else:
            label = ""
        self.emit("raise ContinueLoop(%r)" % (label,))
        self.newline()

    def visit_expr_cond(self, node):
        self.dispatch(node.children[1])
        self.emit(" if ")
        self.dispatch(node.children[0])
        self.emit(" else ")
        self.dispatch(node.children[2])

    def visit_expr_or(self, node):
        lhs, rhs = node.children
        self.emit("NUM(ToInt32(")
        self.dispatch(lhs)
        self.emit(") | ToInt32(")
        self.dispatch(rhs)
        self.emit("))")

    def visit_expr_xor(self, node):
        lhs, rhs = node.children
        self.emit("NUM(ToInt32(")
        self.dispatch(lhs)
        self.emit(") ^ ToInt32(")
        self.dispatch(rhs)
        self.emit("))")

    def visit_expr_and(self, node):
        lhs, rhs = node.children
        self.emit("NUM(ToInt32(")
        self.dispatch(lhs)
        self.emit(") & ToInt32(")
        self.dispatch(rhs)
        self.emit("))")

    def visit_expr_equals(self, node):
        lhs, rhs = node.children
        self.emit("NUM((")
        self.dispatch(lhs)
        self.emit(") == (")
        self.dispatch(rhs)
        self.emit("))")

    def visit_expr_notequals(self, node):
        lhs, rhs = node.children
        self.emit("NUM((")
        self.dispatch(lhs)
        self.emit(") != (")
        self.dispatch(rhs)
        self.emit("))")

    def visit_expr_lt(self, node):
        lhs, rhs = node.children
        self.emit("NUM((")
        self.dispatch(lhs)
        self.emit(") < (")
        self.dispatch(rhs)
        self.emit("))")

    def visit_expr_lteq(self, node):
        lhs, rhs = node.children
        self.emit("NUM((")
        self.dispatch(lhs)
        self.emit(") <= (")
        self.dispatch(rhs)
        self.emit("))")

    def visit_expr_gt(self, node):
        lhs, rhs = node.children
        self.emit("NUM((")
        self.dispatch(lhs)
        self.emit(") > (")
        self.dispatch(rhs)
        self.emit("))")

    def visit_expr_gteq(self, node):
        lhs, rhs = node.children
        self.emit("NUM((")
        self.dispatch(lhs)
        self.emit(") >= (")
        self.dispatch(rhs)
        self.emit("))")

    def visit_expr_lshift(self, node):
        lhs, rhs = node.children
        self.emit("NUM(ToInt32(")
        self.emit("ToInt32(")
        self.dispatch(lhs)
        self.emit(") << (ToUInt32(")
        self.dispatch(rhs)
        self.emit(") & 0x1F)")
        self.emit("))")

    def visit_expr_rshift(self, node):
        lhs, rhs = node.children
        self.emit("NUM(ToInt32(")
        self.emit("ToInt32(")
        self.dispatch(lhs)
        self.emit(") >> (ToUInt32(")
        self.dispatch(rhs)
        self.emit(") & 0x1F)")
        self.emit("))")

    def visit_expr_urshift(self, node):
        lhs, rhs = node.children
        self.emit("NUM(ToUInt32(")
        self.emit("ToUInt32(")
        self.dispatch(lhs)
        self.emit(") >> (ToUInt32(")
        self.dispatch(rhs)
        self.emit(") & 0x1F)")
        self.emit("))")

    def visit_expr_plus(self, node):
        lhs, rhs = node.children
        self.emit("(")
        self.dispatch(lhs)
        self.emit(") + (")
        self.dispatch(rhs)
        self.emit(")")

    def visit_expr_minus(self, node):
        lhs, rhs = node.children
        self.emit("(")
        self.dispatch(lhs)
        self.emit(") - (")
        self.dispatch(rhs)
        self.emit(")")

    def visit_expr_multiply(self, node):
        lhs, rhs = node.children
        self.emit("(")
        self.dispatch(lhs)
        self.emit(") * (")
        self.dispatch(rhs)
        self.emit(")")

    def visit_expr_divide(self, node):
        lhs, rhs = node.children
        self.emit("jsdiv(")
        self.dispatch(lhs)
        self.emit(", ")
        self.dispatch(rhs)
        self.emit(")")

    def visit_expr_modulo(self, node):
        lhs, rhs = node.children
        self.emit("jsmod(")
        self.dispatch(lhs)
        self.emit(", ")
        self.dispatch(rhs)
        self.emit(")")

    def visit_expr_uplus(self, node):
        self.emit("(")
        self.dispatch(node.children[0])
        self.emit(")")

    def visit_expr_uminus(self, node):
        self.emit("-(")
        self.dispatch(node.children[0])
        self.emit(")")

    def visit_expr_unot(self, node):
        self.emit("NUM( not (")
        self.dispatch(node.children[0])
        self.emit("))")

    def visit_expr_uneg(self, node):
        self.emit("NUM(~(ToInt32(")
        self.dispatch(node.children[0])
        self.emit(")))")

    def visit_expr_call(self, node):
        self.dispatch(node.children[0])
        self.emit("(")
        if len(node.children) > 1:
            self.dispatch(node.children[1])
        self.emit(")")

    def visit_callargs(self, node):
        if node.children:
            self.dispatch(node.children[0])
            for child in node.children[1:]:
                self.emit(", ")
                self.dispatch(child)

    def visit_object_name(self, node):
        self.dispatch(node.children[0])
        self.emit("[")
        self.dispatch(node.children[1])
        self.emit("]")

    def visit_dotted_name(self, node):
        self.dispatch(node.children[0])
        for child in node.children[1:]:
            self.emit(".")
            self.dispatch(child)

    def visit_IDENTIFIER(self, node):
        self.emit(node.additional_info)

    def visit_NUMBER(self, node):
        self.emit("NUM(")
        self.emit(node.additional_info)
        self.emit(")")

    def visit_STRING(self, node):
        self.emit(node.additional_info)


def makeHeapView(format, convert=lambda x: x):
    """Given native-endian struct pack format, build a heapview class.

    This is a helper function to generate python equivalents of the javascript
    typed-array classes e.g. Int8Array, Int32Array etc.  Given a native-endian
    struct format string, it produces a wrapper class that will load and store
    objects of that type from an underlying array of bytes.
    """

    itemsize = struct.calcsize(format)

    class _HeapView(object):

        def __init__(self, heap):
            self.heap = heap

        def __getitem__(self, addr):
            data = ""
            for i in xrange(itemsize):
                data += self.heap[(itemsize * addr) + i]
            return struct.unpack(format, data)[0]

        def __setitem__(self, addr, value):
            data = struct.pack(format, convert(value))
            for i in xrange(len(data)):
                self.heap[(itemsize * addr) + i] = data[i]

    return _HeapView


class STDLIB(object):
    """A re-implementation of the asmjs standard library, in python.

    This is a very incomplete re-implementation of the asmjs standard library,
    for use with the simulation asmjs runtime in this module.  It contains just
    enough functionality to run the JIT-compiled asmjs code.
    """

    def __init__(self, heap):
        self._heap = heap

    Int8Array = makeHeapView("b", ToInt8)
    Int16Array = makeHeapView("h", ToInt16)
    Int32Array = makeHeapView("i", ToInt32)
    Uint8Array = makeHeapView("B", ToUInt8)
    Uint16Array = makeHeapView("H", ToUInt16)
    Uint32Array = makeHeapView("I", ToUInt32)
    Float32Array = makeHeapView("f", NUM)
    Float64Array = makeHeapView("d", NUM)

    class Math(object):

        @staticmethod
        def imul(a, b):
            return NUM(ToInt32((ToInt32(a) * ToInt32(b))))

        @staticmethod
        def sqrt(a):
            return NUM(math.sqrt(a))


# We need some 8-byte-aligned memory to use as temporary storage
# when loading/storing unaligned floats.

tempDoubleStorage = ctypes.create_string_buffer(12)
tempDoublePtr = ctypes.addressof(tempDoubleStorage)
if tempDoublePtr % 8 != 0:
    assert tempDoublePtr % 8 == 4
    tempDoublePtr += 4


# Here we have a basic implementation of the foreign-function object as it
# might look when generated by emscripten.  The various dynCall_XXX methods
# and generated automatically when accessed.

class FOREIGN(object):
    """A python re-implementation of the emscripten foreign-function object.

    This is a very incomplete re-implementation of the asmjs foreign function
    environment provided by emscripten.  It simulates the basic utilities that
    will be available to the JIT-compiled code as runtime
    """

    TYPEMAP = {"v": None, "i": ctypes.c_int32, "d": ctypes.c_double}

    tempDoublePtr = tempDoublePtr

    def __init__(self, heap):
        self._heap = heap

    def _memcpy(self, dst, src, size):
        return self._heap.memmove(dst, src, size)

    def _memset(self, dst, c, count):
        return self._heap.memset(dst, c, count)

    def _gettimeofday(self, ptr):
        raise NotImplementedError

    def _abort(self):
        raise RuntimeError("ABORT")

    def __getattr__(self, name):
        # On-demand generation of dynCall_XXX methods.
        # Emscripten provides one such method per type signature of function.
        # We provide a matching method on-demand, that dispatches to an
        # in-process function pointer via the magic of ctypes.
        if name.startswith("dynCall_"):
            callsig = name[len("dynCall_"):]
            restype = self.TYPEMAP[callsig[0]]
            argtypes = [self.TYPEMAP[c] for c in callsig[1:]]
            argconvs = [int if c == "i" else lambda a:a for c in callsig[1:]]
            functype = ctypes.CFUNCTYPE(restype, *argtypes)

            def dynCall(funcaddr, *args):
                funcaddr = self._heap.validate_addr(funcaddr)
                args = [conv(a) for (conv, a) in zip(argconvs, args)]
                # We cheat slightly here.  The ctypes callback won't
                # raise any exceptions, but it will record them in a
                # special private area of the ll2ctypes module.
                res = functype(funcaddr)(*args)
                if ll2ctypes._callback_exc_info is not None:
                    exc_typ, exc_val, exc_tb = ll2ctypes._callback_exc_info
                    ll2ctypes._callback_exc_info = None
                    raise exc_typ, exc_val, exc_tb
                if callsig[0] != "v":
                    return NUM(res)

            return dynCall
        # Allow calling our own exported functions via ffi.
        if name.startswith("_jit") and name[1:] in globals():
            func = globals()[name[1:]]

            def callSupportFunc(*args):
                return func(*map(int, args))

            return callSupportFunc
        raise AttributeError("Unknown foreign function: %s" % (name,))


class NativeHeap(object):
    """A python re-implementation of the asmjs heap memory object.

    This is a very basic re-implementation of the asmjs heap memory object,
    exposing the memory space of the current process as a simple array of
    bytes.  Tremendously slow and unsafe!  But it lets us run the tests.
    """

    @staticmethod
    def validate_addr(addr):
        # XXX TODO: how to quickly check for obviously-bad pointers?
        return ToUInt32(addr)

    def memmove(self, dst, src, size):
        dst = self.validate_addr(dst)
        src = self.validate_addr(src)
        return ctypes.memmove(dst, src, int(size))

    def memset(self, dst, c, count):
        dst = self.validate_addr(dst)
        return ctypes.memset(dst, int(c), int(count))

    def __getitem__(self, addr):
        return ctypes.string_at(self.validate_addr(addr), 1)

    def __setitem__(self, addr, char):
        ctypes.memmove(self.validate_addr(addr), char, 1)


# A partial grammar for javascript.
#
# This is just enough of a javascript grammar to parse the jit-generated
# asmjs code using the functions from rpython.rlib.parsing.

JSGRAMMAR = r"""

IGNORE: "[ \f\t\n]*|//[^\n]*";
IDENTIFIER: "[a-zA-Z_][a-zA-Z0-9_]*";
NUMBER: "[0-9]+(\.[0-9]+(e(\+|\-)?[0-9]+)?)?";
STRING: "\"[^\\\"\n]*\"";

module: ["function"] IDENTIFIER? ["("] declargs [")" "{"] modbody ["}"];

declargs: [""] | IDENTIFIER ([","] IDENTIFIER)*;

modbody: ["\"use asm\"\;"] gvardecl* funcdecl+ stmt_return [";"];

gvardecl: ["var"] IDENTIFIER ["="] ["new"]? expr [";"];

funcdecl: ["function"] IDENTIFIER ["("] declargs [")"] stmt_block;


# All the different kinds of statement.

stmt: <stmt_block> | <stmt_if> | <stmt_while> | <stmt_labelled> |
      <stmt_do> | <stmt_switch> | <stmt_line> [";"];

stmt_line: <stmt_assign> | <stmt_var> | <stmt_expr> |
           <stmt_return> | <stmt_break> | <stmt_continue>;

stmt_block: ["{"] stmt* ["}"];

stmt_if: ["if" "("] expr [")"] stmt (["else"] stmt)?;

stmt_labelled: IDENTIFIER [":"] stmt;

stmt_while: ["while" "("] expr [")"] stmt;

stmt_do: ["do"] stmt ["while" "("] expr [")"];

stmt_switch: ["switch" "("] expr [")" "{"] stmt_case* ["}"];

stmt_case: ["case" "("] expr [")" ":"] stmt | <stmt_default>;

stmt_default: ["default" ":"] stmt;

stmt_expr: expr;

stmt_var: ["var"] IDENTIFIER ["="] expr;

stmt_assign: object_name ["="] expr;

stmt_return: ["return"] expr?;

stmt_break: ["break"] IDENTIFIER?;

stmt_continue: ["continue"] IDENTIFIER?;


# All the different kinds of expression.
# We use the flow of the grammar to implement basic operator precedence.

expr: <expr_cond>;

expr_cond: expr_or ["?"] expr [":"] expr | <expr_or>;

expr_or: expr_xor ["|"] expr | <expr_xor>;

expr_xor: expr_and ["^"] expr | <expr_and>;

expr_and: expr_eqcmp ["&"] expr | <expr_eqcmp>;

expr_eqcmp: <expr_equals> | <expr_notequals> | <expr_rel>;

expr_equals: expr_rel ["=="] expr;

expr_notequals: expr_rel ["!="] expr;

expr_rel: <expr_lt> | <expr_lteq> | <expr_gt> | <expr_gteq> | <expr_shift>;

expr_lt: expr_shift ["<"] expr;

expr_lteq: expr_shift ["<="] expr;

expr_gt: expr_shift [">"] expr;

expr_gteq: expr_shift [">="] expr;

expr_shift: <expr_lshift> | <expr_rshift> | <expr_urshift> | <expr_addish>;

expr_lshift: expr_addish ["<<"] expr;

expr_rshift: expr_addish [">>"] expr;

expr_urshift: expr_addish [">>>"] expr;

expr_addish: <expr_plus> | <expr_minus> | <expr_multish>;

expr_plus: expr_multish ["+"] expr;

expr_minus: expr_multish ["-"] expr;

expr_multish: <expr_multiply> | <expr_divide> | <expr_modulo> | <expr_term>;

expr_multiply: expr_term ["*"] expr;

expr_divide: expr_term ["/"] expr;

expr_modulo: expr_term ["%"] expr;

expr_term: <expr_uplus> | <expr_uminus> | <expr_unot> |
           <expr_uneg> | <expr_call>;

expr_uplus: ["+"] expr_term;

expr_uminus: ["-"] expr_term;

expr_unot: ["!"] expr_term;

expr_uneg: ["~"] expr_term;

expr_call: expr_unit ["(" ")"] | expr_unit ["("] callargs [")"] | <expr_unit>;

expr_unit: ["("] <expr> [")"] | <NUMBER> | <object_name> | <STRING>;

object_name: IDENTIFIER ["["] expr ["]"] | <dotted_name> | <IDENTIFIER>;

dotted_name: IDENTIFIER (["."] IDENTIFIER)+;

callargs: expr ([","] expr)*;

"""

if __name__ == "__main__":
    load_asmjs(open(sys.argv[1], "r").read())
