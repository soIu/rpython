
int jitExists(int);
int jitReserve(void);
int jitCompile(char*);
int jitRecompile(int, char*);
void jitCopy(int, int);
int jitInvoke(int, int, int, int);
void jitFree(int);

