"""Shared modoverrides.lua and setup script rendering.

Both mods-apply and the player bridge install use this module; do not keep a
second copy of the bridge entry in another script.
"""
import math


class OverridesRejected(Exception):
    pass


def reject(code):
    raise OverridesRejected(code)


def lua_string(value):
    pieces = []
    for character in value:
        code = ord(character)
        if character == "\\": pieces.append("\\\\")
        elif character == '"': pieces.append('\\"')
        elif character == "\n": pieces.append("\\n")
        elif character == "\r": pieces.append("\\r")
        elif character == "\t": pieces.append("\\t")
        elif code < 32 or code == 127: pieces.append("\\%03d" % code)
        else: pieces.append(character)
    return '"' + "".join(pieces) + '"'


def lua_value(value, depth=0):
    if depth > 12: reject("MOD_CONFIG_UNSUPPORTED")
    if value is None: reject("MOD_CONFIG_UNSUPPORTED")
    if type(value) is bool: return "true" if value else "false"
    if type(value) is int: return str(value)
    if isinstance(value, float):
        if not math.isfinite(value): reject("MOD_CONFIG_UNSUPPORTED")
        return repr(value)
    if isinstance(value, str): return lua_string(value)
    if isinstance(value, list): return "{" + ",".join(lua_value(item, depth + 1) for item in value) + "}"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value): reject("MOD_CONFIG_UNSUPPORTED")
        return "{" + ",".join("[" + lua_string(key) + "]=" + lua_value(value[key], depth + 1)
                              for key in sorted(value)) + "}"
    reject("MOD_CONFIG_UNSUPPORTED")


def modoverrides(mods, bridge_enabled=False):
    rows = []
    for item in mods:
        if item["enabled"]:
            rows.append("  [" + lua_string("workshop-" + item["workshopId"])
                        + "]={enabled=true,configuration_options="
                        + lua_value(item["configurationOptions"]) + "}")
    if bridge_enabled:
        rows.append('  ["hungerhub_bridge"]={enabled=true}')
    return ("return {\n" + ",\n".join(rows) + ("\n" if rows else "") + "}\n").encode()


def setup_script(mods):
    return "".join('ServerModSetup("' + item["workshopId"] + '")\n'
                   for item in mods if item["enabled"]).encode()
