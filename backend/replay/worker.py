"""Trusted entry point for the isolated replay subprocess. Input contains bounded integers only."""
import hashlib
import json
import sys
from importlib.metadata import version
from pathlib import Path

import wasmtime as wasm


def execute(request):
    if version("wasmtime") != request["runtime"]:
        raise ValueError("runtime mismatch")
    program = Path(__file__).with_name("policies.wat").read_text(encoding="utf-8").encode()
    if len(program) > 32768 or hashlib.sha256(program).hexdigest() != request["module_digest"]:
        raise ValueError("module mismatch")
    if request["setting"] not in {"citation_retry", "source_fallback"} or len(request["inputs"]) > 16:
        raise ValueError("invalid replay request")
    config = wasm.Config()
    config.consume_fuel = True
    config.wasm_threads = False
    config.parallel_compilation = False
    with wasm.Engine(config) as engine, wasm.Module(engine, program) as module, wasm.Store(engine) as store:
        if module.imports:
            raise ValueError("host imports forbidden")
        store.set_limits(memory_size=0, table_elements=0, instances=1, tables=0, memories=0)
        store.set_fuel(10_000)
        instance = wasm.Instance(store, module, [])
        run = instance.exports(store)[request["setting"]]
        return [[run(store, switch, *inputs) for switch in request["switches"]] for inputs in request["inputs"]]


if __name__ == "__main__":
    output = execute(json.loads(sys.stdin.buffer.read(8192)))
    print(json.dumps(output))
