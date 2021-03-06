from rpython.rtyper.lltypesystem import lltype, rffi
from rpython.rlib.rarithmetic import intmask
from rpython.rlib.debug import debug_start, debug_print, debug_stop
from rpython.rlib.debug import have_debug_prints
from rpython.jit.backend.llsupport.asmmemmgr import BlockBuilderMixin
from rpython.jit.backend.x86.rx86 import X86_32_CodeBuilder, X86_64_CodeBuilder
from rpython.jit.backend.x86.regloc import LocationCodeBuilder
from rpython.jit.backend.x86.arch import IS_X86_32, IS_X86_64, WORD
from rpython.jit.backend.x86 import valgrind

# XXX: Seems nasty to change the superclass of MachineCodeBlockWrapper
# like this
if IS_X86_32:
    codebuilder_cls = X86_32_CodeBuilder
    backend_name = 'x86'
elif IS_X86_64:
    codebuilder_cls = X86_64_CodeBuilder
    backend_name = 'x86_64'


class MachineCodeBlockWrapper(BlockBuilderMixin,
                              LocationCodeBuilder,
                              codebuilder_cls):
    def __init__(self):
        self.init_block_builder()
        codebuilder_cls.__init__(self)
        # a list of relative positions; for each position p, the bytes
        # at [p-4:p] encode an absolute address that will need to be
        # made relative.  Only works on 32-bit!
        if WORD == 4:
            self.relocations = []
        else:
            self.relocations = None
        #
        # ResOperation --> offset in the assembly.
        # ops_offset[None] represents the beginning of the code after the last op
        # (i.e., the tail of the loop)
        self.ops_offset = {}

    def add_pending_relocation(self):
        self.relocations.append(self.get_relative_pos())

    def mark_op(self, op):
        pos = self.get_relative_pos()
        self.ops_offset[op] = pos

    def copy_to_raw_memory(self, addr):
        self._copy_to_raw_memory(addr)
        if self.relocations is not None:
            for reloc in self.relocations:       # for 32-bit only
                p = addr + reloc
                adr = rffi.cast(rffi.INTP, p - 4)
                adr[0] = rffi.cast(rffi.INT, intmask(adr[0]) - p)
        valgrind.discard_translations(addr, self.get_relative_pos())
        self._dump(addr, "jit-backend-dump", backend_name)
