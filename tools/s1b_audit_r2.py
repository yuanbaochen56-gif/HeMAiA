"""Offline source, evidence, generated-image and reference-coverage audit."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys

W=Path(__file__).resolve().parents[1]
E=W/'evidence/GateCS1bR1'
sys.path[:0]=[str(W/'tools'),str(W/'target/sim/automation/test')]
from s1b_reference_control import reference_prediction


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(name,value):
    (E/name).write_text(json.dumps(value,indent=2)+'\n')


def main():
    before=json.loads((E/'before_r2.json').read_text())
    changed=[name for name,checksum in before['files'].items() if sha(W/name)!=checksum]
    main_path='target/sw/host/apps/offload_bingo_hw/single_chip/workloads/dma_chain_sdc_recover_2cluster/main_bingo.py'
    tracked=subprocess.check_output(['git','ls-files','-z'],cwd=W).decode().split('\0')
    tracked_changed=[p for p in tracked if p and sha(W/p)!=before['files'][p]]
    assert set(tracked_changed)=={'target/sim/automation/test/bingo_sdc_recover_check.py',
                                 'target/sim/automation/test/test_bingo_sdc_recover.py',main_path}
    old_source=subprocess.check_output(['git','show','HEAD:'+main_path],cwd=W)
    assert (W/main_path).read_bytes()==old_source.replace(b'"char hex[129];"',b'"char hex[129] = {0};"')
    preserved=before['prior_evidence']
    assert all(sha(W/name)==checksum for name,checksum in preserved.items()), 'Prior R1 evidence changed'
    prior_sources=json.loads((E/'changed_files.json').read_text())
    new=['tools/s1b_build_r3.py','tools/s1b_reference_control.py','tools/test_s1b_reference_control.py',
         'tools/s1b_offline_r2.py','tools/s1b_audit_r2.py',main_path]
    candidates=sorted(set(prior_sources)|set(new))
    for name in candidates:
        if name.endswith('.py'): ast.parse((W/name).read_text(),filename=name)
    initial_observer=W/'tools/s1b_readonly_observer_r4.sv'
    assert 'assert(C == 3 && CL == 2 && T == 4 && IDW == 12 && DW == 62)' in initial_observer.read_text()
    warning=json.loads((E/'wt_warning_root_cause.json').read_text())
    assertion_sources=[]
    for record in warning['sources']:
        if record['path'].endswith(('.sv','uart.c')):
            assert sha(record['path'])==record['sha256']
            assertion_sources.append(record)
    generated=E/'r2/product_generation/generated'
    old=W/'evidence/BuildS1bR2/images/dma_chain_sdc_recover_2cluster_recover/staged/generated'
    artifact_rows={}
    for path in old.iterdir():
        if not path.is_file(): continue
        data=path.read_bytes();actual=(generated/path.name).read_bytes()
        normalized=data.replace(b'char hex[129];',b'char hex[129] = {0};') if path.name=='offload_bingo_hw.h' else data
        assert actual==normalized,path.name
        artifact_rows[path.name]=dict(old_sha256=sha(path),new_sha256=sha(generated/path.name),
                                      identical=data==actual,only_authorized_delta=True)
    assert (generated/'offload_bingo_hw.h').read_text().count('char hex[129] = {0};')==10
    old_build=json.loads((W/'evidence/BuildS1bR2/progress.json').read_text())
    for image in old_build['images']:
        root=Path(image['output']);task,=root.glob('task_*')
        assert sha(root/'test_cfg_unpatched.bin')==image['unpatched_sha256']
        for part,hashes in image['part_hashes'].items():
            assert {str(p.relative_to(task/'bin'/part)):sha(p) for p in (task/'bin'/part).rglob('*')
                    if p.is_file()}==hashes
    image_delta=dict(status='PASS_SOURCE_AND_GENERATION',authorized_product_change=main_path,
        source_before_sha256=hashlib.sha256(old_source).hexdigest(),source_after_sha256=sha(W/main_path),
        delta='char hex[129]; -> char hex[129] = {0}; at all ten post-execution dumps',
        all_other_product_sources_and_RTL_unchanged=True,artifacts=artifact_rows,
        existing_17_image_artifacts_preserved=True,new_target_binary_built=False,
        binary_delta_validation='Handoff BuildS1bR3: rebuild; sixteen legacy images and recovery device image must be byte-identical; generated host C only ten initializers')
    write('product_image_delta_r2.json',image_delta)
    sealed=json.loads((W/'evidence/PredictionsS1bR4/predictions.json').read_text())
    prediction=reference_prediction(sealed)
    tasks=lambda p:{e['task'] for e in p['model']['events'] if e['kind']=='DISPATCH'}
    observed=tasks(prediction);required=set().union(*(tasks(p) for p in sealed['predictions'].values()))
    assert required<=observed and {2,3,6,7}<=observed
    write('reference_coverage_r2.json',dict(status='PASS_OFFLINE_MODEL',basis='Same frozen graph/descriptors and unchanged causal model; recovery-only runtime control',
        sdc_enable=1,recovery_enable=0,reference_executing_tasks=sorted(observed),
        required_scenario_tasks=sorted(required),missing_reference_tasks=sorted(required-observed),
        reference_scene='s1b_reference_recovery_off',reference_control=prediction['control'],
        actual_L_ref_captured=False,physical_validation_pending=True))
    write('wt_warning_resolution_r2.json',dict(status='AUTHORIZED_FIX_APPLIED_PHYSICAL_CHECK_PENDING',
        warnings_in_retained_R1_run=10,source=main_path,source_sha256=sha(W/main_path),
        full_129_byte_initialization=True,generated_dump_initializers=10,
        cache_assertion_changed=False,any_product_RTL_changed=False,warning_waiver=False,
        source_bindings=assertion_sources,new_simulator_run=False,
        handoff_requirement='Zero WT invalid-read warnings; the runner still stops on any such warning'))
    suites=json.loads((E/'offline_suites_r2.json').read_text())
    assert len(suites)==10 and all(row['status']=='PASS' for row in suites)
    assert all(sha(E/row['log'])==row['log_sha256'] for row in suites)
    cases={'model_contracts':139,'actual_graph_byte_oracle':39,'native_C_semantic_mutants':8,
           'standard_library_byte_schedule':102}
    for row in suites:
        if row['name'] in cases: row['cases']=cases[row['name']]
    assert json.loads((E/'r2/model_contracts/model_results.json').read_text())['passed']==139
    assert len(json.loads((E/'r2/actual_graph/result.json').read_text())['cases'])==39
    assert len(json.loads((E/'r2/mutants/result.json').read_text())['semantic_mutants'])==8
    assert json.loads((E/'r2/model_contracts/stdlib_results.json').read_text())['counterexample_checks']==8
    for name in ['BuildS1bR3','PreparationS1bR5','PredictionsS1bR5','SystemS1bR5']:
        assert not (W/'evidence'/name).exists(), ('Handoff phase was executed',name)
    assert not (W/'results/SystemS1bR5').exists()
    assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=W,text=True).strip()==before['head']
    assert subprocess.check_output(['git','branch','--show-current'],cwd=W,text=True).strip()==before['branch']
    subprocess.run(['git','diff','--check'],cwd=W,check=True)
    write('offline_suites_r2.json',suites)
    write('source_scope_verification_r2.json',dict(status='PASS',changed_since_R1_baseline=changed,
        tracked_changed_since_R1_baseline=tracked_changed,sole_product_source_delta=main_path,
        prior_R1_evidence_artifacts_preserved=len(preserved),other_tracked_sources_preserved=len([p for p in tracked if p])-3,
        changed_Python_sources_parse='PASS',RTL_assertions_unchanged=True,
        cache_assertion_unchanged=True,Markdown_outside_evidence_unchanged=True))
    write('self_check_r2.json',dict(status='READY_FOR_HANDOFF',head=before['head'],branch=before['branch'],
        offline_suites='PASS',product_fix='Full hex[129] initialization only',
        reference='SDC ON/recovery OFF, all scenario tasks covered in unchanged causal model',
        release='extra=max(0,latency-L_ref)<=1000000ps; original earliest real readiness/START/ingress/HOL/skip semantics',
        freeze='Exclusive files, fsync, 0444; pinned SHA256 plus sidecar verified before coverage/compare/scene launch',
        gates=dict(G_consume='Retained; dual producers/compare dependencies and canonical publication checked',
            G_vote='Retained; independent bytes and eight executed C mutants',
            G_once='Retained; one third/commit/report and duplicate rejection',
            G_status='Retained; raw status preserved and safe rejection unchanged',
            G_release='Approved relative rule; exact boundary, missing task/hash/readiness negatives pass',
            G_finite='Retained; finite completion and return/word/envelope budgets unchanged'),
        real_L_ref_captured=False,physical_gates_claimed_PASS=False,blockers=[],
        pending_handoff=['Fresh target rebuild and binary delta gates','62-bit input-only observer compile/elaboration',
                         'Reference control/cache visibility at real post-init point, zero WT warnings',
                         'Physical L_ref freeze and all original seven scenes/other gates'],
        actions=dict(commits=0,simulators=0,containers=0,target_image_builds=0,pushes=0,merges=0,subagents=0)))
    write('changed_files_r2.json',{name:sha(W/name) for name in candidates})
    print('READY_FOR_HANDOFF:',len(candidates),'implementation files;',len(preserved),'R1 evidence artifacts preserved')


if __name__=='__main__':
    main()
