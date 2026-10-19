"""Read-only creation planning. No directories, reservations or containers are created."""
import base64
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time
import uuid

ROOT = Path('/var/lib/famine-hub')
PROC_NET = Path('/proc/net')
MAX_REVISION = 9007199254740991


class Rejected(Exception):
    pass


def reject(code):
    raise Rejected(code)


def valid_id(value):
    try:
        parsed = uuid.UUID(value)
        return parsed.version == 4 and str(parsed) == value
    except (TypeError, ValueError, AttributeError):
        return False


def read_file(path, limit):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022):
        reject('CREATE_METADATA_UNSAFE')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as source:
        opened = os.fstat(source.fileno())
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            reject('CREATE_METADATA_CHANGED')
        data = source.read(limit + 1)
    if len(data) > limit:
        reject('CREATE_METADATA_UNSAFE')
    return data


def directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022:
        reject('CREATE_METADATA_UNSAFE')


def port(value):
    return type(value) is int and 1 <= value <= 65535


def read_port(item, host_id, instance_id=None):
    if not isinstance(item, dict):
        reject('CREATE_PORT_STATE_INVALID')
    if instance_id is not None and (item.get('hostId') != host_id or item.get('instanceId') != instance_id):
        reject('CREATE_PORT_STATE_INVALID')
    if (item.get('protocol') not in ('udp', 'tcp') or not port(item.get('hostPort'))
            or not port(item.get('containerPort')) or item.get('containerPort') != item.get('hostPort')
            or item.get('shard') not in ('master', 'caves')
            or item.get('purpose') not in ('game', 'steamAuth', 'steamMaster')
            or not isinstance(item.get('bindAddress'), str)):
        reject('CREATE_PORT_STATE_INVALID')
    import ipaddress
    try:
        ipaddress.ip_address(item['bindAddress'])
    except ValueError:
        reject('CREATE_PORT_STATE_INVALID')
    return item['protocol'], item['hostPort']


def registry_ports(host_id, expected_revision):
    directory(ROOT)
    before = read_file(ROOT / 'registry.json', 1024 * 1024)
    host_before = read_file(ROOT / 'host.json', 65536)
    host = json.loads(host_before)
    registry = json.loads(before)
    if type(host.get('schemaVersion')) is not int or host.get('schemaVersion') != 1 or host.get('hostId') != host_id or registry.get('hostId') != host_id:
        reject('HOST_IDENTITY_MISMATCH')
    revision = registry.get('registryRevision')
    if (type(registry.get('schemaVersion')) is not int or registry.get('schemaVersion') != 1 or type(revision) is not int or not 0 <= revision <= MAX_REVISION
            or not isinstance(registry.get('instances'), list) or len(registry['instances']) > 200):
        reject('CREATE_REGISTRY_INVALID')
    if revision != expected_revision:
        reject('REGISTRY_CHANGED')
    ids = set()
    for entry in registry['instances']:
        if (not isinstance(entry, dict) or not valid_id(entry.get('instanceId')) or entry['instanceId'] in ids
                or entry.get('lifecycle') not in ('creating','ready','createFailed','deleting','deleteFailed','retired')):
            reject('CREATE_REGISTRY_INVALID')
        ids.add(entry['instanceId'])
    reservations = registry.get('portReservations')
    blockers = []
    used = set()
    if reservations is None:
        blockers.append('PORT_RESERVATIONS_MISSING')
        reservations = []
    if not isinstance(reservations, list) or len(reservations) > 2400:
        reject('CREATE_PORT_STATE_INVALID')
    for item in reservations:
        owner = item.get('instanceId') if isinstance(item, dict) else None
        if owner not in ids:
            reject('CREATE_PORT_STATE_INVALID')
        used.add(read_port(item, host_id, owner))
    snapshots = []
    for entry in registry['instances']:
        instance_id = entry['instanceId']
        retired = entry['lifecycle'] == 'retired'
        relative = ('retired/' if retired else 'instances/') + instance_id
        if entry.get('relativePath', relative) != relative:
            reject('CREATE_METADATA_UNSAFE')
        if retired:
            continue  # Reservations are still kept conservatively until explicitly released.
        try:
            directory(ROOT / 'instances')
            directory(ROOT / relative)
            data = read_file(ROOT / relative / 'manifest.json', 65536)
        except FileNotFoundError:
            if entry['lifecycle'] not in ('creating', 'createFailed'):
                reject('CREATE_MANIFEST_INVALID')
            # An incomplete instance may already have listeners; never guess its missing port plan.
            blockers.append('EXISTING_PORT_PLAN_INCOMPLETE')
            continue
        manifest = json.loads(data)
        if (type(manifest.get('schemaVersion')) is not int or manifest.get('schemaVersion') != 1 or manifest.get('hostId') != host_id
                or manifest.get('instanceId') != instance_id
                or manifest.get('composeProject') != 'fh_' + instance_id.replace('-', '')
                or type(manifest.get('cavesEnabled')) is not bool):
            reject('CREATE_MANIFEST_INVALID')
        snapshots.append((ROOT / relative / 'manifest.json', data))
        if sum(len(value) for _, value in snapshots) + len(before) + len(host_before) > 4 * 1024 * 1024:
            reject('CREATE_PROBE_LIMIT')
        ports = manifest.get('ports')
        if not isinstance(ports, list) or len(ports) > 12:
            blockers.append('EXISTING_PORT_PLAN_INCOMPLETE')
            continue
        expected = {(shard, purpose) for shard in (['master','caves'] if manifest['cavesEnabled'] else ['master'])
                    for purpose in ('game','steamAuth','steamMaster')}
        actual = set()
        for item in ports:
            used.add(read_port(item, host_id))
            key = item['shard'], item['purpose']
            if key in actual:
                reject('CREATE_PORT_STATE_INVALID')
            actual.add(key)
        if actual != expected:
            blockers.append('EXISTING_PORT_PLAN_INCOMPLETE')
    return used, blockers, (before, host_before, snapshots)


