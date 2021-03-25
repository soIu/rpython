def dummy(): pass

function = type(dummy)

class Dummy:

    def dummy(self): pass

classobj = type(Dummy)

instancemethod = type(Dummy().dummy)

NoneType = type(None)

str = str

ref = 'RPYJSON:null:RPYJSON'

int = int

float = float

bool = bool

dict = dict

list = list

tuple = tuple
