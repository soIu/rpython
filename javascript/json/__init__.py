from . import stringify
from json.encoder import ESCAPE_DCT as escapes
from rpython.rlib.rstring import replace

def encode(value):
    for escape in reversed(escapes.keys()):
    #    #value = escapes[escape].join(value.split(escape))
        value = replace(value, escape, escapes[escape])
    return '"' + value + '"'

def rpyjson(function):
    def wrapper(value, parse=False):
        result = function(value)
        if parse: return result
        return 'RPYJSON:' + result + ':RPYJSON'
    return wrapper

def parse_rpy_json(value):
    if value is None: return 'null'
    if value.startswith('RPYJSON:') and value.endswith(':RPYJSON'):
       end = len(value) - 8
       assert end >= 0
       return value[8:end]
    return encode(value)
    #return '"%s"' % (value.encode('string-escape'))

def rawString(value):
    return 'RPYJSON:' + value + ':RPYJSON'

@rpyjson
def fromString(value):
    if value is None: return 'null'
    return encode(value)
    #return '"%s"' % (value.encode('string-escape'))

fromStr = fromString

@rpyjson
def fromInt(value):
    return '%s' % (value)

fromInteger = fromInt

@rpyjson
def fromFloat(value):
    return repr(value)

@rpyjson
def fromBoolean(value):
    return 'true' if value == True else 'false' if value == False else 'null'

fromBool = fromBoolean

@rpyjson
def fromDict(value): #string only
    if value is None: return 'null'
    json = '{'
    index = 0
    for key in value:
        if index > 0: json += ','
        json += ('"%s":' % key) + parse_rpy_json(value[key])
        index += 1
    json += '}'
    return json

@rpyjson
def fromList(values):
    if values is None: return 'null'
    return '[' + ','.join([parse_rpy_json(value) for value in values]) + ']'

@rpyjson
def fromTuple(values):
    if values is None: return 'null'
    return fromList(list(values), parse=True)

def isFalse(value):
    value = parse_rpy_json(value)
    if value in ['""', '0', '0.0', 'null', 'false', '{}', '[]']: return False
    return True

def isTrue(value): return not isFalse(value)
