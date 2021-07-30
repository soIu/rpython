import inspect
import ast
import os
from rpython.javascript import json
from rpython.rtyper.lltypesystem import lltype, rffi
from rpython.translator.tool.cbuild import ExternalCompilationInfo
from rpython.rlib.entrypoint import entrypoint_highlevel
from rpython.rlib.rstring import replace

JSON = json

decompile = None
tempfile = None
imp = None
try:
    import ast_decompiler
    import tempfile
    import random
    import imp
    def decompile(code, original, lineno):
        if original.endswith('.pyc'): original = original[0:-1]
        object_id = hex(id(code)) + repr(random.random())[2:]
        code = ast_decompiler.decompile(code)
        fd, path = tempfile.mkstemp(dir=os.getenv('PYPY_USESSION_DIR'))
        file = open(path, 'w')
        file.write('\n'.join([line + ' #File ' + original + ':' + str(lineno) for line in code.splitlines()]))
        file.seek(0)
        file.close()
        #name = path.split('/')[-1]
        module = imp.load_source(object_id, path)
        return module
except:
    pass

info = ExternalCompilationInfo(includes=['emscripten.h'])
run_script_string = rffi.llexternal('emscripten_run_script_string', [rffi.CCHARP], rffi.CCHARP, compilation_info=info)
run_script = rffi.llexternal('emscripten_run_script', [rffi.CCHARP], lltype.Void, compilation_info=info)

