from __future__ import annotations

import json
import subprocess
import sys
import time
from typing import Any

from tooltest.tools import ToolRegistry, ToolSpec
from tooltest.events import EventBus


MAX_CODE_CHARS = 8000
DEFAULT_TIMEOUT_MS = 1000
MAX_TIMEOUT_MS = 5000
DEFAULT_MAX_OUTPUT_CHARS = 12000
MAX_OUTPUT_CHARS = 50000
DEFAULT_MEMORY_MB = 256


DISALLOWED_BUILTIN_CALLS = {
    "__import__",
    "eval",
    "exec",
    "compile",
    "open",
    "input",
    "globals",
    "locals",
    "vars",
    "dir",
    "getattr",
    "setattr",
    "delattr",
    "hasattr",
    "help",
    "breakpoint",
    "memoryview",
    "super",
    "classmethod",
    "staticmethod",
    "property",
}


PYTHON_EVAL_WORKER_CODE = r'''
from __future__ import annotations

import ast
import base64 as _base64
import binascii as _binascii
import contextlib
import hashlib as _hashlib
import hmac as _hmac
import json as _json
import math as _math
import os
import secrets as _secrets
import shutil as _shutil
import sys
import tempfile
import traceback
import uuid as _uuid
import zlib as _zlib


DISALLOWED_BUILTIN_CALLS = {
    "__import__",
    "eval",
    "exec",
    "compile",
    "open",
    "input",
    "globals",
    "locals",
    "vars",
    "dir",
    "getattr",
    "setattr",
    "delattr",
    "hasattr",
    "help",
    "breakpoint",
    "memoryview",
    "super",
    "classmethod",
    "staticmethod",
    "property",
}


DISALLOWED_AST_NODES = (
    ast.Import,
    ast.ImportFrom,
    ast.Global,
    ast.Nonlocal,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Lambda,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    ast.Raise,
    ast.Delete,
    ast.Yield,
    ast.YieldFrom,
    ast.Await,
)


SAFE_BUILTINS = {
    "abs": abs,
    "all": all,
    "any": any,
    "ascii": ascii,
    "bin": bin,
    "bool": bool,
    "bytes": bytes,
    "bytearray": bytearray,
    "chr": chr,
    "dict": dict,
    "divmod": divmod,
    "enumerate": enumerate,
    "filter": filter,
    "float": float,
    "format": format,
    "hex": hex,
    "int": int,
    "isinstance": isinstance,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "oct": oct,
    "ord": ord,
    "pow": pow,
    "print": print,
    "range": range,
    "repr": repr,
    "reversed": reversed,
    "round": round,
    "set": set,
    "slice": slice,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
}


SAFE_MATH_NAMES = {
    name: getattr(_math, name)
    for name in dir(_math)
    if not name.startswith("_")
}


SAFE_BASE64_NAMES = {
    "b64encode": _base64.b64encode,
    "b64decode": _base64.b64decode,
    "standard_b64encode": _base64.standard_b64encode,
    "standard_b64decode": _base64.standard_b64decode,
    "urlsafe_b64encode": _base64.urlsafe_b64encode,
    "urlsafe_b64decode": _base64.urlsafe_b64decode,
    "b32encode": _base64.b32encode,
    "b32decode": _base64.b32decode,
    "b32hexencode": _base64.b32hexencode,
    "b32hexdecode": _base64.b32hexdecode,
    "b16encode": _base64.b16encode,
    "b16decode": _base64.b16decode,
    "a85encode": _base64.a85encode,
    "a85decode": _base64.a85decode,
    "b85encode": _base64.b85encode,
    "b85decode": _base64.b85decode,
}


SAFE_BINASCII_NAMES = {
    "hexlify": _binascii.hexlify,
    "unhexlify": _binascii.unhexlify,
    "b2a_hex": _binascii.b2a_hex,
    "a2b_hex": _binascii.a2b_hex,
    "b2a_base64": _binascii.b2a_base64,
    "a2b_base64": _binascii.a2b_base64,
    "crc32": _binascii.crc32,
}


SAFE_HASHLIB_NAMES = {
    "md5": _hashlib.md5,
    "sha1": _hashlib.sha1,
    "sha224": _hashlib.sha224,
    "sha256": _hashlib.sha256,
    "sha384": _hashlib.sha384,
    "sha512": _hashlib.sha512,
    "sha3_224": _hashlib.sha3_224,
    "sha3_256": _hashlib.sha3_256,
    "sha3_384": _hashlib.sha3_384,
    "sha3_512": _hashlib.sha3_512,
    "shake_128": _hashlib.shake_128,
    "shake_256": _hashlib.shake_256,
    "blake2b": _hashlib.blake2b,
    "blake2s": _hashlib.blake2s,
    "new": _hashlib.new,
    "pbkdf2_hmac": _hashlib.pbkdf2_hmac,
    "scrypt": _hashlib.scrypt,
    "algorithms_available": _hashlib.algorithms_available,
    "algorithms_guaranteed": _hashlib.algorithms_guaranteed,
}


SAFE_HMAC_NAMES = {
    "new": _hmac.new,
    "digest": _hmac.digest,
    "compare_digest": _hmac.compare_digest,
}


SAFE_SECRETS_NAMES = {
    "choice": _secrets.choice,
    "randbelow": _secrets.randbelow,
    "randbits": _secrets.randbits,
    "token_bytes": _secrets.token_bytes,
    "token_hex": _secrets.token_hex,
    "token_urlsafe": _secrets.token_urlsafe,
    "compare_digest": _secrets.compare_digest,
}


SAFE_UUID_NAMES = {
    "uuid4": _uuid.uuid4,
    "uuid5": _uuid.uuid5,
    "NAMESPACE_DNS": _uuid.NAMESPACE_DNS,
    "NAMESPACE_URL": _uuid.NAMESPACE_URL,
    "NAMESPACE_OID": _uuid.NAMESPACE_OID,
    "NAMESPACE_X500": _uuid.NAMESPACE_X500,
}


SAFE_JSON_NAMES = {
    "dumps": _json.dumps,
    "loads": _json.loads,
}


SAFE_ZLIB_NAMES = {
    "compress": _zlib.compress,
    "decompress": _zlib.decompress,
    "crc32": _zlib.crc32,
    "adler32": _zlib.adler32,
}


class PythonEvalValidationError(ValueError):
    pass


class SafeModule:
    def __init__(self, name, mapping):
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_mapping", dict(mapping))

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        mapping = object.__getattribute__(self, "_mapping")
        if name not in mapping:
            module_name = object.__getattribute__(self, "_name")
            raise AttributeError(f"{module_name}.{name} is not allowed")

        return mapping[name]

    def __setattr__(self, name, value):
        raise AttributeError("SafeModule is read-only")

    def __repr__(self):
        module_name = object.__getattribute__(self, "_name")
        return f"<safe module {module_name}>"


class LimitedTextIO:
    def __init__(self, max_chars):
        self.max_chars = max(0, int(max_chars))
        self.parts = []
        self.size = 0
        self.truncated = False

    def write(self, s):
        s = str(s)

        if self.size >= self.max_chars:
            self.truncated = True
            return len(s)

        room = self.max_chars - self.size

        if len(s) > room:
            self.parts.append(s[:room])
            self.size += room
            self.truncated = True
        else:
            self.parts.append(s)
            self.size += len(s)

        return len(s)

    def flush(self):
        pass

    def getvalue(self):
        return "".join(self.parts)


class _SafeAstValidator(ast.NodeVisitor):
    def visit(self, node):
        if isinstance(node, DISALLOWED_AST_NODES):
            raise PythonEvalValidationError(
                f"Unsupported syntax: {node.__class__.__name__}"
            )
        return super().visit(node)

    def visit_Name(self, node):
        if node.id.startswith("_"):
            raise PythonEvalValidationError(
                f"Private name is not allowed: {node.id}"
            )

        if node.id in DISALLOWED_BUILTIN_CALLS:
            raise PythonEvalValidationError(
                f"Disallowed name: {node.id}"
            )

        self.generic_visit(node)

    def visit_Attribute(self, node):
        if node.attr.startswith("_"):
            raise PythonEvalValidationError(
                f"Private attribute is not allowed: {node.attr}"
            )

        self.generic_visit(node)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name):
            if node.func.id in DISALLOWED_BUILTIN_CALLS:
                raise PythonEvalValidationError(
                    f"Disallowed call: {node.func.id}"
                )

        if isinstance(node.func, ast.Attribute):
            if node.func.attr.startswith("_"):
                raise PythonEvalValidationError(
                    f"Disallowed attribute call: {node.func.attr}"
                )

        self.generic_visit(node)


def _install_light_resource_limits(timeout_ms, memory_mb):
    try:
        import resource
    except Exception:
        return

    try:
        cpu_seconds = max(1, int(timeout_ms / 1000) + 1)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    except Exception:
        pass

    try:
        memory_bytes = int(memory_mb) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    except Exception:
        pass


def _safe_globals():
    return {
        "__builtins__": SAFE_BUILTINS,
        "math": SafeModule("math", SAFE_MATH_NAMES),
        "base64": SafeModule("base64", SAFE_BASE64_NAMES),
        "binascii": SafeModule("binascii", SAFE_BINASCII_NAMES),
        "hashlib": SafeModule("hashlib", SAFE_HASHLIB_NAMES),
        "hmac": SafeModule("hmac", SAFE_HMAC_NAMES),
        "secrets": SafeModule("secrets", SAFE_SECRETS_NAMES),
        "uuid": SafeModule("uuid", SAFE_UUID_NAMES),
        "json": SafeModule("json", SAFE_JSON_NAMES),
        "zlib": SafeModule("zlib", SAFE_ZLIB_NAMES),
    }


def _parse_and_validate(code):
    if not isinstance(code, str) or not code.strip():
        raise ValueError("code is required")

    tree = ast.parse(code, mode="exec")
    _SafeAstValidator().visit(tree)
    return tree


def _bytes_to_jsonable(value):
    raw = bytes(value)
    max_bytes = 4096
    sample = raw[:max_bytes]

    out = {
        "type": "bytes",
        "size": len(raw),
        "base64": _base64.b64encode(sample).decode("ascii"),
        "hex": sample.hex(),
        "truncated": len(raw) > max_bytes,
    }

    try:
        out["utf8"] = sample.decode("utf-8")
    except UnicodeDecodeError:
        pass

    return out


def _to_jsonable(value, depth=0, max_items=50):
    if depth > 4:
        return repr(value)

    if value is None or isinstance(value, (int, float, bool)):
        return value

    if isinstance(value, str):
        return value[:4000]

    if isinstance(value, (bytes, bytearray)):
        return _bytes_to_jsonable(value)

    if isinstance(value, _uuid.UUID):
        return str(value)

    if isinstance(value, (list, tuple, set)):
        items = list(value)
        return [
            _to_jsonable(item, depth + 1, max_items)
            for item in items[:max_items]
        ]

    if isinstance(value, dict):
        out = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= max_items:
                break
            out[str(k)] = _to_jsonable(v, depth + 1, max_items)
        return out

    return repr(value)


def _truncate_text(text, max_chars):
    if len(text) <= max_chars:
        return text, False

    return text[:max_chars], True


def _cleanup_tmpdir_best_effort(path):
    if not path:
        return

    try:
        _shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def _run_user_code(code, variables, max_output_chars):
    tree = _parse_and_validate(code)

    stdout_buffer = LimitedTextIO(max_output_chars)
    stderr_buffer = LimitedTextIO(max_output_chars)

    safe_globals = _safe_globals()
    safe_locals = dict(variables)

    result = None

    with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            prefix_body = tree.body[:-1]
            last_expr = tree.body[-1].value

            if prefix_body:
                prefix_tree = ast.Module(body=prefix_body, type_ignores=[])
                ast.fix_missing_locations(prefix_tree)
                exec(
                    compile(prefix_tree, "<python_eval>", "exec"),
                    safe_globals,
                    safe_locals,
                )

            expr_tree = ast.Expression(body=last_expr)
            ast.fix_missing_locations(expr_tree)
            result = eval(
                compile(expr_tree, "<python_eval>", "eval"),
                safe_globals,
                safe_locals,
            )
        else:
            ast.fix_missing_locations(tree)
            exec(
                compile(tree, "<python_eval>", "exec"),
                safe_globals,
                safe_locals,
            )
            result = None

    result_repr, result_repr_truncated = _truncate_text(repr(result), 4000)

    return {
        "stdout": stdout_buffer.getvalue(),
        "stderr": stderr_buffer.getvalue(),
        "result": _to_jsonable(result),
        "result_repr": result_repr,
        "result_type": type(result).__name__,
        "truncated": bool(
            stdout_buffer.truncated
            or stderr_buffer.truncated
            or result_repr_truncated
        ),
    }


def main():
    try:
        payload = _json.loads(sys.stdin.read())

        code = payload.get("code")
        variables = payload.get("variables") or {}
        max_output_chars = int(payload.get("max_output_chars") or 12000)
        timeout_ms = int(payload.get("timeout_ms") or 1000)
        memory_mb = int(payload.get("memory_mb") or 256)

        try:
            os.environ.clear()
        except Exception:
            pass

        try:
            sys.setrecursionlimit(1000)
        except Exception:
            pass

        _install_light_resource_limits(timeout_ms, memory_mb)

        old_cwd = None
        tmpdir = None

        try:
            try:
                old_cwd = os.getcwd()
            except Exception:
                old_cwd = None

            tmpdir = tempfile.mkdtemp(prefix="tool_python_eval_")

            try:
                os.chdir(tmpdir)
            except Exception:
                pass

            result = _run_user_code(code, variables, max_output_chars)
        finally:
            if old_cwd:
                try:
                    os.chdir(old_cwd)
                except Exception:
                    pass

            if tmpdir:
                _cleanup_tmpdir_best_effort(tmpdir)

        result["status"] = "ok"

        sys.stdout.write(
            _json.dumps(
                result,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    except BaseException as exc:
        sys.stdout.write(
            _json.dumps(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(limit=6),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )


main()
'''


