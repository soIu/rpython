#ifndef EM_JS_API_H
#define EM_JS_API_H

extern const char* run_safe_json(const char* json, const char* variable);

extern const char* run_safe_get(const char* variable, const char* key, const char* new_variable);

extern void run_safe_set(const char* variable, const char* key, const char* value);

extern void run_safe_del(const char* variable, const char* key);

extern const char* run_safe_call(const char* variable, const char* args, const char* new_variable);

extern void run_safe_promise(const char* arg1, const char* arg2, const char* arg3);

extern const char* create_function(const char* id, const char* new_variable);

extern const char* create_method(const char* id, const char* method_id, const char* new_variable);

extern const char* create_js_closure(const char* func, const char* args, const char* new_variable);

extern const char* run_safe_type_update(const char* variable);

extern const char* get_string(const char* variable);

extern const char* get_integer(const char* variable);

extern const char* get_float(const char* variable);

extern const char* get_boolean(const char* variable);

#endif
