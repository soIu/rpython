(function () {
  var copy;
  return function () {
    var releaseLock = global.Module.rpython_release_gc_lock;
    var checkPendingAsync = global.Module.rpython_check_pending_async;
    if (checkPendingAsync()) return releaseLock();
    if (!copy) {
      releaseLock();
      copy = new Int8Array(global.Module.wasmMemory.buffer.slice());
    }
    if (Module.rpythonShrinkToInitial && copy.byteLength < global.Module.wasmMemory.buffer.byteLength) return Module.rpythonShrinkToInitial(copy);
    var view = new Int8Array(global.Module.wasmMemory.buffer);
    view.fill(0);
    view.set(copy);
  }
})();