em_js = """
#include <emscripten.h>

/*EM_JS(const char*, run_safe_string, (const char* str), {
  var global = typeof rpyGlobalArg !== "undefined" ? rpyGlobalArg : this;
});*/

EM_JS(const char*, run_safe_json, (const char* json, const char* variable), {
  var global = typeof rpyGlobalArg !== "undefined" ? rpyGlobalArg : this;
  variable = UTF8ToString(variable);
  var object = JSON.parse(UTF8ToString(json));
  object = global.deserialize_rpython_json(object);
  try {
    global[variable] = object;
  }
  catch (error) {
    console.error('Trying to set variable ' + variable);
    console.error(error);
    throw error;
  }
  var type;
  if (object === null) type = 'null';
  else if (Array.isArray(object)) type = 'array';
  else type = typeof object;
  var lengthBytes = lengthBytesUTF8(type) + 1;
  var stringOnWasmHeap = _malloc(lengthBytes);
  stringToUTF8(type, stringOnWasmHeap, lengthBytes);
  return stringOnWasmHeap;
});

EM_JS(const char*, run_safe_get, (const char* variable, const char* key, const char* new_variable), {
  var global = typeof rpyGlobalArg !== "undefined" ? rpyGlobalArg : this;
  key = UTF8ToString(key);
  variable = UTF8ToString(variable);
  new_variable = UTF8ToString(new_variable);
  var object;
  try {
    object = global[variable][key];
  }
  catch (error) {
    console.error('Trying to get variable ' + variable + ' and ' + key);
    console.error(error);
    throw error;
  }
  if (typeof object === 'function' && (!object.prototype || Object.getOwnPropertyNames(object.prototype).length === 1)) object = object.bind(global[variable]);
  global[new_variable] = object;
  var type;
  if (object === null) type = 'null';
  else if (Array.isArray(object)) type = 'array';
  else type = typeof object;
  var lengthBytes = lengthBytesUTF8(type) + 1;
  var stringOnWasmHeap = _malloc(lengthBytes);
  stringToUTF8(type, stringOnWasmHeap, lengthBytes);
  return stringOnWasmHeap;
});

EM_JS(void, run_safe_set, (const char* variable, const char* key, const char* value), {
  var global = typeof rpyGlobalArg !== "undefined" ? rpyGlobalArg : this;
  key = UTF8ToString(key);
  variable = UTF8ToString(variable);
  value = global.deserialize_rpython_json(JSON.parse(UTF8ToString(value)));
  try {
    global[variable][key] = value;
  }
  catch (error) {
    console.error('Trying to set variable ' + variable + ' and ' + key);
    console.error(error);
    throw error;
  }
});

EM_JS(void, run_safe_del, (const char* variable, const char* key), {
  var global = typeof rpyGlobalArg !== "undefined" ? rpyGlobalArg : this;
  key = UTF8ToString(key);
  variable = UTF8ToString(variable);
  try {
    delete global[variable][key];
  }
  catch (error) {
    console.error('Trying to delete variable ' + variable + ' and ' + key);
    console.error(error);
    throw error;
  }
});

EM_JS(const char*, run_safe_call, (const char* variable, const char* args, const char* new_variable), {
  var global = typeof rpyGlobalArg !== "undefined" ? rpyGlobalArg : this;
  variable = UTF8ToString(variable);
  args = JSON.parse(UTF8ToString(args));
  new_variable = UTF8ToString(new_variable);
  deserialize_rpython_json(args);
  var call;
  try {
    call = global[variable];
  }
  catch (error) {
    console.error('Trying to get variable ' + variable);
    console.error(error);
    throw error;
  }
  var object;
  try {
    object = call(...args);
  }
  catch (error) {
   console.error('Trying to call variable ' + variable);
   console.error(error);
   throw error;
  }
  global[new_variable] = object;
  var type;
  if (object === null) type = 'null';
  else if (Array.isArray(object)) type = 'array';
  else type = typeof object;
  var lengthBytes = lengthBytesUTF8(type) + 1;
  var stringOnWasmHeap = _malloc(lengthBytes);
  stringToUTF8(type, stringOnWasmHeap, lengthBytes);
  return stringOnWasmHeap;
});

EM_JS(const char*, run_safe_new, (const char* variable, const char* args, const char* new_variable), {
  var global = typeof rpyGlobalArg !== "undefined" ? rpyGlobalArg : this;
  variable = UTF8ToString(variable);
  args = JSON.parse(UTF8ToString(args));
  new_variable = UTF8ToString(new_variable);
  deserialize_rpython_json(args);
  var constructor;
  try {
    constructor = global[variable];
  }
  catch (error) {
    console.error('Trying to get variable ' + variable);
    console.error(error);
    throw error;
  }
  var object;
  try {
    object = new constructor(...args);
  }
  catch (error) {
   console.error('Trying to instantiate variable ' + variable);
   console.error(error);
   throw error;
  }
  global[new_variable] = object;
  var type;
  if (object === null) type = 'null';
  else if (Array.isArray(object)) type = 'array';
  else type = typeof object;
  var lengthBytes = lengthBytesUTF8(type) + 1;
  var stringOnWasmHeap = _malloc(lengthBytes);
  stringToUTF8(type, stringOnWasmHeap, lengthBytes);
  return stringOnWasmHeap;
});

EM_JS(void, run_safe_promise, (const char* parent_promise_id, const char* promise_id, const char* variables), {
  var global = typeof rpyGlobalArg !== "undefined" ? rpyGlobalArg : this;
  var args = [UTF8ToString(parent_promise_id), UTF8ToString(promise_id)]; //.map(function (string) {return allocate.length === 2 ? allocate(intArrayFromString(string), ALLOC_NORMAL) : allocate(intArrayFromString(string), 'i8', ALLOC_NORMAL)});
  variables = JSON.parse(UTF8ToString(variables));
  Promise.all(variables.map(async function (variable) {
    var promise;
    try {
      promise = global[variable];
    }
    catch (error) {
      console.error('Trying to get variable ' + variable);
      console.error(error);
      throw error;
    }
    var object = await promise;
    if (object && object.then) object.rpython_resolved = true;
    global[variable] = object;
  })).then(function () {
    //Module.asm.onresolve(...args);
    Module.ccall('onresolve', 'null', ['string', 'string'], args);
  }) //.catch(function (error) {console.error(error) /*|| throw error*/});
});

EM_JS(const char*, run_safe_type_update, (const char* variable), {
  var global = typeof rpyGlobalArg !== "undefined" ? rpyGlobalArg : this;
  variable = UTF8ToString(variable);
  var object;
  try {
    object = global[variable];
  }
  catch (error) {
    console.error('Trying to get variable ' + variable);
    console.error(error);
    throw error;
  }
  var type;
  if (object === null) type = 'null';
  else if (Array.isArray(object)) type = 'array';
  else type = typeof object;
  var lengthBytes = lengthBytesUTF8(type) + 1;
  var stringOnWasmHeap = _malloc(lengthBytes);
  stringToUTF8(type, stringOnWasmHeap, lengthBytes);
  return stringOnWasmHeap;
});

EM_JS(const char*, create_function, (const char* id, const char* new_variable, const char* function_info), {
  var global = typeof rpyGlobalArg !== "undefined" ? rpyGlobalArg : this;
  var index = parseInt(UTF8ToString(id));
  new_variable = UTF8ToString(new_variable);
  function_info = UTF8ToString(function_info);
  var new_object = {};
  new_object[function_info] = (function (...args) {
    if (!global.rpyfunction_call_args) global.rpyfunction_call_args = {};
    var variable = 'rpyfunction_call_args';
    global.rpyfunction_call_args[index] = args;
    args = [variable, index.toString()]; //.map(function (string) {return allocate.length === 2 ? allocate(intArrayFromString(string), ALLOC_NORMAL) : allocate(intArrayFromString(string), 'i8', ALLOC_NORMAL)});
    //Module.asm.onfunctioncall(...args);
    Module.ccall('onfunctioncall', 'null', ['string', 'string'], args);
    delete global.rpyfunction_call_args[index];
    var result = global[global['rpyfunction_call_' + index]];
    delete global['rpyfunction_call_' + index];
    return result;
  });
  //object[function_info].rpython_info = function_info;
  var object = (function (...args) {
    try {
      return new_object[function_info](...args);
    }
    catch (error) {
      console.error('Trying to call ' + function_info);
      throw error;
    }
  });
  global[new_variable] = object;
  var type;
  if (object === null) type = 'null';
  else if (Array.isArray(object)) type = 'array';
  else type = typeof object;
  var lengthBytes = lengthBytesUTF8(type) + 1;
  var stringOnWasmHeap = _malloc(lengthBytes);
  stringToUTF8(type, stringOnWasmHeap, lengthBytes);
  return stringOnWasmHeap;
});

EM_JS(const char*, create_method, (const char* id, const char* method_id, const char* new_variable), {
  var global = typeof rpyGlobalArg !== "undefined" ? rpyGlobalArg : this;
  var index = parseInt(UTF8ToString(id));
  new_variable = UTF8ToString(new_variable);
  method_id = UTF8ToString(method_id);
  var object = (function (...args) {
    if (!global.rpymethod_call_args) global.rpymethod_call_args = {};
    var variable = 'rpymethod_call_args';
    global.rpymethod_call_args[index] = args;
    args = [variable, index.toString()]; //.map(function (string) {return allocate.length === 2 ? allocate(intArrayFromString(string), ALLOC_NORMAL) : allocate(intArrayFromString(string), 'i8', ALLOC_NORMAL)});
    //Module.asm['onmethodcall' + method_id](...args);
    Module.ccall('onmethodcall' + method_id, 'null', ['string', 'string'], args);
    delete global.rpymethod_call_args[index];
    var result = global[global['rpymethod_call_' + index]];
    delete global['rpymethod_call_' + index];
    return result;
  });
  global[new_variable] = object;
  var type;
  if (object === null) type = 'null';
  else if (Array.isArray(object)) type = 'array';
  else type = typeof object;
  var lengthBytes = lengthBytesUTF8(type) + 1;
  var stringOnWasmHeap = _malloc(lengthBytes);
  stringToUTF8(type, stringOnWasmHeap, lengthBytes);
  return stringOnWasmHeap;
});

EM_JS(const char*, create_js_closure, (const char* func, const char* args, const char* new_variable), {
  var global = typeof rpyGlobalArg !== "undefined" ? rpyGlobalArg : this;
  new_variable = UTF8ToString(new_variable);
  args = JSON.parse(UTF8ToString(args));
  global.deserialize_rpython_json(args);
  func = global.deserialize_rpython_json(UTF8ToString(func));
  var object = (function (...new_args) {
    return func(...args, ...new_args);
  });
  global[new_variable] = object;
  var type;
  if (object === null) type = 'null';
  else if (Array.isArray(object)) type = 'array';
  else type = typeof object;
  var lengthBytes = lengthBytesUTF8(type) + 1;
  var stringOnWasmHeap = _malloc(lengthBytes);
  stringToUTF8(type, stringOnWasmHeap, lengthBytes);
  return stringOnWasmHeap;
});

EM_JS(const char*, get_string, (const char* variable), {
  var global = typeof rpyGlobalArg !== "undefined" ? rpyGlobalArg : this;
  variable = UTF8ToString(variable);
  var string = global[variable];
  var result;
  if (typeof string === 'string') result = string;
  else if (string && string.toString) result = string.toString();
  else result = String(string);
  var lengthBytes = lengthBytesUTF8(result) + 1;
  var stringOnWasmHeap = _malloc(lengthBytes);
  stringToUTF8(result, stringOnWasmHeap, lengthBytes);
  return stringOnWasmHeap;
});

EM_JS(const char*, get_integer, (const char* variable), {
  var global = typeof rpyGlobalArg !== "undefined" ? rpyGlobalArg : this;
  variable = UTF8ToString(variable);
  var integer = parseInt(global[variable]);
  if (isNaN(integer)) {
    throw new Error(global[variable] + ' is not a number');
  }
  var result = integer.toString();
  var lengthBytes = lengthBytesUTF8(result) + 1;
  var stringOnWasmHeap = _malloc(lengthBytes);
  stringToUTF8(result, stringOnWasmHeap, lengthBytes);
  return stringOnWasmHeap;
});

EM_JS(const char*, get_float, (const char* variable), {
  var global = typeof rpyGlobalArg !== "undefined" ? rpyGlobalArg : this;
  variable = UTF8ToString(variable);
  var float = parseFloat(global[variable]);
  if (isNaN(float)) {
    throw new Error(global[variable] + ' is not a number');
  }
  var result = float.toString();
  var lengthBytes = lengthBytesUTF8(result) + 1;
  var stringOnWasmHeap = _malloc(lengthBytes);
  stringToUTF8(result, stringOnWasmHeap, lengthBytes);
  return stringOnWasmHeap;
});

EM_JS(const char*, get_boolean, (const char* variable), {
  var global = typeof rpyGlobalArg !== "undefined" ? rpyGlobalArg : this;
  var result = global[variable];
  if (typeof result !== 'boolean') result = !!result;
  result = JSON.stringify(result);
  var lengthBytes = lengthBytesUTF8(result) + 1;
  var stringOnWasmHeap = _malloc(lengthBytes);
  stringToUTF8(result, stringOnWasmHeap, lengthBytes);
  return stringOnWasmHeap;
});

EM_JS(const char*, run_unsafe_code, (const char* code), {
  code = UTF8ToString(code);
  var global = typeof rpyGlobalArg !== "undefined" ? rpyGlobalArg : this;
  if (!Module.wasmMemory) Module.wasmMemory = wasmMemory;
  var eval_function;
  try {
    eval_function = eval(code);
  }
  catch (error) {
    console.error('Trying to build eval code: ' + code);
    throw error;
  }
  var result;
  try {
    result = String(eval_function(Module, global));
  }
  catch (error) {
    console.error('Trying to execute eval code: ' + code);
    throw error;
  }
  var lengthBytes = lengthBytesUTF8(result) + 1;
  var stringOnWasmHeap = _malloc(lengthBytes);
  stringToUTF8(result, stringOnWasmHeap, lengthBytes);
  return stringOnWasmHeap;
});

"""
def rffi_1(function, void=False):
    def wrapper(arg1, skip_gc=False):
        if not skip_gc and globals.collector_id is None: run_garbage_collector()
        pointer = function(rffi.str2charp(arg1))
        if void: return
        result = rffi.charp2str(pointer)
        lltype.free(pointer, flavor='raw')
        return result
    return wrapper

