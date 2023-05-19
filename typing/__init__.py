from inspect import isclass

primitives = [str, int, float, list, dict, bool, type(None)]

def cast_primitive(value, get_type=False):
    if value in primitives: return str(value) if not get_type else value
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

def generate_frozenset(structure): return frozenset({key: original_primitives.get(structure[key], structure[key]) if not isinstance(structure[key], JSObject) else structure[key].__serialize_structure__() for key in structure}.items())

list_hash = hash(list)
dict_hash = hash(dict)

object_cache = {}

class JSObject:

    structure = None

    def __call__(self, structure=None):
        class_structure = structure
        class Object(JSObject):

            structure = class_structure

            @classmethod
            def __serialize_structure__(self):
                if not self.structure: return frozenset()
                return generate_frozenset(self.structure)

        if not structure:
            if None in object_cache: return object_cache[None]
            object_cache[None] = Object
            return Object
        for key in structure:
            setattr(Object, key, cast_primitive(structure[key], get_type=True)() if not isclass(structure[key]) or not issubclass(structure[key], JSObject) else None)
        structure_serialized = generate_frozenset(structure)
        if structure_serialized in object_cache: return object_cache[structure_serialized]
        object_cache[structure_serialized] = Object
        return Object

Object = JSObject()

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
