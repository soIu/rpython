import py
def pytest_runtest_setup(item):
    from rpython.translator import platform
    if platform.platform.name != "emscripten":
        py.test.skip("test requires emscripten platform")
