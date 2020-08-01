from utils import enforceargs
from .. import types

#enforceargs(dict, types.function)
def main(object, stringify_value):
    json = '{'
    index = 0
    for key in object:
        if index > 0: json += ','
        json += '"%s":' % (key)
        value = stringify_value(object[key])
        json += value
        index += 1
    json += '}'
    return json

#enforceargs(types.str)
def stringify_str(value):
    if value is None: return 'null'
    assert isinstance(value, types.str)
    return '"%s"' % (value)

#enforceargs(dict)
def str(object):
    return main(object, stringify_str)

#enforceargs(types.str)
def stringify_other(value):
    return value

#enforceargs(dict)
def int(object):
    new_object = {}
    for key in object:
        new_object[key] = 'null' if object[key] is None else types.str(object[key])
    return main(new_object, stringify_other)

#enforceargs(dict)
def float(object):
    new_object = {}
    for key in object:
        new_object[key] = 'null' if object[key] is None else repr(object[key])
    return main(new_object, stringify_other)

#enforceargs(dict)
def bool(object):
    new_object = {}
    for key in object:
        new_object[key] = 'true' if object[key] == True else 'false' if object[key] == False else 'null'
    return main(new_object, stringify_other)

boolean = bool