def rffi_2(function, void=False):
    def wrapper(arg1, arg2, skip_gc=False):
        if not skip_gc and globals.collector_id is None: run_garbage_collector()
        pointer = function(rffi.str2charp(arg1), rffi.str2charp(arg2))
        if void: return
        result = rffi.charp2str(pointer)
        lltype.free(pointer, flavor='raw')
        return result
    return wrapper

def rffi_3(function, void=False):
    def wrapper(arg1, arg2, arg3, skip_gc=False):
        if not skip_gc and globals.collector_id is None: run_garbage_collector()
        pointer = function(rffi.str2charp(arg1), rffi.str2charp(arg2), rffi.str2charp(arg3))
        if void: return
        result = rffi.charp2str(pointer)
        lltype.free(pointer, flavor='raw')
        return result
    return wrapper

info = ExternalCompilationInfo(separate_module_sources=[em_js], includes=['src/em_js_api.h'])
#run_safe_string = rffi.llexternal('run_safe_string', [rffi.CCHARP], rffi.CCHARP, compilation_info=info)
run_safe_json = rffi_2(rffi.llexternal('run_safe_json', [rffi.CCHARP, rffi.CCHARP], rffi.CCHARP, compilation_info=info))
run_safe_get = rffi_3(rffi.llexternal('run_safe_get', [rffi.CCHARP, rffi.CCHARP, rffi.CCHARP], rffi.CCHARP, compilation_info=info))
run_safe_set = rffi_3(rffi.llexternal('run_safe_set', [rffi.CCHARP, rffi.CCHARP, rffi.CCHARP], lltype.Void, compilation_info=info), void=True)
run_safe_del = rffi_2(rffi.llexternal('run_safe_del', [rffi.CCHARP, rffi.CCHARP], lltype.Void, compilation_info=info), void=True)
run_safe_call = rffi_3(rffi.llexternal('run_safe_call', [rffi.CCHARP, rffi.CCHARP, rffi.CCHARP], rffi.CCHARP, compilation_info=info))
run_safe_new = rffi_3(rffi.llexternal('run_safe_new', [rffi.CCHARP, rffi.CCHARP, rffi.CCHARP], rffi.CCHARP, compilation_info=info))
run_safe_promise = rffi_3(rffi.llexternal('run_safe_promise', [rffi.CCHARP, rffi.CCHARP, rffi.CCHARP], lltype.Void, compilation_info=info), void=True)
run_safe_type_update = rffi_1(rffi.llexternal('run_safe_type_update', [rffi.CCHARP], rffi.CCHARP, compilation_info=info))

run_unsafe_code = rffi_1(rffi.llexternal('run_unsafe_code', [rffi.CCHARP], rffi.CCHARP, compilation_info=info))

create_function = rffi_3(rffi.llexternal('create_function', [rffi.CCHARP, rffi.CCHARP, rffi.CCHARP], rffi.CCHARP, compilation_info=info))
create_method = rffi_3(rffi.llexternal('create_method', [rffi.CCHARP, rffi.CCHARP, rffi.CCHARP], rffi.CCHARP, compilation_info=info))
create_js_closure = rffi_3(rffi.llexternal('create_js_closure', [rffi.CCHARP, rffi.CCHARP, rffi.CCHARP], rffi.CCHARP, compilation_info=info))

