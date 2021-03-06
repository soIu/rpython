
import os
from rpython.rlib.objectmodel import we_are_translated
from rpython.rtyper.lltypesystem import lltype, rffi

from rpython.jit.backend.asmjs import jsvalue as jsval
from rpython.jit.backend.asmjs.arch import SANITYCHECK


class ASMJSFragment(object):

    def __init__(self, source, all_intvars, all_doublevars, functions):
        self.source = source
        self.all_intvars = all_intvars
        self.all_doublevars = all_doublevars
        self.imported_functions = functions


class ASMJSBuilder(object):
    """Class for building ASMJS assembly of a trace."""

    def __init__(self, cpu):
        self.cpu = cpu
        self.source_chunks = []
        self.all_intvars = {}
        self.all_doublevars = {}
        self.free_intvars = []
        self.free_doublevars = []
        self.imported_functions = {}
        self.helper_functions = {}

    def finish(self):
        return "".join(self._build_prelude() +
                       self.source_chunks +
                       self._build_epilog())

    def _build_prelude(self):
        chunks = []
        # Standard asmjs prelude stuff.
        chunks.append('function M(stdlib, foreign, heap){\n')
        chunks.append('"use asm";\n')
        chunks.append('var HI8 = new stdlib.Int8Array(heap);\n')
        chunks.append('var HI16 = new stdlib.Int16Array(heap);\n')
        chunks.append('var HI32 = new stdlib.Int32Array(heap);\n')
        chunks.append('var HU8 = new stdlib.Uint8Array(heap);\n')
        chunks.append('var HU16 = new stdlib.Uint16Array(heap);\n')
        chunks.append('var HU32 = new stdlib.Uint32Array(heap);\n')
        chunks.append('var HF32 = new stdlib.Float32Array(heap);\n')
        chunks.append('var HF64 = new stdlib.Float64Array(heap);\n')
        chunks.append('var imul = stdlib.Math.imul;\n')
        chunks.append('var sqrt = stdlib.Math.sqrt;\n')
        # Import any required names from the Module object.
        chunks.append('var tempDoublePtr = foreign.tempDoublePtr|0;\n')
        chunks.append('var jitInvoke = foreign._jitInvoke;\n')
        for funcname in self.imported_functions:
            chunks.append('var %s = foreign.%s;\n' % (funcname, funcname))
        # Definitions for any helper functions.
        for helper_chunks in self.helper_functions.itervalues():
            chunks.extend(helper_chunks)
        # The function definition, including variable declarations.
        chunks.append('function F(frame, tladdr, label){\n')
        chunks.append('frame=frame|0;\n')
        chunks.append('tladdr=tladdr|0;\n')
        chunks.append('label=label|0;\n')
        for var in self.all_intvars.itervalues():
            chunks.append("var %s=0;\n" % (var.varname,))
        for var in self.all_doublevars.itervalues():
            chunks.append("var %s=0.0;\n" % (var.varname,))
        return chunks

    def _build_epilog(self):
        chunks = []
        # The compiled function always returns frame when it's finished.
        chunks.append('return frame|0;\n')
        chunks.append('}\n')
        # Export the singleton compiled function.
        chunks.append('return F;\n')
        chunks.append('}\n')
        return chunks

    def allocate_intvar(self, num=-1):
        """Allocate a variable of type int.

        If there is a previously-freed variable of appropriate type then
        that variable is re-used; otherwise a fresh variable name is
        allocated and emitted.
        """
        if num >= 0:
            var = self.all_intvars.get(num, None)
            if var is None:
                var = jsval.IntVar("i%d" % (num,))
                self.all_intvars[num] = var
            else:
                try:
                    self.free_intvars.remove(var)
                except ValueError:
                    pass
        else:
            if len(self.free_intvars) > 0:
                var = self.free_intvars.pop()
            else:
                num = len(self.all_intvars)
                while num in self.all_intvars:
                    num += 1
                var = jsval.IntVar("i%d" % (num,))
                self.all_intvars[num] = var
        return var

    def allocate_doublevar(self, num=-1):
        """Allocate a variable of type double.

        If there is a previously-freed variable of appropriate type then
        that variable is re-used; otherwise a fresh variable name is
        allocated and emitted.
        """
        if num >= 0:
            var = self.all_doublevars.get(num, None)
            if var is None:
                var = jsval.DoubleVar("f%d" % (num,))
                self.all_doublevars[num] = var
            else:
                try:
                    self.free_doublevars.remove(var)
                except ValueError:
                    pass
        else:
            if len(self.free_doublevars) > 0:
                var = self.free_doublevars.pop()
            else:
                num = len(self.all_doublevars)
                while num in self.all_doublevars:
                    num += 1
                var = jsval.DoubleVar("f%d" % (num,))
                self.all_doublevars[num] = var
        return var

    def free_intvar(self, var):
        """Free up the given int variable for future re-use."""
        assert isinstance(var, jsval.IntVar)
        self.free_intvars.append(var)

    def free_doublevar(self, var):
        """Free up the given double variable for future re-use."""
        assert isinstance(var, jsval.DoubleVar)
        self.free_doublevars.append(var)

    def emit(self, code):
        """Emit the given string directly into the generated code."""
        self.source_chunks.append(code)

    def emit_statement(self, code):
        """Emit the given string as a statement into.

        This sanity-checks and appends an appropriate statement terminator.
        """
        self.emit(code)
        if code.endswith("}"):
            self.emit("\n")
        else:
            self.emit(";\n")

    def emit_value(self, val):
        """Emit a reference to a ASMJSValue in the generated source."""
        if val is None:
            self.emit_value(jsval.zero)
        elif isinstance(val, jsval.ASMJSValue):
            val.emit_value(self)
        elif isinstance(val, jsval.ConstInt):
            intval = (rffi.cast(lltype.Signed, val.getint()))
            self.emit(str(intval))
        elif isinstance(val, jsval.ConstPtr):
            refval = (rffi.cast(lltype.Signed, val.getref_base()))
            self.emit(str(refval))
        elif isinstance(val, jsval.ConstFloat):
            # RPython doesn't have %r format for float repr.
            # XXX TODO: how to do this with no loss of precsion?
            if not we_are_translated():
                self.emit("%r" % (val.getfloat(),))
            else:
                self.emit("%f" % (val.getfloat(),))
        else:
            os.write(2, "Unknown js value type: %s" % (val,))
            raise RuntimeError("Unknown js value type: %s" % (val,))

    def emit_expr(self, val):
        self.emit_value(val)
        self.emit(";\n")

    def emit_assignment(self, target, value):
        """Emit an assignment of a value to the given target."""
        assert isinstance(target, jsval.Variable)
        self.emit(target.varname)
        self.emit("=")
        if jsval.istype(target, jsval.Double):
            if not jsval.istype(value, jsval.Double):
                self.emit("+")
        self.emit("(")
        self.emit_value(value)
        self.emit(")")
        if jsval.istype(target, jsval.Int):
            if not jsval.istype(value, jsval.Int):
                self.emit("|0")
        self.emit(";\n")

    def emit_load(self, target, addr, typ):
        """Emit a typed load operation from the heap into a target.

        Given a result-storing target, a heap address, and an optional heap
        type, this method generates a generic "load from heap" instruction of
        the following form:

            target = HEAPVIEW[(addr) >> shift];

        """
        assert isinstance(addr, jsval.AbstractValue)
        # When running untranslated, we can't guarantee alignment of doubles.
        # Read it as two 32bit ints into a properly aligned chunk.
        if not we_are_translated() and typ is jsval.Float64:
            tempaddr = self.allocate_intvar()
            self.emit_assignment(tempaddr, addr)
            addr = jsval.tempDoublePtr
            pt1 = jsval.HeapData(jsval.Int32, tempaddr)
            self.emit_store(pt1, addr, jsval.Int32)
            pt2 = jsval.HeapData(jsval.Int32, jsval.Plus(tempaddr, jsval.word))
            self.emit_store(pt2, jsval.Plus(addr, jsval.word), jsval.Int32)
            self.free_intvar(tempaddr)
        # Now we can do the actual load.
        assert isinstance(target, jsval.Variable)
        self.emit(target.varname)
        self.emit("=")
        if jsval.istype(target, jsval.Double):
            self.emit("+")
        self.emit("(")
        self.emit(typ.heap_name)
        self.emit("[(")
        self.emit_value(addr)
        self.emit(")>>")
        self.emit(str(typ.shift))
        self.emit("])")
        if jsval.istype(target, jsval.Int):
            self.emit("|0")
        self.emit(";\n")

    def emit_store(self, value, addr, typ):
        """Emit a typed store operation of a value into the heap.

        Given a jsvalue, a heap address, and a HeapType, this method generates
        a generic "store to heap" instruction of the following form:

            HEAPVIEW[(addr) >> shift] = value;

        """
        assert isinstance(addr, jsval.AbstractValue)
        # When running untranslated, we can't guarantee alignment of doubles.
        # We have to store it into a properly aligned chunk, then
        # copy to final destination as two 32-bit ints.
        tempaddr = None
        if not we_are_translated() and typ is jsval.Float64:
            tempaddr = self.allocate_intvar()
            self.emit_assignment(tempaddr, addr)
            addr = jsval.tempDoublePtr
        self.emit(typ.heap_name)
        self.emit("[(")
        self.emit_value(addr)
        self.emit(")>>")
        self.emit(str(typ.shift))
        self.emit("]=")
        self.emit_value(value)
        self.emit(";\n")
        if not we_are_translated() and typ is jsval.Float64:
            pt1 = jsval.HeapData(jsval.Int32, addr)
            self.emit_store(pt1, tempaddr, jsval.Int32)
            pt2 = jsval.HeapData(jsval.Int32, jsval.Plus(addr, jsval.word))
            self.emit_store(pt2, jsval.Plus(tempaddr, jsval.word), jsval.Int32)
            self.free_intvar(tempaddr)

    def emit_continue(self, label=""):
        if label:
            self.emit_statement("continue %s" % (label,))
        else:
            self.emit_statement("continue")

    def emit_break(self, label=""):
        if label:
            self.emit_statement("break %s" % (label,))
        else:
            self.emit_statement("break")

    def emit_exit(self):
        """Emit an immediate return from the function."""
        self.emit("return frame|0;\n")

    def emit_assert(self, check, msg="ASSERTION FAILED", args=None):
        if SANITYCHECK:
            with self.emit_if_block(jsval.UNot(check)):
                self.emit_debug(msg, args)
                self.emit_expr(jsval.CallFunc("abort", []))

    def emit_comment(self, msg):
        if SANITYCHECK:
            self.emit("// %s\n" % (msg,))

    def emit_debug(self, msg, values=None):
        if SANITYCHECK:
            if we_are_translated():
                self.emit("print(\"%s\"" % (msg,))
            else:
                self.emit("log(\"%s\"" % (msg,))
            if values:
                for i in xrange(len(values)):
                    self.emit(",")
                    self.emit_value(values[i])
            self.emit(");\n")

    def emit_if_block(self, test):
        return ctx_if_block(self, test)

    def emit_else_block(self):
        return ctx_else_block(self)

    def emit_while_block(self, test, label=""):
        return ctx_while_block(self, test, label)

    def emit_do_block(self, test, label=""):
        return ctx_do_block(self, test, label)

    def emit_switch_block(self, value, label=""):
        return ctx_switch_block(self, value, label)

    def emit_case_block(self, value):
        return ctx_case_block(self, value)

    def emit_fragment(self, fragment):
        self.source_chunks.append(fragment.source)
        for x in fragment.all_intvars:
            if x not in self.all_intvars:
                self.all_intvars[x] = jsval.IntVar("i%d" % (x,))
        for x in fragment.all_doublevars:
            if x not in self.all_doublevars:
                self.all_doublevars[x] = jsval.DoubleVar("f%d" % (x,))
        self.imported_functions.update(fragment.imported_functions)

    def capture_fragment(self):
        fragment = ASMJSFragment("".join(self.source_chunks),
                                 list(self.all_intvars),
                                 list(self.all_doublevars),
                                 self.imported_functions)
        del self.source_chunks[:]
        return fragment

    def has_helper_func(self, name):
        return name in self.helper_functions

    def make_helper_func(self, name, argtypes):
        return ctx_make_helper_func(self, name, argtypes)

    def emit_call_helper_func(self, name, args):
        self.source_chunks.append(name)
        self.source_chunks.append("(frame")
        for i in xrange(len(args)):
            self.source_chunks.append(",")
            self.emit_value(args[i])
        self.source_chunks.append(")|0;\n")  # alway returns frame


