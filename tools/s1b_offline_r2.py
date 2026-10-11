"""Reproduce the offline R2 suites in owned evidence paths; no target build or simulator."""
import datetime
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

W = Path(__file__).resolve().parents[1]
E = W / 'evidence/GateCS1bR1'
R = E / 'r2'
C4 = W.parent / 'c4_droid_20261005/tools'
COMPILER = W / 'target/sw/host/runtime/libbingo/mini_compiler'
WORKLOAD = W / 'target/sw/host/apps/offload_bingo_hw/single_chip/workloads/dma_chain_sdc_recover_2cluster'
PLATFORM = W / 'evidence/BuildS1bR2/images/dma_chain_sdc_recover_2cluster_recover/HeMAiA/target/sw/shared/platform/generated/occamy.h'
SNITCH = W/'evidence/PreparationS1bR1/HeMAiA/.bender/git/checkouts/snitch_cluster-9a64e6b3572c7a8d'
LAYER = Path('/volume1/users/r1015708/containers/storage/overlay/9a70ed46dca534974a5f77c2d0ab0cc4370a7cae19ed1b3a3528ed042026b4e1/diff/usr/local/lib/python3.12/dist-packages')


def occamy_environment(env):
    # Existing cached dependencies only. No container process, install or network.
    root=R/'occamy_checkout'
    root.mkdir()
    copies=['util/occamygen','util/solder','docs/schema','target/rtl/cfg','hw/occamy']
    source_bindings={}
    for name in copies:
        shutil.copytree(W/name,root/name,ignore=shutil.ignore_patterns('__pycache__'))
        for path in (W/name).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                assert path.read_bytes()==(root/path.relative_to(W)).read_bytes()
                source_bindings[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
    dependency=root/'deps/snitch_cluster'
    dependency.mkdir(parents=True)
    for name in ('util','docs','hw'):
        (dependency/name).symlink_to(SNITCH/name,target_is_directory=True)
    # Occamy normalizes cluster sizing in place; keep that operation owned.
    shutil.copytree(SNITCH/'target/snitch_cluster/cfg',dependency/'target/snitch_cluster/cfg')
    package_files={}
    for name in ('jsonschema','jsonschema_specifications','referencing','rpds','attrs','attr','mako','markupsafe','hjson'):
        for path in (LAYER/name).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                package_files[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
    for path in (LAYER/'typing_extensions.py',Path('/users/students/r1015708/.local/lib/python3.11/site-packages/jsonref.py')):
        package_files[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ('util/clustergen','util/snaxgen','docs/schema'):
        for path in (SNITCH/name).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                source_bindings[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
    (E/'occamygen_inputs_r2.json').write_text(json.dumps(dict(
        scope='Unmodified current generator/tests in owned source copy; existing dependencies read-only',
        interpreter='/usr/bin/python3.12',cached_package_root=str(LAYER),
        product_and_dependency_inputs=source_bindings,package_files=package_files),indent=2)+'\n')
    adapted=dict(env)
    adapted['PYTHONPATH']=os.pathsep.join([str(root/'util/occamygen'),str(root/'util'),
        str(LAYER),'/users/students/r1015708/.local/lib/python3.11/site-packages',env['PYTHONPATH']])
    return root,adapted

def main():
    for package in ('networkx', 'matplotlib', 'numpy', 'hjson', 'jsonref'):
        assert importlib.util.find_spec(package) is not None, package
    assert importlib.metadata.version('jsonref') == '1.1.0'
    (R/'tmp').mkdir(exist_ok=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONHASHSEED='0',
               MPLCONFIGDIR=str(R/'mpl_cache'), MPLBACKEND='Agg', TMPDIR=str(R/'tmp'))
    env['PYTHONPATH'] = os.pathsep.join(map(str, [R/'fixtures', W/'tools',
        W/'target/sim/automation/test', W/'util/automation_scripts', COMPILER, C4,
        W/'evidence/PreparationS1bR1/HeMAiA/.bender/git/checkouts/snitch_cluster-9a64e6b3572c7a8d/util/clustergen',
        W/'util']))
    suites = [
      ('product_generation', [sys.executable,'-B',str(WORKLOAD/'main_bingo.py'),
          '--output_dir',str(R/'product_generation/generated'), '-c',str(WORKLOAD/'params.hjson'),
          '--platformcfg',str(PLATFORM)]),
      ('compiler', [sys.executable,'-B','-m','unittest','discover','-s',str(COMPILER/'tests'),'-v']),
      ('checker_driver', [sys.executable,'-B','-m','unittest','discover','-s',str(W/'target/sim/automation/test'),'-v']),
      ('observer_reference', [sys.executable,'-B','-m','unittest','discover','-s',str(W/'tools'),'-p','test_s1b_*.py','-v']),
      ('occamygen', [sys.executable,'-B','-m','unittest','discover','-s',str(W/'util/occamygen/tests'),'-v']),
      ('expression_fixture', [sys.executable,'-B',str(R/'fixtures/s1b_fixture_r6.py')]),
      ('model_contracts', [sys.executable,'-B',str(R/'fixtures/s1b_tests_r6.py')]),
      ('actual_graph_byte_oracle', [sys.executable,'-B',str(R/'fixtures/s1b_graph_implementation_r3.py')]),
      ('native_C_semantic_mutants', [sys.executable,'-B',str(R/'fixtures/s1b_mutants_r1.py')]),
      ('standard_library_byte_schedule', [sys.executable,'-S','-B',str(R/'fixtures/s1b_stdlib_r6.py')]),
    ]
    rows=[]
    if len(sys.argv) > 1:
        rows = json.loads((E/'offline_suites_r2.json').read_text())
        assert set(sys.argv[1:]) <= {name for name, _ in suites}
        suites = [(name, cmd) for name, cmd in suites if name in sys.argv[1:]]
    for name, command in suites:
        log = E / (name+'_r2.log')
        print('RUN',name,flush=True)
        started=datetime.datetime.now().astimezone().isoformat()
        suite_env=env
        if name=='occamygen':
            occamy_root,suite_env=occamy_environment(env)
            command=['/usr/bin/python3.12','-B','-m','unittest','discover','-s',str(occamy_root/'util/occamygen/tests'),'-v']
        with log.open('x') as stream:
            result=subprocess.run(command,cwd=W,env=suite_env,stdout=stream,stderr=subprocess.STDOUT,timeout=600)
        data=log.read_text();match=re.search(r'Ran (\d+) tests?',data)
        skipped=re.search(r'OK \(skipped=(\d+)\)',data)
        row=dict(name=name,status='PASS' if result.returncode==0 else 'FAIL',command=command,
                 started=started,returncode=result.returncode,log=str(log.relative_to(E)),
                 log_sha256=hashlib.sha256(log.read_bytes()).hexdigest())
        if match: row.update(tests_run=int(match[1]),skipped=int(skipped[1]) if skipped else 0)
        if name=='occamygen': row['jsonref_version']=importlib.metadata.version('jsonref')
        rows = [old for old in rows if old['name'] != name] + [row]
        (E/'offline_suites_r2.json').write_text(json.dumps(rows,indent=2)+'\n')
        print(name,row['status'],row.get('tests_run',''),flush=True)
        if result.returncode:
            print(data[-5000:],flush=True)
    return int(any(row['status'] != 'PASS' for row in rows))

if __name__=='__main__':
    raise SystemExit(main())