class PythonEvalValidationError(ValueError):
    pass


class PythonEvalRuntimeError(RuntimeError):
    pass


class PythonEvalTimeoutError(TimeoutError):
    pass


def _clamp_int(value: Any, default: int, min_value: int, max_value: int) -> int:
    try:
        v = int(value)
    except Exception:
        return default

    return max(min_value, min(v, max_value))


def _validate_variable_name(name: str) -> None:
    if not isinstance(name, str):
        raise ValueError("Variable name must be string")

    if not name.isidentifier():
        raise ValueError(f"Invalid variable name: {name}")

    if name.startswith("_"):
        raise ValueError(f"Private variable name is not allowed: {name}")

    if name in DISALLOWED_BUILTIN_CALLS:
        raise ValueError(f"Disallowed variable name: {name}")


def _validate_jsonable(value: Any, depth: int = 0) -> None:
    if depth > 8:
        raise ValueError("Variable value is too deeply nested")

    if value is None or isinstance(value, (str, int, float, bool)):
        return

    if isinstance(value, list):
        if len(value) > 1000:
            raise ValueError("Variable list is too large")

        for item in value:
            _validate_jsonable(item, depth + 1)

        return

    if isinstance(value, dict):
        if len(value) > 1000:
            raise ValueError("Variable dict is too large")

        for k, v in value.items():
            if not isinstance(k, str):
                raise ValueError("Variable dict keys must be strings")

            _validate_jsonable(v, depth + 1)

        return

    raise ValueError(f"Unsupported variable value type: {type(value).__name__}")