get_string = rffi_1(rffi.llexternal('get_string', [rffi.CCHARP], rffi.CCHARP, compilation_info=info))
get_integer = rffi_1(rffi.llexternal('get_integer', [rffi.CCHARP], rffi.CCHARP, compilation_info=info))
get_float = rffi_1(rffi.llexternal('get_float', [rffi.CCHARP], rffi.CCHARP, compilation_info=info))
get_boolean = rffi_1(rffi.llexternal('get_boolean', [rffi.CCHARP], rffi.CCHARP, compilation_info=info))

def run_javascript(code, returns=False, skip_gc=False):
    if not skip_gc and globals.collector_id is None: run_garbage_collector()
    code = '(function(Module, global) {' + code + '})'
    return run_unsafe_code(code)
    """if returns:
       pointer = run_script_string(rffi.str2charp(code))
       result = rffi.charp2str(pointer)
       lltype.free(pointer, flavor='raw')
       return result
    run_script(rffi.str2charp(code))
    return None"""

def resolve_next_event(parent_id, child_id): return

#Extra JSON Types for interfacing, it is recommended to use JSON.from* APIs instead of creating fat pointers from these Object.from* APIs. This APIs is used to mix primitive types to be used on Javascript-side

def rpyobject(function):
    def wrapper(value):
        return Object(function(value), safe_json=True)
    return wrapper

@rpyobject
def toString(value):
    #if value is None: return 'null'
    return json.fromString(value)

toStr = toString

@rpyobject
def toInt(value):
    #return '%s' % (value)
    return json.fromInteger(value)

toInteger = toInt

@rpyobject
def toFloat(value):
    #return repr(value)
    return json.fromFloat(value)

@rpyobject
def toBoolean(value):
    #return 'true' if value == True else 'false' if value == False else 'null'
    return json.fromBoolean(value)

toBool = toBoolean

@rpyobject
def toList(value):
    return json.fromList(value)

@rpyobject
def toDict(value):
    return json.fromDict(value)

functions = {}

function_template = '''
(function (...args) {
  if (!global.rpyfunction_call_args) global.rpyfunction_call_args = {};
  var index = %s;
  var variable = 'global.rpyfunction_call_args[' + index + ']';
  global.rpyfunction_call_args[index] = args;
  args = [variable, index.toString()]; //.map(function (string) {return allocate.length === 2 ? allocate(intArrayFromString(string), ALLOC_NORMAL) : allocate(intArrayFromString(string), 'i8', ALLOC_NORMAL)});
  //Module.asm.onfunctioncall(...args);
  Module.ccall('onnfunctioncall', 'null', ['string', 'string'], args);
  delete global.rpyfunction_call_args[index];
  var result = global[global['rpyfunction_call_' + index]];
  delete global['rpyfunction_call_' + index];
  return result;
});
'''

def toFunction(function, keep=False):
    #if globals.functions_cache is None:
    #   globals.functions_cache = {}
    #if function in globals.functions_cache: return globals.functions_cache[function]
    #index = globals.functions
    #functions[index] = function
    #functions.append(function)
    #globals.functions += 1
    object =  Object(str(decorated_functions[function]), safe_function=True, safe_function_info='Function %s in module %s' % (function.function_name, function.function_module)) #function_template % (index))
    if keep: object.keep()
    #globals.functions_cache[function] = object
    return object

def fromFunction(function=None, keep=False):
    if function is None: return 'RPYJSON:null:RPYJSON'
    return toFunction(function, keep=keep).toRef()

@entrypoint_highlevel(key='main', c_name='onfunctioncall', argtypes=[rffi.CCHARP, rffi.CCHARP])
def onfunctioncall(*arguments):
    pointers = list(arguments)
    variable, function_id = [rffi.charp2str(pointer) for pointer in pointers]
    for pointer in pointers: lltype.free(pointer, flavor='raw')
    args = Object.get(variable, function_id).toArray()
    #id = int(function_id)
    #return
    function = functions[int(function_id)]
    result = function.function[0](args=[arg for arg in args]) #if function in decorated_functions else function([arg for arg in args])
    run_safe_set('global', 'rpyfunction_call_' + function_id, ('"%s"' % result.variable) if result is not None else 'null', skip_gc=True)
    #run_javascript('global.rpyfunction_call_' + function_id + ((' = "%s"' % result.variable) if result is not None else ' = null'), skip_gc=True)
    #globals.collector_id = None

decorated_functions = {}
#functions_id = {}

class Function:
    function = (None,)
    cache = {'count': 0}

    def __init__(self, function):
        self.cache['count'] += 1
        count = self.cache['count']
        self.function = (function,)
        decorated_functions[self] = count
        functions[count] = self
        self.function_name = function.__name__
        self.function_module = function.__module__

    #TODO make this callable without Object.fromFunction

def javascript_function(function=None, asynchronous=False, name=None, count=None): #Spread list of Object to each of the argument
    if not function and asynchronous:
       def wrapper(function):
           return javascript_function(function=asynchronous_function(function), name=function.__name__, count=function.func_code.co_argcount, asynchronous=True)
       return wrapper
    if function is None: raise Exception("Where is the function?")
    if name is None: name = function.__name__
    if count is None: count = function.func_code.co_argcount
    args = ', '.join('args[%s]' % index for index in range(count))
    arg_names = ', '.join('rpyarg%s=None' % (index + 1) for index in range(count))
    namespace = {'rpython_decorated_function': function, 'RPYObject': Object}
    indent = '\n' + (' ' * 4)
    #code = 'def ' + name + '(args=None' + (', ' if count else "") + arg_names + '):
    code = 'def ' + name + '(' + arg_names + (', ' if count else "") + 'args=None):'
    def return_if_synchronous(): return 'return ' if not asynchronous else ""
    if count:
       code += indent + 'if args is None: ' + return_if_synchronous() + 'rpython_decorated_function(' + ', '.join('rpyarg%s or RPYObject("null")' % (index + 1) for index in range(count)) + ')'
       if asynchronous: code += indent + 'if args is None: return'
    code += indent + 'if args is not None and len(args) < ' + str(count) + ': ' + return_if_synchronous() + 'rpython_decorated_function(' + ', '.join('args[%s] if len(args) >= %s else RPYObject("null")' % (index, index + 1) for index in range(count))  + ')'
    if asynchronous:
       code += indent + 'if args is not None and len(args) < ' + str(count) + ': return'
    code += indent + 'assert args is not None and len(args) >= ' + str(count)
    code += indent + return_if_synchronous() + 'rpython_decorated_function(' + args + ')'
    exec(code, namespace)
    function = namespace[name]
    #decorated_functions.append(function)
    #return function
    return Function(function)

args = javascript_function

function = args