class ctx_if_block(object):

    def __init__(self, bldr, test):
        self.bldr = bldr
        if not jsval.istype(test, jsval.Int):
            test = jsval.IntCast(test)
        self.test = test

    def __enter__(self):
        self.bldr.emit("if(")
        self.bldr.emit_value(self.test)
        self.bldr.emit("){\n")
        return self.bldr

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.bldr.emit("}\n")


class ctx_else_block(object):

    def __init__(self, bldr):
        self.bldr = bldr

    def __enter__(self):
        self.bldr.emit("else {\n")
        return self.bldr

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.bldr.emit("}\n")


class ctx_while_block(object):

    def __init__(self, bldr, test, label=""):
        self.bldr = bldr
        if not jsval.istype(test, jsval.Int):
            test = jsval.IntCast(test)
        self.test = test
        self.label = label

    def __enter__(self):
        if self.label:
            self.bldr.emit(self.label)
            self.bldr.emit(":")
        self.bldr.emit("while(")
        self.bldr.emit_value(self.test)
        self.bldr.emit("){\n")
        return self.bldr

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.bldr.emit("}\n")


class ctx_do_block(object):

    def __init__(self, bldr, test, label=""):
        self.bldr = bldr
        if not jsval.istype(test, jsval.Int):
            test = jsval.IntCast(test)
        self.test = test
        self.label = label

    def __enter__(self):
        if self.label:
            self.bldr.emit(self.label)
            self.bldr.emit(":")
        self.bldr.emit("do {\n")
        return self.bldr

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.bldr.emit("\n} while (")
        self.bldr.emit_value(self.test)
        self.bldr.emit(")\n")