def run(command):
    environment = dict(os.environ)
    for name in ('DOCKER_CONTEXT','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH','DOCKER_API_VERSION','DOCKER_CONFIG'):
        environment.pop(name, None)
    environment.update({'LC_ALL':'C','DOCKER_HOST':'unix:///var/run/docker.sock'})
    result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, timeout=10, check=False,
                            env=environment)
    if len(result.stdout) > 1024 * 1024:
        reject('CREATE_PROBE_LIMIT')
    return result


def environment():
    blockers, used = [], set()
    complete = True
    try:
        for protocol in ('udp','tcp'):
            for suffix in ('','6'):
                with (PROC_NET / (protocol + suffix)).open('r', encoding='ascii') as source:
                    lines = source.read(4 * 1024 * 1024 + 1)
                if len(lines) > 4 * 1024 * 1024:
                    reject('CREATE_PROBE_LIMIT')
                if not lines.strip():
                    reject('CREATE_SOCKET_INVALID')
                for line in lines.splitlines()[1:]:
                    fields = line.split()
                    if len(fields) < 2:
                        reject('CREATE_SOCKET_INVALID')
                    number = int(fields[1].rsplit(':', 1)[1], 16)
                    if not 0 <= number <= 65535:
                        reject('CREATE_SOCKET_INVALID')
                    if number:
                        used.add((protocol, number))
    except (OSError, ValueError, IndexError):
        complete = False
        blockers.append('PORT_EVIDENCE_UNAVAILABLE')
    engine_version, compose_version = None, None
    try:
        docker = run(['docker','version','--format','{{.Server.Os}} {{.Server.Arch}} {{.Server.Version}}'])
        fields = docker.stdout.strip().split()
        if docker.returncode != 0 or len(fields) != 3:
            blockers.append('DOCKER_UNAVAILABLE')
            complete = False
        elif fields[0] != 'linux' or fields[1] not in ('amd64','x86_64'):
            blockers.append('DOCKER_PLATFORM_UNSUPPORTED')
            complete = False
        elif re.fullmatch(r'[a-zA-Z0-9.+_-]{1,64}', fields[2]):
            engine_version = fields[2]
        else:
            reject('CREATE_DOCKER_RESPONSE_INVALID')
        compose = run(['docker','compose','version','--short'])
        compose_match = re.fullmatch(r'v?([0-9]+)\.[0-9]+\.[a-zA-Z0-9.+_-]+', compose.stdout.strip())
        if (compose.returncode != 0 or len(compose.stdout.strip()) > 64
                or compose_match is None or int(compose_match[1]) < 2):
            blockers.append('COMPOSE_UNAVAILABLE')
        else:
            compose_version = compose.stdout.strip()
        if engine_version:
            containers = run(['docker','container','ls','--all','--quiet','--no-trunc'])
            ids = containers.stdout.splitlines()
            if containers.returncode != 0 or len(ids) > 400 or any(not re.fullmatch(r'[0-9a-f]{64}', item) for item in ids):
                reject('CREATE_DOCKER_RESPONSE_INVALID')
            if ids:
                # Inspect only published ports; never serialize environment variables or secrets.
                bindings = run(['docker','container','inspect','--format','{{json .HostConfig.PortBindings}}',*ids])
                rows = bindings.stdout.splitlines()
                if bindings.returncode != 0 or len(rows) != len(ids):
                    reject('CREATE_DOCKER_RESPONSE_INVALID')
                for row in rows:
                    values = json.loads(row)
                    if values is None:
                        continue
                    if not isinstance(values, dict) or len(values) > 256:
                        reject('CREATE_DOCKER_RESPONSE_INVALID')
                    for key, addresses in values.items():
                        if not re.fullmatch(r'[0-9]{1,5}/(tcp|udp)', key):
                            continue
                        if addresses is None:
                            continue
                        if not isinstance(addresses, list) or len(addresses) > 16:
                            reject('CREATE_DOCKER_RESPONSE_INVALID')
                        for address in addresses:
                            number = address.get('HostPort') if isinstance(address, dict) else None
                            if not isinstance(number, str) or not number.isascii() or not number.isdigit() or not port(int(number)):
                                reject('CREATE_DOCKER_RESPONSE_INVALID')
                            used.add((key.rsplit('/',1)[1], int(number)))
    except (OSError, subprocess.SubprocessError, ValueError):
        complete = False
        blockers.append('DOCKER_UNAVAILABLE')
    return used, complete, blockers, engine_version, compose_version


