from . import stringify

def fromString(value):
    if value is None: return 'null'
    return '"%s"' % (value)

def fromInt(value):
    return '%s' % (value)

def fromFloat(value):
    return repr(value)

def fromBoolean(value):
    return 'true' if value == True else 'false' if value == False else 'null'

def fromDict(value): #string only
    if value is None: return 'null'
    json = '{'
    index = 0
    for key in value:
        if index > 0: json += ','
        json += '"%s":%s' % (key, value[key])
        index += 1
    json += '}'
    return json

def fromList(value):
    if value is None: return 'null'
    return '[' + ','.join(value) + ']'
