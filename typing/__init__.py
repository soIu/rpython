from inspect import isclass

primitives = [str, int, float, list, dict, bool, type(None)]

def cast_primitive(value, get_type=False):
    if value in primitives: return str(value) if not get_type else value
    if isinstance(value, JSFunction): return str(function_type)
    determine = None
    try:
        determine = type(value)
        if determine not in primitives: determine = determine.__bases__[0]
    except: pass
    if determine not in primitives: raise Exception('Cannot find a primitive type for: ' + str(value))
    if get_type:
        if determine is None or type(None) == determine: return lambda: None
        return determine
    return str(determine)

function_type = type(cast_primitive)

def generate_frozenset(structure): return frozenset({key: original_primitives.get(structure[key], structure[key]) if not isinstance(structure[key], JSObject) else structure[key].__serialize_structure__() for key in structure}.items())

list_hash = hash(list)
dict_hash = hash(dict)

object_cache = {}

object_loads_template = """
def loads(self, values):
"""

def adapt_object_to_field(type):
    import json
    if type == str:
        return '.toString()'
    elif type == int:
        return '.toInteger()'
    elif type == float:
        return '.toFloat()'
    elif type == bool:
        return '.toBoolean()'
    elif type in [List, primitives[3]] or isinstance(type, primitives[3]):
        if type in [List, primitives[3]] or (isinstance(type, ListClass) and type.current_type == JSObjectInstance):
            return '.toList()'
        elif isinstance(type, ListClass) and type.current_type == Types.str: return '.toListString()'
        elif isinstance(type, ListClass) and type.current_type == Types.int: return '.toListInteger()'
        elif isinstance(type, ListClass) and type.current_type == Types.float: return '.toListFloat()'
        elif isinstance(type, ListClass) and type.current_type == Types.bool: return '.toListBoolean()'
    elif type in [Dict, primitives[4]] or isinstance(type, primitives[4]):
        if type in [Dict, primitives[4]] or (isinstance(type, DictClass) and type.current_types in [[str, None], [str, JSObjectInstance]]):
            return '.toDict()'
        elif isinstance(type, DictClass) and type.current_types == [Types.str, Types.str]: return '.toDictStringString()'
        elif isinstance(type, DictClass) and type.current_types == [Types.str, Types.int]: return '.toDictStringInteger()'
        elif isinstance(type, DictClass) and type.current_types == [Type.str, Types.float]: return '.toDictStringFloat()'
        elif isinstance(type, DictClass) and type.current_types == [Types.str, Types.bool]: return '.toDictStringBoolean()'
    elif type == Function:
        return '.toFunction()'
    #Implement this later
    #elif isinstance(type, JSFunction):
    #    return
    raise Exception('Cannot determine type')

def configure_object(object):
    loads = object_loads_template
    indent = ' ' * 4
    count = 0
    namespace = {}
    for key in object.structure:
        count += 1
        field = object.structure[key]
        if field == JSObjectInstance:
            loads += '\n' + indent + "self." + key + " = values.unsafe_get_item('" + key  + "')"
        elif isclass(field) and issubclass(field, JSObject):
            variable = 'class_' + str(count)
            namespace[variable] = field
            loads += '\n' + indent + "self." + key + ' = ' + variable + '(' + "values.unsafe_get_item('" + key  + "')" + ')'
        else: loads += '\n' + indent + "if values.unsafe_get_item('" + key  + "').type != 'undefined': self." + key + ' = ' + "values.unsafe_get_item('" + key + "')" + adapt_object_to_field(field)
    loads += '\n' + indent + 'return self'
    exec(loads, namespace)
    object.loads = namespace['loads']

class JSObject:

    structure = None

    def __serialize_structure__(self): return frozenset()

    def __call__(self, structure=None):
        if not structure: return JSObjectInstance
        class_structure = structure
        class Object(JSObject):

            structure = class_structure

            @classmethod
            def __serialize_structure__(self):
                #if not self.structure: return frozenset()
                return generate_frozenset(self.structure)

            def __init__(self, values):
                self.loads(values)

        if not structure:
            if None in object_cache: return object_cache[None]
            object_cache[None] = Object
            return Object
        structure_serialized = generate_frozenset(structure)
        if structure_serialized in object_cache: return object_cache[structure_serialized]
        for key in structure:
            setattr(Object, key, cast_primitive(structure[key], get_type=True)() if (not isclass(structure[key]) or not issubclass(structure[key], JSObject)) and structure[key] != JSObjectInstance and not isinstance(structure[key], JSFunction) else None)
        configure_object(Object)
        object_cache[structure_serialized] = Object
        return Object

from threading import Timer
Timer(2, lambda: delattr(JSObject, '__call__')).start()

import os
if os.getenv('RPY_USE_EMSCRIPTEN') == 'true':
    from . import javascript
    JSObject.get = javascript.unsafe_global_get

Object = JSObject()

JSObjectInstance = Object

function_cache = {}

class JSFunction:

    current_types = []

    def __getitem__(self, types):
        return Exception('Handling of JS types conversion to Python is not implemented yet')
        if not isinstance(types, tuple):
            types = [types]
        current_types = [cast_primitive(type) for type in types]
        types_stringified = str(current_types)
        if types_stringified in function_cache: return function_cache[types_stringified]
        new = JSFunction()
        new.current_types = current_types
        function_cache[types_stringified] = new
        return new

    #def __hash__(self):
    #    hash(tuple(self))

Function = JSFunction()

list_cache = {}

class List(list):

    current_type = None

    def __getitem__(self, type):
        if isinstance(type, tuple): raise Exception('Expected one type for List')
        current_type = cast_primitive(type)
        if current_type in list_cache: return list_cache[current_type]
        new = ListClass()
        new.current_type = current_type
        list_cache[current_type] = new
        return new

    def __hash__(self):
        return list_hash if self.current_type is None else hash(self.current_type)

ListClass = List
List = List()

dict_cache = {}

class Dict(dict):

    current_types = [None, None]

    def __getitem__(self, types):
        if not isinstance(types, tuple):
            types = [types, None]
        elif len(types) != 2: raise Exception('Expected 2 types for Dict (key and value)')
        if types[0] != str: raise Exception('Currently non-string key on dict is not handled, submit PR if you really want this to be implemented natively')
        elif types[1] in [None, JSObjectInstance]: return self
        current_types = [cast_primitive(type) for type in types]
        types_stringified = str(current_types)
        if types_stringified in dict_cache: return dict_cache[types_stringified]
        new = DictClass()
        new.current_types = current_types
        dict_cache[types_stringified] = new
        return new

    def __hash__(self):
        return dict_hash if self.current_types == [None, None] else hash(str(self.current_types))

DictClass = Dict
Dict = Dict()

original_primitives = {List: list, Dict: dict}

#String Types (Enum)
class Types:
    int = str(int)
    float = str(float)
    bool = str(bool)
    list = str(list)
    dict = str(dict)
    function = str(function_type)
    str = str(str)

list = List
dict = Dict

String = str
Integer = int
Float = float
Boolean = bool
Bool = bool

str = String
int = Integer
float = Float
bool = Bool

PlainObject = Object()
