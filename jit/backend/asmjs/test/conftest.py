import py
def pytest_runtest_setup(item):
    from rpython.translator import platform
    if platform.platform.name != "emscripten":
        py.test.skip("test requires emscripten platform")
    from rpython.rtyper.lltypesystem import ll2ctypes
    try:
        ll2ctypes.do_allocation_in_far_regions()
    except Exception:
        # This doesn't work so well when platform=="emscripten".
        # No harm, no foul.
        pass
