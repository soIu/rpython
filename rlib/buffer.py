"""
Buffer protocol support.
"""
from rpython.rlib import jit


class Buffer(object):
    """Abstract base class for buffers."""
    __slots__ = ['readonly']
    _immutable_ = True

    def getlength(self):
        raise NotImplementedError

    def __len__(self):
        res = self.getlength()
        assert res >= 0
        return res

    def as_str(self):
        "Returns an interp-level string with the whole content of the buffer."
        # May be overridden.
        return self.getslice(0, self.getlength(), 1, self.getlength())

    def getitem(self, index):
        "Returns the index'th character in the buffer."
        raise NotImplementedError   # Must be overriden.  No bounds checks.

    def __getitem__(self, i):
        return self.getitem(i)

    def getslice(self, start, stop, step, size):
        # May be overridden.  No bounds checks.
        return ''.join([self.getitem(i) for i in range(start, stop, step)])

    def __getslice__(self, start, stop):
        return self.getslice(start, stop, 1, stop - start)

    def setitem(self, index, char):
        "Write a character into the buffer."
        raise NotImplementedError   # Must be overriden.  No bounds checks.

    def __setitem__(self, i, char):
        return self.setitem(i, char)

    def setslice(self, start, string):
        # May be overridden.  No bounds checks.
        for i in range(len(string)):
            self.setitem(start + i, string[i])

    def get_raw_address(self):
        raise ValueError("no raw buffer")


class StringBuffer(Buffer):
    __slots__ = ['value']
    _immutable_ = True

    def __init__(self, value):
        self.value = value
        self.readonly = True

    def getlength(self):
        return len(self.value)

    def as_str(self):
        return self.value

    def getitem(self, index):
        return self.value[index]

    def getslice(self, start, stop, step, size):
        if size == 0:
            return ""
        if step == 1:
            assert 0 <= start <= stop
            return self.value[start:stop]
        return Buffer.getslice(self, start, stop, step, size)


class SubBuffer(Buffer):
    __slots__ = ['buffer', 'offset', 'size']
    _immutable_ = True

    def __init__(self, buffer, offset, size):
        self.readonly = buffer.readonly
        self.buffer = buffer
        self.offset = offset
        self.size = size

    def getlength(self):
        at_most = self.buffer.getlength() - self.offset
        if 0 <= self.size <= at_most:
            return self.size
        elif at_most >= 0:
            return at_most
        else:
            return 0

    def getitem(self, index):
        return self.buffer.getitem(self.offset + index)

    def getslice(self, start, stop, step, size):
        if start == stop:
            return ''     # otherwise, adding self.offset might make them
                          # out of bounds
        return self.buffer.getslice(self.offset + start, self.offset + stop,
                                    step, size)

    def setitem(self, index, char):
        self.buffer.setitem(self.offset + index, char)

    def setslice(self, start, string):
        if len(string) == 0:
            return        # otherwise, adding self.offset might make 'start'
                          # out of bounds
        self.buffer.setslice(self.offset + start, string)

    def get_raw_address(self):
        from rpython.rtyper.lltypesystem import rffi
        ptr = self.buffer.get_raw_address()
        return rffi.ptradd(ptr, self.offset)
