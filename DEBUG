# Compile-time Debugging
Generally RPython will statically compile your program so every RPython-level code that successfully goes to the WebAssembly binary should not throw error unless it is a MemoryError or the likes (I/O, disk, etc). In [Limitation](https://github.com/soIu/rpython#limitation) there's some guides to understand what does the compiler error means.

# Runtime Debugging
On the other hand, JS-level code _can_ throws error. Whether it is from another library that your RPython code calls or Network Error or anything else. Before, everytime a JS error was thrown at RPython scope (a JS code that was called from RPython) it used to stop the RPython runtime and makes it unusable because WebAssembly doesn't directly supports catching error right now. But because we implement the memory reset mechanism [here](https://github.com/soIu/rpython/blob/master/javascript/utils/snapshot_memory.js), now everytime an Error occurs it resets the memory back to the original state.

We provide some command line flags to debug from where the JS error was triggered.

## --debug
```shell
rpython file.py --debug
```

This flag turns on the [-g3](https://emscripten.org/docs/tools_reference/emcc.html#emcc-g3) flag for emcc and prints RPython function names that is being called.
For example, with this code:

```python
from javascript import JSON, Object, Error, function

@function
def throw_error():
    Error('error from rpython')

def main(argv):
    Object('setTimeout').call(JSON.fromFunction(throw_error), JSON.fromInteger(5000))
    return 0

def target(*args): return main, None
```

It throws an error like this on the JS console:

![RPython Stack Trace](https://github.com/rafi16jan/screenshots/blob/master/rpython-stacktrace.png?raw=true)

This is sometimes enough to determine from which RPython code the JS Error was triggered from

## --source-map
```shell
rpython file.py --source-map
```


This flag turns on the [-g4](https://emscripten.org/docs/tools_reference/emcc.html#emcc-g4) flag for emcc, generating source map for the wasm file and copies the C source to the current directory. With the exact code from above it will prints the stack trace with additional information when clicked:


![RPython Source Map](https://github.com/rafi16jan/screenshots/blob/master/rpython-sourcemap.png?raw=true)


The full stack trace prints all the way to the main RPython function so we can trace the stack from the beginning of program execution. Although it maps the wasm to the C source code, it's only mapped to one file the module_1.c:


![RPython get_errno](https://github.com/rafi16jan/screenshots/blob/master/rpython-geterrorno.png?raw=true)


This code could be from the RPython/PyPy side catching all the error to throws at this get_errorno function.

In the meantime the source map only works on a browser, and it should be loaded with a script tag. When loaded from Module.wasmBinary or on Node.js it doesn't work (although on Node.js maybe --enable-source-maps is still in early stages and source-map-support doesn't work either). On browser, to enable loading from Module.wasmBinary use [wasm-sourcemap](https://www.npmjs.com/package/wasm-sourcemap) to rewrite the sourceMapURL relative to current directory.

In the future however, we will work on the get_errno issue so it will map to the right C file and include a function that overwrite the sourceMapURL on the wasm file just like what wasm-sourcemap do.
