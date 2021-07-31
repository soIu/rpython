(function () {
  var copy;
  return function () {
    if (!copy) copy = new Int8Array(global.Module.wasmMemory.buffer.slice());
    if (Module.rpythonShrinkToInitial && copy.byteLength < global.Module.wasmMemory.buffer.byteLength) return Module.rpythonShrinkToInitial(copy);
    var view = new Int8Array(global.Module.wasmMemory.buffer);
    view.fill(0);
    view.set(copy);
  }
})();