snapshot = open(os.path.join(os.path.dirname(os.path.realpath(__file__)), 'utils/snapshot_memory.js'), 'r').read()

@Function
def garbage_collector(args):
    if globals.collector_id is None: return
    garbage = globals.garbage
    for variable in garbage: garbage[variable].free()
    if globals.pendingAsync is None or not len(globals.pendingAsync):
       if globals.snapshot is None:
          globals.snapshot = Object(snapshot).keep()
       globals.setTimeout(globals.snapshot.toRef(), JSON.fromInteger(0))
    globals.collector_id = None


def run_garbage_collector():
    globals.collector_id = ''
    if globals.garbage is None:
       globals.garbage = {}
    #if globals.collector_function is None:
    globals.collector_function = json.fromFunction(garbage_collector)
    if globals.setTimeout is None:
       globals.setTimeout = Object('setTimeout').keep().toFunction()
    setTimeout = globals.setTimeout
    timeout = setTimeout(globals.collector_function, json.fromInteger(0))
    globals.collector_id = timeout.toString()

method_template = '''
(function (...args) {
  if (!global.rpymethod_call_args) global.rpymethod_call_args = {};
  var index = %s;
  var variable = 'global.rpymethod_call_args[' + index + ']';
  global.rpymethod_call_args[index] = args;
  args = [variable, index.toString()]; //.map(function (string) {return allocate.length === 2 ? allocate(intArrayFromString(string), ALLOC_NORMAL) : allocate(intArrayFromString(string), 'i8', ALLOC_NORMAL)});
  Module.ccall('onmethodcall%s', 'null', ['string', 'string'], args);
  delete global.rpymethod_call_args[index];
  var result = global[global['rpymethod_call_' + index]];
  delete global['rpymethod_call_' + index];
  return result;
});
'''

def fromMethod():
    methods = {}
    globals.method_callers += 1
    method_callers = globals.method_callers
    @entrypoint_highlevel(key='main', c_name='onmethodcall' + str(globals.method_callers), argtypes=[rffi.CCHARP, rffi.CCHARP])
    def onmethodcall(*arguments):
        pointers = list(arguments)
        variable, method_id = [rffi.charp2str(pointer) for pointer in pointers]
        for pointer in pointers: lltype.free(pointer, flavor='raw')
        args = Object.get(variable, method_id).toArray()
        method = methods[int(method_id)]
        result = method(args=[arg for arg in args])
        run_safe_set('global', 'rpymethod_call_' + method_id, ('"%s"' % result.variable) if result is not None else 'null')
        #run_javascript('global.rpymethod_call_' + method_id + ((' = "%s"' % result.variable) if result is not None else ' = null'))
    onmethodcall.__name__ = 'onmethodcall' + str(globals.method_callers)

    #class Cache:
    #    methods = None

    #cache = Cache()
    def Method(method, keep=False):
        if method is None: return 'RPYJSON:null:RPYJSON'
        #if cache.methods is None:
        #   cache.methods = {}
        #if method in cache.methods: return cache.methods[method]
        #globals.methods += 1
        methods[method_callers] = method
        object = Object(str(method_callers), safe_method=method_callers) #method_template % (globals.methods, globals.method_callers))
        if keep: object.keep()
        #cache.methods[method] = object.toRef()
        return object.toRef() #cache.methods[method]
    return Method

def Method(count=0):
    if not count: return fromMethod()
    return (fromMethod() for index in range(count))

def method(function, asynchronous=False, name=None, count=None): #Spread list of Object to each of the argument
    if asynchronous:
       def wrapper(function):
           return method(asynchronous_function(function), name=function.__name__, count=function.func_code.co_argcount)
       return wrapper
    if name is None: name = function.__name__
    if count is None: count = function.func_code.co_argcount
    count -= 1
    args = ', '.join(['self'] + ['args[%s]' % index for index in range(count)])
    arg_names = ', '.join(['self'] + ['rpyarg%s=None' % (index + 1) for index in range(count)] + ['args=None'])
    namespace = {'rpython_decorated_function': function, 'RPYObject': Object}
    indent = '\n' + (' ' * 4)
    code = 'def ' + name + '(' + arg_names + '):'
    #if count: 
    code += indent + 'if args is None: return rpython_decorated_function(' + ', '.join(['self'] + ['rpyarg%s or RPYObject("null")' % (index + 1) for index in range(count)]) + ')'
    code += indent + 'if args is not None and len(args) < ' + str(count) + ': return rpython_decorated_function(' + ', '.join(['self'] + ['args[%s] if len(args) >= %s else RPYObject("null")' % (index, index + 1) for index in range(count)])  + ')'
    code += indent + 'assert args is not None and len(args) >= ' + str(count)
    code += indent + 'return rpython_decorated_function(' + args + ')'
    exec(code, namespace)
    function = namespace[name]
    #decorated_methods.append(function)
    return function

class String:

    def __init__(self, value):
        self.value = value

    def replace(self, search, substitute):
        self.value = replace(self.value, search, substitute)
        return self

    def format(self, *strings):
        index = 0
        for string in list(strings):
            self.value = replace(self.value, '{%s}' % index, string)
            index += 1
        return self

class Globals:

    promises = 0
    objects = 0
    functions = 0
    functions_cache = None
    methods = 0
    methods_cache = None
    method_callers = 0
    garbage = None
    collector_id = None
    collector_function = None
    setTimeout = None
    snapshot = None
    pendingAsync = None

    def __init__(self):
        self.resolve_next_event = resolve_next_event

globals = Globals()

class Array:

    def __init__(self, object):
        self.object = object

    def __iter__(self):
        object = self.object['length']
        if object.type != 'number': return iter([])
        length = object.toInteger()
        objects = []
        for index in range(length):
            objects += [self.object[str(index)]]
        return iter(objects)

class Error:

    def __init__(self, message):
        run_script(rffi.str2charp('throw new Error(`%s`)' % message))

def create_closure(function, *objects):
    object = Object(JSON.fromFunction(function), safe_closure_args=[object.toRef() for object in list(objects)])
    return object

