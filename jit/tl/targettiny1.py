from rpython.jit.tl import tiny1
from rpython.jit.codegen.hlinfo import highleveljitinfo


def entry_point(args):
    """Main entry point of the stand-alone executable:
    takes a list of strings and returns the exit code.
    """
    # store args[0] in a place where the JIT log can find it (used by
    # viewcode.py to know the executable whose symbols it should display)
    highleveljitinfo.sys_executable = args[0]
    if len(args) < 4:
        print "Usage: %s bytecode x y" % (args[0],)
        return 2
    bytecode = args[1]
    x = int(args[2])
    y = int(args[3])
    res = tiny1.ll_plus_minus(bytecode, x, y)
    print res
    return 0

def target(driver, args):
    return entry_point, None

# ____________________________________________________________

from rpython.jit.hintannotator.policy import HintAnnotatorPolicy

class MyHintAnnotatorPolicy(HintAnnotatorPolicy):
    novirtualcontainer = True
    oopspec = True

def portal(driver):
    """Return the 'portal' function, and the hint-annotator policy.
    The portal is the function that gets patched with a call to the JIT
    compiler.
    """
    return tiny1.ll_plus_minus, MyHintAnnotatorPolicy()
