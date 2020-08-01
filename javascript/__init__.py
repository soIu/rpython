from . import json, types

JSON = json

class Object:

    key = {}
    values = ()

    def __init__(self, object):
        index = 0
        for key in object:
            self.key[key] = index
            function = object[key]
            if function == types.string:
               self.values += ('',)
            elif function == types.integer:
               self.values += (0,)
            elif function == types.float:
               self.values += (0.0,)
            elif function == types.boolean:
               self.values += (False,)
            index += 1