class Object:

    id = -1
    resolved = True
    keep_from_gc = False

    fromString = staticmethod(toString)
    fromStr = staticmethod(toStr)
    fromInteger = staticmethod(toInteger)
    fromInt = staticmethod(toInt)
    fromFloat = staticmethod(toFloat)
    fromBoolean = staticmethod(toBoolean)
    fromBool = staticmethod(toBool)
    fromList = staticmethod(toList)
    fromDict = staticmethod(toDict)
    fromFunction = staticmethod(toFunction)
    createClosure = staticmethod(create_closure)

    def __init__(self, code, bind='', prestart='', safe_json=False, safe_get="", safe_call="", safe_new=str(), safe_function=False, safe_function_info=str(), safe_method=0, safe_closure_args=None):
        self.id = globals.objects
        globals.objects += 1
        self.code = code
        self.variable = 'rpython_object_' + str(self.id)
        if safe_json:
           self.type = run_safe_json(json.parse_rpy_json(code), self.variable)
        elif safe_get:
           self.type = run_safe_get(code, safe_get, self.variable)
        elif safe_call:
           self.type = run_safe_call(safe_call, code, self.variable)
        elif safe_new:
           self.type = run_safe_new(safe_new, code, self.variable)
        elif safe_function:
           self.type = create_function(code, self.variable, safe_function_info)
        elif safe_method:
           self.type = create_method(code, str(safe_method), self.variable)
        elif safe_closure_args is not None:
           self.type = create_js_closure(code, json.parse_rpy_json(json.fromList(safe_closure_args)), self.variable)
        else:
           self.type = run_javascript(String("""
           {3}
           global.{0} = {1}
           var object = global.{0};
           {2}
           global.{0} = object;
           if (global.{0} === null) return 'null';
           if (Array.isArray(global.{0})) return 'array';
           return typeof global.{0};
           """).format(self.variable, code, bind, prestart).value, returns=True)
        globals.garbage[self.variable] = self

    @staticmethod
    def get(*args):
        keys = list(args)
        object = Object('global', safe_get=keys.pop(0))
        for key in keys:
            object = object[key]
        return object

    def new(self, *args):
        json_args = '[' + ', '.join([json.parse_rpy_json(arg) for arg in list(args)]) + ']'
        return Object(json_args, safe_new=self.variable)

    def call(self, *args):
        #if not args: return Object(String('call()').replace('{0}', self.variable).value, prestart='var call = global.' + self.variable)
        json_args = '[' + ', '.join([json.parse_rpy_json(arg) for arg in list(args)]) + ']'
        return Object(json_args, safe_call=self.variable)
        #return Object(String('call(...[{1}])').replace('{0}', self.variable).replace('{1}', json_args).value, prestart='var call = global.' + self.variable)

    def free(self):
        if self.keep_from_gc: return
        #run_javascript('delete global.' + self.variable)
        run_safe_del('global', self.variable)
        del globals.garbage[self.variable]

    def keep(self):
        self.keep_from_gc = True
        return self

    def release(self):
        self.keep_from_gc = False
        return self

    def __iter__(self):
        keys = Object('Object.keys(global.%s)' % (self.variable))
        length = keys['length'].toInteger()
        objects = []
        for index in range(length):
            objects += [keys[str(index)].toString()]
        return iter(objects)

    def __getitem__(self, key):
        return Object(self.variable, safe_get=key) #, bind="object = typeof object != 'function' || object.prototype ? object : object.bind(global." + self.variable + ')')

    def __setitem__(self, key, value):
        run_safe_set(self.variable, key, json.parse_rpy_json(value))
        #run_javascript(('global.%s["%s"] = ' % (self.variable, key)) + json.parse_rpy_json(value))
        return

    def toString(self):
        return get_string(self.variable)
        #if self.type == 'string': return run_javascript('return global.%s' % self.variable, returns=True)
        #return run_javascript(String('return global.{0} && global.{0}.toString ? global.{0}.toString() : String(global.{0})').format(self.variable).value, returns=True)

    def toStr(self): return self.toString()

    def toInteger(self):
        return int(get_integer(self.variable))
        #integer = 0
        #if self.type == 'number': integer = int(run_javascript('return JSON.stringify(global.%s)' % self.variable, returns=True))
        #else: integer = int(run_javascript(String('var integer = parseInt(global.{0}); if (!isNaN(integer)) return integer; console.log(global.{0}); throw new Error("Not a number")').format(self.variable).value, returns=True))
        #return integer

    def toInt(self): return self.toInteger()

    def toFloat(self):
        return float(get_float(self.variable))
        #number = 0
        #if self.type == 'number': number = float(run_javascript('return JSON.stringify(global.%s)' % self.variable, returns=True))
        #else: number = float(run_javascript(String('var float = parseFloat(global.{0}); if (!isNaN(float)) return float; console.log(global.{0}); throw new Error("Not a number")').format(self.variable).value, returns=True))
        #return number

    def toBoolean(self):
        self._update()
        if self.type == 'boolean':
           return True if 'true' == self.toString() else False
        #return True if 'true' == get_boolean(self.variable) else False
        elif self.type in ['array', 'object']: return True
        elif self.type == 'string': return self['length'].toBoolean()
        elif self.type == 'number': return self.toInteger() != 0
        elif self.type in ['null', 'undefined']: return False
        return True

    def toBool(self): return self.toBoolean()

    def toArray(self): #This is basically iter but returns the object just like for of
        return Array(self)

    def toFunction(self):
        return self.call

    def toReference(self):
        return 'RPYJSOBJECT:' + self.variable + ':RPYJSOBJECT'

    def toRef(self): return self.toReference()

    #def toDict(self): TODO

    #def toList(self): TODO

    def log(self):
        run_javascript('console.log(global.%s)' % (self.variable))
        return self

    def wait(self, awaits, native_awaits, promise_id, parent_id):
        self.resolved = True if self.type in ['null', 'undefined'] or self['then'].type != 'function' else False #False
        awaits.append(self)
        return self

    def _update(self):
        self.type = run_safe_type_update(self.variable)
        #self.type = run_javascript(String("if (global.{0} === null) {return 'null'} else if (Array.isArray(global.{0})) {return 'array'} else return typeof global.{0}").replace('{0}', self.variable).value, returns=True)
        self.resolved = True if self.type in ['null', 'undefined'] or self['then'].type != 'function' else False if self['rpython_resolved'].type != 'boolean' else True

class Wait:

    variable = ''
    parent_id = -1
    promise_id = -1

    def __init__(self):
        self.object = {'resolved': False}

handler_template = """
def resolve_next_event(parent_id, child_id):
    if False: return
"""

def get_variables_name(variables):
    return ', '.join(variables)

def get_variables_cache(variables):
    return ', '.join(['rpython_promise.var_' + variable for variable in variables])

dummy_tuple = (None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None)

