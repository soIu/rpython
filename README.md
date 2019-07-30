# RPython WASM
This is a fork from PyPy's RPython in the original bitbucket [repo](https://bitbucket.org/pypy/pypy/src/default/rpython/) compatible to Emscripten's [emcc](https://emscripten.org/docs/tools_reference/emcc.html).

# What is RPython
Graham Jenson writes a nice article about [it](https://maori.geek.nz/rpython-compiling-python-to-c-for-the-speed-5411d57a5316). Basically, RPython is what powers PyPy as described [here](https://rpython.readthedocs.io/en/latest/).

# Installation
RPython it self depends on PyPy and GCC. Install PyPy from the official [website](https://pypy.org/download.html).
To compile to WASM, install Emscripten's [SDK](https://emscripten.org/docs/getting_started/downloads.html) and then change the resulting Makefile CC to emcc

# Usage
For example create a python file named main.py:
```python
def main(argv):
    print('hello from rpython')
    return 0

def target(*args):
    return main, None

if __name__ == '__main__':
    import sys
    main(sys.argv)
```
And then compiles it to C with RPython:

```shell
cd rpython-wasm/
pypy bin/rpython /path/to/main.py
```

It will compiles the main.py to an executable named main-c.

# Compatibility
There's two things that are commented out from the RPython's source code:

- ASM call
- Pthread

Emscripten doesn't support ASM call on the resulting binary as it would make it not portable. And for now Pthread is also commented because major browsers have not implement it yet (https://emscripten.org/docs/porting/pthreads.html).
