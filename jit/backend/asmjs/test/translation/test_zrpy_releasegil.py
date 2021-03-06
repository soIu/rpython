import py
from rpython.jit.backend.llsupport.test.zrpy_releasegil_test import ReleaseGILTests


class TestShadowStack(ReleaseGILTests):
    gcrootfinder = "shadowstack"

    # XXX TODO: this needs to read a logfile written by the process, which
    # won't work under asmjs because it can't access the real filesystem.
    # Need to work on a live-filesystem-access module for emscripten?

    def test_simple(self):
        py.test.xfail("needs filesystem access")

    def test_close_stack(self):
        py.test.xfail("needs filesystem access")
  