def asynchronous(function):
    def keep_object(object):
        if isinstance(object, Object): object.keep()
        return object
    original_file = function.__globals__.get('__file__', "")
    original_function = function
    function_globals = function.__globals__
    class Waitable(Wait):

        rpython_promise = None
        native_map = None
        native_index = -1
        native_values = None
        native_values_count = 0
        function_name = function.__name__
        function_module = function.__module__

        def wait(self, awaits, native_awaits, promise_id, parent_id):
            self.promise_id = promise_id
            self.parent_id = parent_id
            native_awaits.append(self.object)
            return (self, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None,)

    class Promise:
        step = 1
        #last = -1
        id = -1
        promises = {}
        count = 0
        parent = None
        wait = None
        next_called = False
        function_name = function.__name__
        function_module = function.__module__

        def __init__(self, function, last):
            self.awaits = []
            self.native_awaits = []
            #self.promises = {}
            self.function = function
            self.last = last

        def entry(self, *args):
            promise = Promise(self.function, self.last)
            promise.waitable = Waitable()
            promise.waitable.rpython_promise = promise
            promise.parent = self
            promise.args = args
            promise.id = self.count
            if globals.pendingAsync is None:
               globals.pendingAsync = {}
            globals.pendingAsync[str(promise.parent.id) + ':' + str(promise.id)] = True
            self.count += 1
            self.promises[promise.id] = promise
            result = self.function(promise, promise.wait, *args)
            #if not promise.next_called and not promise.waitable.object['resolved']:
            #   promise.resolve(result)
            return promise.waitable

        def next(self):
            for object in self.awaits:
                object._update()
                if not object.resolved: return
            for native in self.native_awaits:
                if not native['resolved']: return
            for object in self.awaits: object.release()
            self.next_called = False
            self.native_awaits = []
            self.awaits = []
            self.step += 1
            result = self.function(self, self.wait, *self.args)
            #if not self.next_called and not self.waitable.object['resolved']:
            #   self.resolve(result)
            #Maybe returns here too to catch promise chain

        def wait(self, awaits=None, native=None):
            if awaits is not None:
               for object in awaits:
                   object.keep()
            self.awaits = awaits if awaits is not None else []
            self.native_awaits = [item.object for item in native] if native is not None else []
            if native is not None and len(native):
               values = []
               map = {}
               for index, object in enumerate(native):
                   object.promise_id = self.id
                   object.parent_id = self.parent.id
                   object.native_map = map
                   object.native_index = index
                   object.native_values = values
                   object.native_values_count = len(native)
               return values
            return []
            #return self.awaits, self.native_awaits

        def resolve(self, value):
            self.waitable.object['resolved'] = True
            self.value = value
            for object in self.awaits: object.release()
            if globals.pendingAsync is not None and (str(self.parent.id) + ':' + str(self.id)) in globals.pendingAsync:
               del globals.pendingAsync[str(self.parent.id) + ':' + str(self.id)]
            if self.waitable.parent_id == -1: return
            if self.waitable.native_values is not None and self.waitable.native_map is not None:
               self.waitable.native_map[self.waitable.native_index] = self.value
               if len(self.waitable.native_map) == self.waitable.native_values_count:
                  for index in range(self.waitable.native_values_count):
                      self.waitable.native_values[index] = self.waitable.native_map[index]
            globals.resolve_next_event(str(self.waitable.parent_id), str(self.waitable.promise_id))

    source = inspect.getsource(function)
    name = function.__name__
    #if '():' in source.split('\n')[1 if source and source[0] == '@' else 0]: source = source.replace('():', '(wait=None, rpython_promise=None):', 1)
    #else: source = source.replace('):', ', wait=None, rpython_promise=None):', 1)
    first_line = 0
    if source[0] in [' ', '	']:
       indents = ''
       for indent in source:
           if indent == '@': break
           indents += indent
       source = indents.join(source.split(indents)[2:])
    if source[0] == '@':
       source = '#' + source
       first_line = 1
    #if '(self' in source.split('\n')[first_line]: source = source.replace('def ' + name + '(self', 'def ' + name + '(self, rpython_promise, wait, ', 1)
    #else:
    source = source.replace('def ' + name + '(', 'def ' + name + '(rpython_promise, wait, ', 1)
    source = source.replace('.wait()', '.wait(rpython_promise.awaits, rpython_promise.native_awaits, rpython_promise.id, rpython_promise.parent.id)')
    #print source
    #source = promise_source + '\n' + source
    code = ast.parse(source)
    function = code.body[0]
    args = [arg.id for arg in function.args.args if arg.id not in ['rpython_promise', 'wait']]
    #new_function = ast.parse('def ' + name + '(rpython_promise=None, *args): ' + (', '.join(args) if args else 'args') + (' = args[0]' if len(args) == 1 else ' = args')).body[0]
    groups = []
    returns = False
    for object in ast.walk(code):
        if isinstance(object, ast.Return):
           resolve = ast.parse('rpython_promise.resolve()' if object.value is None else 'rpython_promise.resolve(rpython_keep_object())').body[0].value
           if object.value is None:
              resolve.args.append(ast.parse('None').body[0].value)
           else:
              resolve.args[0].args.append(object.value)
           object.value = resolve
    for line in function.body:
        if not groups: groups.append([])
        object = line #{'line': line}
        if isinstance(object, ast.Return):
           returns = True
           """resolve = ast.parse('return rpython_promise.resolve()' if object.value is None else 'return rpython_promise.resolve(rpython_keep_object())').body[0]
           if object.value is None:
              resolve.value.args.append(object.value if object.value is not None else ast.parse('None').body[0].value)
           else:
              resolve.value.args[0].args.append(object.value)
           #if object.value is not None:
           #   keep = ast.parse('rpython_keep_object()').body[0]
           #   keep.value.args.append(object.value)
           #   groups[-1].append(keep)
           groups[-1].append(resolve)
           break
        else: """
        groups[-1].append(object)
        conditions = []
        conditions += [(isinstance(line, ast.Expr) or isinstance(line, ast.Assign)) and isinstance(line.value, ast.Call) and isinstance(line.value.func, ast.Name) and line.value.func.id == 'wait']
        conditions += [(isinstance(line, ast.Expr) or isinstance(line, ast.Assign)) and isinstance(line.value, ast.Call) and isinstance(line.value.func, ast.Attribute) and line.value.func.attr == 'wait']
        conditions += [(isinstance(line, ast.Expr) or isinstance(line, ast.Assign)) and isinstance(line.value, ast.Tuple) and line.value.elts and any(isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) and value.func.attr == 'wait' for value in line.value.elts)]
        if any(condition for condition in conditions):
           #object['wait'] = 'function'
           groups.append([])
    if not returns:
       if not groups: groups.append([])
       groups[-1].append(ast.parse('return rpython_promise.resolve(None)').body[0])
    ast_if = ast.parse('\n'.join([('if rpython_promise is None' if index == 0 else ('elif rpython_promise.step == ' + str(index))) + ': return' for index in range(len(groups) + 1)]))
    function.body = ast_if.body
    current_elif = function.body[0].orelse[0]
    last_variables = []
    for group in groups:
        objects = []
        variables = {}
        if last_variables:
           for variable in last_variables:
               objects.append(ast.parse("if isinstance(rpython_promise.promise_{0}, tuple) and len(rpython_promise.promise_{0}) == 99 and rpython_promise.promise_{0}[0] is not None:\n rpython_promise.var_{0} = rpython_promise.promise_{0}[0].rpython_promise.value\n rpython_promise.promise_{0} = rpython_dummy_tuple".format(variable)).body[0])
           appended_variables = list(set(last_variables + args))
           objects.append(ast.parse(get_variables_name(appended_variables) + ' = ' + get_variables_cache(appended_variables)).body[0])
        for object in group:
            if isinstance(object, ast.Assign):
               for target in object.targets:
                   if isinstance(target, ast.Name): variables[target.id] = True
                   elif isinstance(target, ast.Tuple):
                      for value in target.elts:
                          if isinstance(value, ast.Name): variables[value.id] = True
            elif isinstance(object, ast.AugAssign):
               if isinstance(object.target, ast.Name): variables[object.target.id] = True
            objects += [object]
        current_elif.body = objects
        if not current_elif.orelse:
           return_object = current_elif.body[-1]
           objects = current_elif.body[0:-1]
           for variable in list(set(last_variables + args)):
               objects.append(ast.parse('if isinstance({0}, RPYObject): {0}.release()'.format(variable)).body[0])
               #objects.append(ast.parse('if isinstance({0}, RPYObject): {0}.release()\nelif isinstance({0}, rpython_list) and len({0}) and isinstance({0}[0], RPYObject):\n    for rpyobject_item in {0}: rpyobject_item.release()'.format(variable)).body[0])
           objects.append(return_object)
           current_elif.body = objects
           #last_variables = None
           break
        else:
           if len(variables) or (not len(variables) and not last_variables):
              variables = [variable for variable in variables if variable not in last_variables]
              last_variables += variables
              variables = last_variables
              for variable in list(set(variables + args)): #TODO For now args is appended on here too
