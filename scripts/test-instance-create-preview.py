"""Exercise production creation planning against isolated metadata and mocked Docker."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

PROJECT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('creation_preview',PROJECT/'remote-scripts/probes/instance-create-preview.py')
PROBE=importlib.util.module_from_spec(spec)
spec.loader.exec_module(PROBE)
HOST='11111111-1111-4111-8111-111111111111'
INSTANCE='22222222-2222-4222-8222-222222222222'

class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='instance-preview-')
        self.root=Path(self.temp.name)
        self.management=self.root/'management'
        self.management.mkdir(mode=0o700)
        self.proc=self.root/'net'
        self.proc.mkdir()
        for protocol in ('udp','udp6','tcp','tcp6'):
            (self.proc/protocol).write_text('sl local_address rem_address st\n')
        self.registry={'schemaVersion':1,'hostId':HOST,'registryRevision':3,'instances':[],'portReservations':[]}
        self.host=self.management/'host.json'
        self.host.write_text(json.dumps({'schemaVersion':1,'hostId':HOST}))
        self.save_registry()
        self.request={'hostId':HOST,'expectedRegistryRevision':3,'input':{'displayName':'生存一服','autostart':True,'startAfterCreate':True,'portPolicy':'auto','cavesEnabled':True}}
        self.patches=[patch.object(PROBE,'ROOT',self.management),patch.object(PROBE,'PROC_NET',self.proc),patch.object(PROBE.subprocess,'run',side_effect=self.docker)]
        for item in self.patches:item.start()

    def tearDown(self):
        for item in reversed(self.patches):item.stop()
        self.temp.cleanup()

    def save_registry(self):
        (self.management/'registry.json').write_text(json.dumps(self.registry))

    def docker(self,args,**_kwargs):
        if args[:2]==['docker','version']:text='linux amd64 29.0.0\n'
        elif args[:3]==['docker','compose','version']:text='2.40.0\n'
        elif args[:3]==['docker','container','ls']:text=''
        else:raise AssertionError('Unexpected external command')
        return subprocess.CompletedProcess(args,0,text)

    def test_empty_host_proposes_full_same_number_plan_without_writes(self):
        before={str(p.relative_to(self.management)):p.read_bytes() for p in self.management.rglob('*') if p.is_file()}
        result=PROBE.inspect(self.request)
        self.assertEqual(result['blockers'],[])
        self.assertEqual(len(result['ports']),6)
        self.assertEqual([p['hostPort'] for p in result['ports']],[11000,8766,27016,11001,8767,27017])
        self.assertTrue(all(p['hostPort']==p['containerPort'] for p in result['ports']))
        self.assertEqual(before,{str(p.relative_to(self.management)):p.read_bytes() for p in self.management.rglob('*') if p.is_file()})

    def add_instance(self):
        directory=self.management/'instances'/INSTANCE
        directory.mkdir(parents=True)
        ports=PROBE.allocate(set(),False)
        manifest={'schemaVersion':1,'hostId':HOST,'instanceId':INSTANCE,'composeProject':'fh_'+INSTANCE.replace('-',''),'cavesEnabled':False,'ports':ports}
        (directory/'manifest.json').write_text(json.dumps(manifest))
        self.registry['instances']=[{'instanceId':INSTANCE,'lifecycle':'ready'}]
        self.registry['portReservations']=[{'hostId':HOST,'instanceId':INSTANCE,**p} for p in ports]
        self.save_registry()
        return manifest,directory

    def test_stopped_instance_and_live_ipv6_ports_remain_unavailable(self):
        self.add_instance()
        # IPv6 socket and an ordinary IPv4 listener must both be considered.
        (self.proc/'udp6').write_text('sl local_address rem_address st\n0: '+('0'*32)+':2AF9 '+('0'*32)+':0000 07\n')
        result=PROBE.inspect(self.request)
        master=next(p for p in result['ports'] if p['shard']=='master' and p['purpose']=='game')
        self.assertEqual(master['hostPort'],11002)
        self.assertNotIn(8766,[p['hostPort'] for p in result['ports']])

    def test_docker_port_bindings_are_checked_even_without_proxy_sockets(self):
        def docker(args,**kwargs):
            if args[:3]==['docker','container','ls']:return subprocess.CompletedProcess(args,0,'a'*64+'\n')
            if args[:3]==['docker','container','inspect']:
                self.assertIn('{{json .HostConfig.PortBindings}}',args)
                return subprocess.CompletedProcess(args,0,json.dumps({'11000/udp':[{'HostIp':'0.0.0.0','HostPort':'11000'}]})+'\n')
            return self.docker(args,**kwargs)
        with patch.object(PROBE.subprocess,'run',side_effect=docker):result=PROBE.inspect(self.request)
        self.assertEqual(result['ports'][0]['hostPort'],11001)

    def test_public_range_exhaustion_and_caves_disabled(self):
        used={('udp',number) for number in range(11000,20000)}
        self.assertEqual(PROBE.allocate(used,True),[])
        self.assertEqual(len(PROBE.allocate(set(),False)),3)
        self.assertEqual(PROBE.allocate({('tcp',11000)},False)[0]['hostPort'],11000)

    def test_incomplete_existing_ports_never_produce_an_optimistic_plan(self):
        manifest,directory=self.add_instance()
        manifest['ports']=[]
        (directory/'manifest.json').write_text(json.dumps(manifest))
        result=PROBE.inspect(self.request)
        self.assertEqual(result['ports'],[])
        self.assertIn('EXISTING_PORT_PLAN_INCOMPLETE',result['blockers'])

    def test_missing_reservations_or_socket_evidence_block_port_suggestions(self):
        self.registry.pop('portReservations')
        self.save_registry()
        result=PROBE.inspect(self.request)
        self.assertEqual(result['ports'],[])
        self.assertIn('PORT_RESERVATIONS_MISSING',result['blockers'])
        self.registry['portReservations']=[]
        self.save_registry()
        (self.proc/'udp').unlink()
        result=PROBE.inspect(self.request)
        self.assertEqual(result['ports'],[])
        self.assertIn('PORT_EVIDENCE_UNAVAILABLE',result['blockers'])

    def test_wrong_host_revision_and_unsafe_paths_reject(self):
        for changes,expected in [({'hostId':INSTANCE},'HOST_IDENTITY_MISMATCH'),({'expectedRegistryRevision':2},'REGISTRY_CHANGED')]:
            with self.assertRaises(PROBE.Rejected) as failure:PROBE.inspect({**self.request,**changes})
            self.assertEqual(str(failure.exception),expected)
        manifest,directory=self.add_instance()
        original=directory/'manifest.json'
        data=original.read_bytes();original.unlink()
        outside=self.root/'outside';outside.write_bytes(data)
        original.symlink_to(outside)
        with self.assertRaises(PROBE.Rejected):PROBE.inspect(self.request)

    def test_registry_changed_during_network_probe_is_rejected(self):
        def environment():
            self.registry['registryRevision']=4
            self.save_registry()
            return set(),True,[],'29.0.0','2.40.0'
        with patch.object(PROBE,'environment',side_effect=environment):
            with self.assertRaises(PROBE.Rejected) as failure:PROBE.inspect(self.request)
        self.assertEqual(str(failure.exception),'CREATE_METADATA_CHANGED')

    def test_docker_failure_never_means_port_availability(self):
        with patch.object(PROBE.subprocess,'run',side_effect=FileNotFoundError):result=PROBE.inspect(self.request)
        self.assertIn('DOCKER_UNAVAILABLE',result['blockers'])
        self.assertEqual(result['ports'],[])

    def test_read_only_root_and_future_compose_major_are_reported_correctly(self):
        from types import SimpleNamespace
        def docker(args,**kwargs):
            if args[:3]==['docker','compose','version']:
                return subprocess.CompletedProcess(args,0,'10.0.0\n')
            return self.docker(args,**kwargs)
        with patch.object(PROBE.subprocess,'run',side_effect=docker), patch.object(PROBE.os,'statvfs',return_value=SimpleNamespace(f_flag=os.ST_RDONLY)):
            # disk_usage also calls statvfs, so keep its real result as a separate fixture.
            with patch.object(PROBE.shutil,'disk_usage',return_value=SimpleNamespace(free=32*1024**3)):
                result=PROBE.inspect(self.request)
        self.assertEqual(result['composeVersion'],'10.0.0')
        self.assertIn('MANAGEMENT_ROOT_UNWRITABLE',result['blockers'])

if __name__=='__main__':unittest.main(verbosity=2)
