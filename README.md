# RPython WASM
This is a fork from PyPy's RPython in the original bitbucket [repo](https://bitbucket.org/pypy/pypy/src/default/rpython/) compatible to Emscripten's [emcc](https://emscripten.org/docs/tools_reference/emcc.html).

# What is RPython
Graham Jenson writes a nice [article](https://maori.geek.nz/rpython-compiling-python-to-c-for-the-speed-5411d57a5316) about it. Basically, RPython is what powers PyPy as described in its documentation [here](https://rpython.readthedocs.io/en/latest/).

# Installation
RPython it self depends on PyPy (or Python 2.7) and GCC on Linux and Windows (Cygwin), on MacOS it uses clang. Install PyPy from the official [website](https://pypy.org/download.html) ([PyPy still supports 2.7](https://hub.packtpub.com/pypy-supports-python-2-7-even-as-major-python-projects-migrate-to-python-3/)) or alternatively use existing Python 2.7 on your system. Then, install emcc from Emscripten's [SDK](https://emscripten.org/docs/getting_started/downloads.html)

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

# JS API
There are some JS API that can be used to help interfacing to JS, awaiting asynchronous functions, and building nested, multi-hierarchial, multi-types JSON:

- JS Bindings

```python
from javascript import Object, JSON

def main(argv):
    console = Object('console') #Evaluate string and binds corresponding object to Object instance
    console['log'].call('Logging from rpython')
    #__getitem__ and __setitem__ works in RPython but unfortunately __call__ doesn't,
    #so we replace it with call method. Call receives *args of string, can receive stringified json from JSON
    console['log'].call(JSON.fromDict({
      'array': JSON.fromList(['regular string', JSON.fromInt(2)]),
      'another_json': JSON.fromDict({})
    }))
    #JSON exports useful helpers to serialize python primitive types to stringified version that are recognized in JS as their respective types
    json_object = Object('{}') #Plain object
    json_object['is_empty'] = JSON.fromBool(True) #__setitem__
    json_object.log() #Prints {"is_empty": true}
    return 0

def target(*args): return main, None
```

- Async/Await like syntax

```python
from javascript import Object, asynchronous

@asynchronous
def get_page(url):
    require = Object('require')
    fetch = require.call('node-fetch') #Obviously, install node-fetch first on npm
    response = fetch.call(url).wait()
    text = response['text'].call().wait()
    return text

@asynchronous
def get_pages():
    google, youtube = get_page('https://google.com').wait(), get_page('https://youtube.com').wait()
    print(google)
    print(youtube)

def main(argv):
    get_pages()
    return 0

def target(*args): return main, None 
```

# Asynchronous Execution

When we decide to implement async/await the easiest option is to use [Asyncify](https://emscripten.org/docs/porting/asyncify.html). But Asyncify come with few caveats, overhead both in performance and file size, and the worst, [reentrancy](https://emscripten.org/docs/porting/asyncify.html). Asyncify doesn't support reentrancy and that means if RPython is awaiting a task blocking the execution stack, and another function is called (either by user event like click or a timer, or another asynchronous task, etc) it will throw an error. We can overcome this by awaiting RPython until it is done executing active task and immediately call it but **that will results in synchronous execution (no parallel) and that is not what async/await is all about.**

Another solution is implementing an event loop, awaiting in a pseudo while loop for event listener calls but that is complex and non-intiuitive. What we do alternatively is transforming the function to a series of callbacks and yields at compile time (RPython supports evaluating and modifying Python objects at compile time), consisting of variables caching and re-assigning on the next tick and resolves the caller when the function is done executing (similar to https://babeljs.io/docs/en/babel-plugin-transform-async-to-generator). This is all done with the asynchronous decorator.

# Limitation

Because RPython is a subset of Python. You can't code in RPython just like you do on Python. Variables, functions, dictionaries, and lists can only have one type. The most dynamic type in RPython is class, class can nearly have any types on it's attributes. Just don't getattr them and assign them to a same variable, use isinstance in an if else if you insist to use getattr. The second most dynamic type in RPython is tuples, but unlike in Python tuples must be in a fixed length and you can't concat multiple tuples into one tuple. Here's a few guide to code in RPython:

- https://rpython.readthedocs.io/
- https://maori.geek.nz/rpython-is-not-a-language-an-introduction-to-the-rpython-language-9f48c7a3047
- https://mesapy.org/rpython-by-example/
- https://refi64.com/posts/the-magic-of-rpython.html

# Compatibility
There's two things that are commented out from the RPython's source code:

- ASM call
- Pthread

Emscripten doesn't support ASM call on the resulting binary as it would make it not portable. And for now Pthread is also commented because major browsers have not implement it yet (https://emscripten.org/docs/porting/pthreads.html).
