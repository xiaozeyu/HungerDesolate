"""Render validated desired world settings into DST cluster configuration files."""
import configparser
import io
import re


def lua_string(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("invalid option value")
    return '"' + value + '"'


def render_worldgen(settings, shard):
    shard_config = settings["worldgen"][shard]
    overrides = {}
    overrides.update(shard_config["worldgenOverrides"])
    overrides.update(shard_config["worldSettingsOverrides"])
    rows = ["return {", "    override_enabled = true,", "    preset = " + lua_string(shard_config["preset"]) + ",", "    overrides = {"]
    for key, value in sorted(overrides.items()):
        if not re.fullmatch(r"[A-Za-z0-9_]{1,128}", key):
            raise ValueError("invalid option key")
        rows.append("        " + key + " = " + lua_string(value) + ",")
    rows.extend(["    },", "}", ""])
    return "\n".join(rows).encode("ascii")


def render_cluster(settings, token, password, cluster_key):
    if not isinstance(token, bytes) or not token or len(token) > 8192 or any(value < 0x20 for value in token):
        raise ValueError("invalid token")
    if not isinstance(password, bytes) or len(password) > 8192 or any(value < 0x20 for value in password):
        raise ValueError("invalid server password")
    if not isinstance(cluster_key, str) or not re.fullmatch(r"[0-9a-f]{32}", cluster_key):
        raise ValueError("invalid cluster key")
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser["GAMEPLAY"] = {
        "game_mode": settings["gameMode"],
        "max_players": str(settings["maxPlayers"]),
        "pvp": str(settings["pvp"]).lower(),
        "pause_when_empty": str(settings["pauseWhenEmpty"]).lower(),
    }
    parser["NETWORK"] = {
        "cluster_name": settings["roomName"],
        "cluster_description": settings["description"],
        "cluster_password": password.decode("utf-8"),
        "cluster_intention": "cooperative",
        "cluster_language": settings["language"],
    }
    parser["MISC"] = {"console_enabled": "true"}
    parser["SHARD"] = {
        "shard_enabled": str(settings["cavesEnabled"]).lower(),
        "bind_ip": "0.0.0.0",
        "master_ip": "master",
        "master_port": "10888",
        "cluster_key": cluster_key,
    }
    parser["GAMEPLAY"]["vote_enabled"] = str(settings["advanced"]["voteEnabled"]).lower()
    parser["GAMEPLAY"]["vote_kick_percentage"] = str(settings["advanced"]["voteKickPercentage"])
    parser["GAMEPLAY"]["whitelist_slots"] = str(settings["advanced"]["whitelistSlots"])
    output = io.StringIO()
    parser.write(output)
    return output.getvalue().encode("utf-8"), token.rstrip(b"\r\n")


def render_shard_ini(settings, shard, port_plan):
    ports = {item["purpose"]: item["containerPort"] for item in port_plan if item["shard"] == shard}
    if set(ports) != {"game", "steamAuth", "steamMaster"}:
        raise ValueError("incomplete shard ports")
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser["NETWORK"] = {"server_port": str(ports["game"])}
    parser["SHARD"] = {"is_master": str(shard == "master").lower(),
                       "name": "Master" if shard == "master" else "Caves"}
    parser["STEAM"] = {
        "authentication_port": str(ports["steamAuth"]),
        "master_server_port": str(ports["steamMaster"]),
    }
    output = io.StringIO()
    parser.write(output)
    return output.getvalue().encode("utf-8")


def render(settings, token, password, cluster_key, port_plan):
    cluster, token_file = render_cluster(settings, token, password, cluster_key)
    files = {
        "Cluster_1/cluster.ini": cluster,
        "Cluster_1/cluster_token.txt": token_file,
        "Cluster_1/Master/server.ini": render_shard_ini(settings, "master", port_plan),
        "Cluster_1/Master/worldgenoverride.lua": render_worldgen(settings, "master"),
        "Cluster_1/adminlist.txt": render_player_list(settings["advanced"]["adminList"]),
        "Cluster_1/whitelist.txt": render_player_list(settings["advanced"]["whitelist"]),
        "Cluster_1/blocklist.txt": render_player_list(settings["advanced"]["blocklist"]),
    }
    if settings["cavesEnabled"]:
        files["Cluster_1/Caves/server.ini"] = render_shard_ini(settings, "caves", port_plan)
        files["Cluster_1/Caves/worldgenoverride.lua"] = render_worldgen(settings, "caves")
    return files


def render_player_list(values):
    if (not isinstance(values, list)
            or any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value)
                   for value in values)
            or len(values) != len(set(values))):
        raise ValueError("invalid player list")
    return ("".join(value + "\n" for value in values)).encode("ascii")


render_config = render