def _validate_variables(variables: Any) -> dict[str, Any]:
    if variables is None:
        return {}

    if not isinstance(variables, dict):
        raise ValueError("variables must be an object")

    validated: dict[str, Any] = {}

    for key, value in variables.items():
        _validate_variable_name(key)
        _validate_jsonable(value)
        validated[key] = value

    return validated


def _validate_code(code: Any) -> str:
    if not isinstance(code, str) or not code.strip():
        raise ValueError("code is required")

    if len(code) > MAX_CODE_CHARS:
        raise ValueError(f"code is too long, max {MAX_CODE_CHARS} chars")

    return code


def _run_python_eval_subprocess(
    *,
    code: str,
    variables: dict[str, Any],
    timeout_ms: int,
    max_output_chars: int,
    memory_mb: int,
) -> dict[str, Any]:
    payload = {
        "code": code,
        "variables": variables,
        "timeout_ms": timeout_ms,
        "max_output_chars": max_output_chars,
        "memory_mb": memory_mb,
    }

    started = time.monotonic()

    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-c",
                PYTHON_EVAL_WORKER_CODE,
            ],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_ms / 1000,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PythonEvalTimeoutError(
            f"Python eval timed out after {timeout_ms} ms"
        ) from exc

    elapsed_ms = int((time.monotonic() - started) * 1000)

    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        detail = stderr or stdout or "<no child output>"

        raise PythonEvalRuntimeError(
            f"Python eval worker exited with exitcode={completed.returncode}: {detail}"
        )

    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()

        raise PythonEvalRuntimeError(
            f"Python eval worker returned invalid JSON. stdout={stdout!r}, stderr={stderr!r}"
        ) from exc

    if payload.get("status") != "ok":
        error_type = payload.get("error_type", "PythonEvalError")
        message = payload.get("message", "")
        tb = payload.get("traceback", "")

        raise PythonEvalRuntimeError(f"{error_type}: {message}\n{tb}")

    return {
        "stdout": payload.get("stdout", ""),
        "stderr": payload.get("stderr", ""),
        "result": payload.get("result"),
        "result_repr": payload.get("result_repr", ""),
        "result_type": payload.get("result_type", "NoneType"),
        "truncated": bool(payload.get("truncated", False)),
        "elapsed_ms": elapsed_ms,
    }


