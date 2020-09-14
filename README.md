# RPython WASM
This is a fork from PyPy's RPython in the original bitbucket [repo](https://bitbucket.org/pypy/pypy/src/default/rpython/) compatible to Emscripten's [emcc](https://emscripten.org/docs/tools_reference/emcc.html).

# What is RPython
Graham Jenson writes a nice [article](https://maori.geek.nz/rpython-compiling-python-to-c-for-the-speed-5411d57a5316) about it. Basically, RPython is what powers PyPy as described in its documentation [here](https://rpython.readthedocs.io/en/latest/).

# Installation
RPython it self depends on PyPy (or Python 2.7) and GCC. Install PyPy from the official [website](https://pypy.org/download.html).
<del>To compile to WASM, install Emscripten's [SDK](https://emscripten.org/docs/getting_started/downloads.html) and then change the resulting Makefile CC to emcc</del> Install emcc from Emscripten's [SDK](https://emscripten.org/docs/getting_started/downloads.html)

Install rpython with npm:
```shell
npm install rpython -g
```

Now the rpython executable is available in your PATH

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
And then compiles it to WASM with RPython:

```shell
rpython /path/to/main.py
```

It will compiles the main.py to WASM file and it's .js files (to load the WASM) to current directory.

# Compatibility
There's two things that are commented out from the RPython's source code:

- ASM call
- Pthread

Emscripten doesn't support ASM call on the resulting binary as it would make it not portable. And for now Pthread is also commented because major browsers have not implement it yet (https://emscripten.org/docs/porting/pthreads.html).