def allocate(used, caves):
    used = set(used)
    planned = []
    for shard in (['master','caves'] if caves else ['master']):
        for purpose, first, last in [('game',11000,19999),
                                     ('steamAuth',8766,9765), ('steamMaster',27016,28015)]:
            candidate = next((number for number in range(first,last+1) if ('udp',number) not in used), None)
            if candidate is None:
                return []
            used.add(('udp',candidate))
            planned.append({'shard':shard,'purpose':purpose,'protocol':'udp','bindAddress':'0.0.0.0',
                            'hostPort':candidate,'containerPort':candidate})
    return planned


def inspect(request):
    host_id = request.get('hostId')
    settings = request.get('input', {})
    caves_enabled = settings.get('cavesEnabled') if isinstance(settings, dict) else None
    if (not valid_id(host_id) or type(request.get('expectedRegistryRevision')) is not int
            or not isinstance(settings,dict)
            or type(caves_enabled) is not bool or settings.get('portPolicy') != 'auto'
            or type(settings.get('autostart')) is not bool or type(settings.get('startAfterCreate')) is not bool
            or (not settings['autostart'] and settings['startAfterCreate'])
            ):
        reject('CREATE_INPUT_INVALID')
    used, blockers, snapshot = registry_ports(host_id, request['expectedRegistryRevision'])
    live, complete, conditions, docker, compose = environment()
    blockers += conditions
    if not os.access(ROOT, os.W_OK | os.X_OK) or os.statvfs(ROOT).f_flag & os.ST_RDONLY:
        blockers.append('MANAGEMENT_ROOT_UNWRITABLE')
    ports = []
    if complete and not any(code in blockers for code in ('PORT_RESERVATIONS_MISSING','EXISTING_PORT_PLAN_INCOMPLETE')):
        ports = allocate(used | live, caves_enabled)
        if not ports:
            blockers.append('PORT_PLAN_EXHAUSTED')
    before, host_before, manifests = snapshot
    if read_file(ROOT/'registry.json',1024*1024) != before or read_file(ROOT/'host.json',65536) != host_before:
        reject('CREATE_METADATA_CHANGED')
    if any(read_file(path,65536) != data for path,data in manifests):
        reject('CREATE_METADATA_CHANGED')
    return {'schemaVersion':1,'hostId':host_id,'registryRevision':request['expectedRegistryRevision'],
            'capturedAtUnixSeconds':int(time.time()),'ports':ports,'blockers':sorted(set(blockers)),
            'availableBytes':shutil.disk_usage(ROOT).free,'dockerVersion':docker,'composeVersion':compose}


def main(request):
    try:
        response = {'ok':True,'data':inspect(request)}
    except Rejected as error:
        response = {'ok':False,'code':str(error)}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        response = {'ok':False,'code':'CREATE_PROBE_FAILED'}
    print(json.dumps(response,separators=(',',':')),flush=True)