def _emit(ctx: Any, event_name: str, payload: dict[str, Any]) -> None:
    if ctx is not None and hasattr(ctx, "emit"):
        ctx.emit(event_name, payload)


def register(registry: ToolRegistry, event_bus: EventBus | None = None):
    @registry.register(
        ToolSpec(
            name="util_python_eval",
            description=(
                "Run small Python calculations or simple scripts in a lightly isolated subprocess. "
                "Imports, file access, eval/exec/open and private attributes are blocked. "
                "Allowed helper modules: math, base64, binascii, hashlib, hmac, secrets, uuid, json, zlib."
            ),
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python code. If the last statement is an expression, it is returned as result.",
                    },
                    "variables": {
                        "type": "object",
                        "description": "Optional JSON variables available to the code.",
                    },
                    "timeout_ms": {
                        "type": "integer",
                        "default": DEFAULT_TIMEOUT_MS,
                    },
                    "max_output_chars": {
                        "type": "integer",
                        "default": DEFAULT_MAX_OUTPUT_CHARS,
                    },
                },
                "required": ["code"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "stdout": {"type": "string"},
                    "stderr": {"type": "string"},
                    "result": {},
                    "result_repr": {"type": "string"},
                    "result_type": {"type": "string"},
                    "truncated": {"type": "boolean"},
                    "elapsed_ms": {"type": "integer"},
                },
                "required": [
                    "stdout",
                    "stderr",
                    "result_type",
                    "truncated",
                    "elapsed_ms",
                ],
                "additionalProperties": False,
            },
        )
    )
    def util_python_eval(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        code = _validate_code(args.get("code"))
        variables = _validate_variables(args.get("variables"))

        timeout_ms = _clamp_int(
            args.get("timeout_ms"),
            DEFAULT_TIMEOUT_MS,
            50,
            MAX_TIMEOUT_MS,
        )

        max_output_chars = _clamp_int(
            args.get("max_output_chars"),
            DEFAULT_MAX_OUTPUT_CHARS,
            1000,
            MAX_OUTPUT_CHARS,
        )

        result = _run_python_eval_subprocess(
            code=code,
            variables=variables,
            timeout_ms=timeout_ms,
            max_output_chars=max_output_chars,
            memory_mb=DEFAULT_MEMORY_MB,
        )

        _emit(
            ctx,
            "util.python_eval.completed",
            {
                "tool": "util_python_eval",
                "code_chars": len(code),
                "timeout_ms": timeout_ms,
                "stdout_chars": len(result["stdout"]),
                "stderr_chars": len(result["stderr"]),
                "result_type": result["result_type"],
                "elapsed_ms": result["elapsed_ms"],
            },
        )

        return result