#   {0}[0].promise_id, {0}[0].parent_id = rpython_promise.id, rpython_promise.parent.id
                  objects.append(ast.parse('''
if isinstance({0}, tuple) and len({0}) == 99 and {0}[0] is not None:
   rpython_promise.promise_{0} = {0}
else:
   if isinstance({0}, RPYObject): {0}.keep()
   rpython_promise.var_{0} = {0}
                  '''.format(variable)).body[0])
              #objects.append(ast.parse(get_variables_cache(variables) + ' = ' + get_variables_name(variables)).body[0])
           objects.append(ast.parse('rpython_next_event(rpython_promise)').body[0])
        current_elif = current_elif.orelse[0]
    #new_function.body += function.body
    #code.body[0] = new_function
    code = compile(code, filename='', mode='exec') if decompile is None else decompile(code, original_file, inspect.getsourcelines(original_function)[1])
    def rpython_next_event(promise):
        promise.next_called = True
        if promise.native_awaits:
           resolved_all = True
           for object in promise.native_awaits:
               if not object['resolved']:
                  resolved_all = False
                  break
           for object in promise.awaits:
               if not object.resolved:
                  resolved_all = False
                  break
           if resolved_all:
              globals.resolve_next_event(str(promise.parent.id), str(promise.id))
              return
        if promise.native_awaits and not promise.awaits: return
        run_safe_promise(str(promise.parent.id), str(promise.id), '[' + ', '.join(['"%s"' % object.variable for object in promise.awaits]) + ']')
        '''run_javascript("""
        var args = ['%s', '%s'].map(function (string) {return allocate.length === 2 ? allocate(intArrayFromString(string), ALLOC_NORMAL) : allocate(intArrayFromString(string), 'i8', ALLOC_NORMAL)});
        Promise.all(%s.map(async function (variable) {
          var object = await global[variable];
          global[variable] = object;
        })).then(function () {
          Module.asm.onresolve(...args);
        }) //.catch(function (error) {console.error(error) /*|| throw error*/});
        """ % (promise.parent.id, promise.id, '[' + ', '.join(['"%s"' % object.variable for object in promise.awaits]) + ']'))'''
    namespace = {}
    namespace.update(function_globals)
    namespace.update({'rpython_next_event': rpython_next_event, 'rpython_globals': globals, 'RPYObject': Object, 'rpython_keep_object': keep_object, 'rpython_dummy_tuple': dummy_tuple, 'rpython_list': list}) #, 'Wait': Wait})
    if decompile is None: exec(code, namespace)
    else:
       code.__dict__.update(namespace)
       namespace[function.name] = getattr(code, function.name)
    function = namespace[function.name]
    promise = Promise(function, len(groups))
    for variable in last_variables:
        setattr(promise, 'promise_' + variable, dummy_tuple)
    id = globals.promises
    promise.id = id
    globals.promises += 1
    setattr(globals, 'promise_' + str(id), promise)
    template = handler_template
    indent = ' ' * 4
    for index in range(globals.promises):
        template += indent + "elif parent_id == '%s': int(child_id) in rpython_globals.promise_%s.promises and rpython_globals.promise_%s.promises[int(child_id)].next()\n" % (index, index, index)
    if decompile is None:
       exec(template, namespace)
    else:
       fd, path = tempfile.mkstemp(dir=os.getenv('PYPY_USESSION_DIR'))
       file = open(path, 'w')
       file.write(template)
       file.seek(0)
       file.close()
       module = imp.load_source(path, path)
       module.__dict__.update(namespace)
       namespace['resolve_next_event'] = module.resolve_next_event
    globals.resolve_next_event = namespace['resolve_next_event']
    entry = promise.entry
    def async_wrapper(*args):
        return entry(*args)
    async_wrapper.asynchronous = True
    return async_wrapper

asynchronous_function = asynchronous

@entrypoint_highlevel(key='main', c_name='onresolve', argtypes=[rffi.CCHARP, rffi.CCHARP])
def onresolve(*args):
    pointers = list(args)
    parent, child = [rffi.charp2str(pointer) for pointer in pointers]
    for pointer in pointers: lltype.free(pointer, flavor='raw')
    globals.resolve_next_event(parent, child)