class ctx_switch_block(object):

    def __init__(self, bldr, value, label=""):
        self.bldr = bldr
        if not jsval.istype(value, jsval.Signed):
            value = jsval.SignedCast(value)
        self.value = value
        self.label = label

    def __enter__(self):
        if self.label:
            self.bldr.emit(self.label)
            self.bldr.emit(":")
        self.bldr.emit("switch(")
        self.bldr.emit_value(self.value)
        self.bldr.emit("){\n")
        return self.bldr

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.bldr.emit("}\n")


class ctx_case_block(object):

    def __init__(self, bldr, value):
        self.bldr = bldr
        if not jsval.istype(value, jsval.Signed):
            value = jsval.SignedCast(value)
        self.value = value

    def __enter__(self):
        self.bldr.emit("case (")
        self.bldr.emit_value(self.value)
        self.bldr.emit("): {\n")
        return self.bldr

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.bldr.emit("break;\n}\n")


class ctx_make_helper_func(object):

    def __init__(self, bldr, name, argtypes):
        self.bldr = bldr
        self.name = name
        self.argtypes = argtypes
        self.helper_builder = ASMJSBuilder(self.bldr.cpu)
        self.argvars = [None] * len(self.argtypes)
        for i in xrange(len(self.argtypes)):
            if self.argtypes[i] == "i":
                self.argvars[i] = self.helper_builder.allocate_intvar()
            else:
                assert self.argtypes[i] == "d"
                self.argvars[i] = self.helper_builder.allocate_doublevar()

    def __enter__(self):
        return self.helper_builder

    def __exit__(self, exc_type, exc_val, exc_tb):
        chunks = []
        chunks.append("function %s(frame" % (self.name,))
        for i in xrange(len(self.argvars)):
            chunks.append(",")
            chunks.append(self.argvars[i].varname)
        chunks.append("){\n")
        chunks.append("frame = frame|0;\n")
        for i in xrange(len(self.argtypes)):
            chunks.append(self.argvars[i].varname)
            chunks.append("=")
            if self.argtypes[i] == "i":
                chunks.append(self.argvars[i].varname)
                chunks.append("|0;\n")
            else:
                chunks.append("+")
                chunks.append(self.argvars[i].varname)
                chunks.append(";\n")
        for var in self.helper_builder.all_intvars.itervalues():
            if var not in self.argvars:
                chunks.append("var %s=0;\n" % (var.varname,))
        for var in self.helper_builder.all_doublevars.itervalues():
            if var not in self.argvars:
                chunks.append("var %s=0.0;\n" % (var.varname,))
        chunks.extend(self.helper_builder.source_chunks)
        chunks.append("return frame|0;\n}\n")
        self.bldr.helper_functions[self.name] = chunks
        self.bldr.imported_functions.update(self.helper_builder.imported_functions)
