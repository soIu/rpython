//
//  emscripten helper library for JIT-compiling asmjs functions.
//

var LibraryJIT = {

  //  JIT-compile a single function.
  //
  //  The input argument must be the heap address of a string containing
  //  asmjs source code, defining a single function that takes two integer
  //  arguments and returns an integer.
  //
  //  The source will be loaded, compiled, and linked with the main Module.
  //  An opaque integer "function id" will be returned, which can be passed
  //  to jitInvoke to invoke the newly-compiled function.
  //
  jitCompile__deps: ['jitReserve', 'jitRecompile'],
  jitCompile: function(addr) {
    addr = addr|0;
    var id = _jitReserve()|0;
    return _jitRecompile(id, addr);
  },

  //  Reserve a function id for later use.
  //
  //  Rather than creating a new function straight away, this simply allocates
  //  and returns a new function id.  The code can be filled in later by a
  //  call to jitRecompile().
  //
  //  Attempts to invoke a not-yet-defined function will immediately return
  //  zero.
  //
  jitReserve: function() {
    if (!Module._jitCompiledFunctions) {
      // We never allocate a function ID of zero.
      // In theory we could use zero to report compilation failure, but
      // the try-catch may prevent optimization of this function.
      Module._jitCompiledFunctions = [null];
    }
    var id = Module._jitCompiledFunctions.length;
    Module._jitCompiledFunctions[id] = null;
    return id;
  },


  //  Check if a compiled function exists with given id.
  //
  jitExists: function(id) {
    id = id|0;
    if (!Module._jitCompiledFunctions) {
      return 0;
    }
    if (Module._jitCompiledFunctions[id]) {
      return 1;
    }
    return 0;
  },

  //  Re-compile a JIT-compiled function with new source code.
  //
  //  The input arguments are an existing function id and the heap address
  //  of a string containing asmjs source code.
  //
  //  The source will be loaded, compiled, and linked with the main Module.
  //  An opaque integer "function id" will be returned, which can be passed
  //  to jitInvoke to invoke the newly-compiled function.
  //  
  jitRecompile: function(id, addr) {
    id = id|0;
    addr = addr|0;
    // Read js source from the heap, as a C-style string.
    var sourceChars = [];
    var i = addr;
    while (HEAP8[i] != 0) {
      sourceChars.push(String.fromCharCode(HEAP8[i]));
      i++;
    }
    var source = sourceChars.join("");
    // Compile it into an asmjs linkable function, and link it.
    var mkfunc = new Function("return (" + source + ")");
    var stdlib = {
      "Math": Math,
      "Int8Array": Int8Array,
      "Int16Array": Int16Array,
      "Int32Array": Int32Array,
      "Uint8Array": Uint8Array,
      "Uint16Array": Uint16Array,
      "Uint32Array": Uint32Array,
      "Float32Array": Float32Array,
      "Float64Array": Float64Array
    };
    if (typeof Module.tempDoublePtr === "undefined") {
      if (typeof tempDoublePtr === "undefined") {
          throw "NO TEMP DOUBLE PTR";
      }
      Module.tempDoublePtr = tempDoublePtr;
    }
    Module._jitCompiledFunctions[id] = mkfunc()(stdlib, Module, buffer);
    return id
  },

  // Copy a JIT-compiled function to another id.
  //
  jitCopy: function(srcId, dstId) {
    srcId = srcId|0;
    dstId = dstId|0;
    Module._jitCompiledFunctions[dstId] = Module._jitCompiledFunctions[srcId];
  },

  // Invoke a JIT-compiled function.
  //
  // All JIT-compiled functions accept two integer arguments and produce an
  // integer result.  You'll probably want to treat these like a void* to pass
  // around data, but that's up to you.
  //
  // If you pass an id that does not have compiled code associated with it,
  // it will produce a return value of zero.
  //
  jitInvoke: function(id, frame, tladdr, label) {
    id = id|0;
    label = label|0;
    frame = frame|0;
    tladdr = tladdr|0;
    var func = Module._jitCompiledFunctions[id];
    if (func) {
        return func(frame, tladdr, label)|0;
    } else {
        return 0|0;
    }
  },

  // Free a JIT-compiled function.
  //
  jitFree: function(id) {
    id = id|0;
    Module._jitCompiledFunctions[id] = null;
  }
}

mergeInto(LibraryManager.library, LibraryJIT);
