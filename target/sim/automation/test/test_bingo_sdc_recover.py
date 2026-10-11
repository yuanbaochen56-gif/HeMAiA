import copy
import unittest

from bingo_sdc_recover_check import evaluate_recovery, recovery_records
from bingo_sdc_recover_scenarios import recovery_scenarios
from test_bingo_test_cfg import watchdog

GRAPH = dict(roles={str(i):role for i,role in enumerate(
    ('init','P0','S0','compare0','resolver0','P1','S1','compare1','resolver1','consumer'))},
    stages=[dict(transaction=tx,primary=1+4*tx,shadow=2+4*tx,compare=3+4*tx,
                 resolver=4+4*tx,status_index=tx,status_offset_bytes=4*tx) for tx in (0,1)],
    status_contract=dict(indices=[0,1]),status_addresses=[0x80020000,0x80020004])
REPORT = dict(nodes=[dict(selected=True,node_id=s['primary'],shadow_id=s['shadow'],
    compare_id=s['compare'],status_index=s['status_index']) for s in GRAPH['stages']])


def fixture(scene):
    enabled=scene['sdc_enable']; fault_tx=scene['recovery_fault_tx']
    shadow=scene['recovery_fault_shadow']; log=[]; uart=[]; eligibility={}; stamp=1000000
    def event(kind,task,core=2,cluster=0):
        nonlocal stamp
        stamp+=200000
        log.append(f'[BINGO_{kind}] {stamp} chip=0 task={task} core={core} cluster={cluster}')
        if kind=='DISPATCH': eligibility[str(task)]=stamp-100000
    event('DISPATCH',10); event('DONE',10); event('DISPATCH',0);event('DONE',0)
    original=bytes(((i*37+11)^(i>>2))&255 for i in range(64)); buffers={'input':original}; src=original
    for tx,st in enumerate(GRAPH['stages']):
        event('DISPATCH',st['primary'],1);event('DONE',st['primary'],1)
        if enabled:
            for task in (st['shadow'],st['compare']):
                event('DISPATCH',task,1,1);event('DONE',task,1,1)
        event('DISPATCH',st['resolver']);event('DONE',st['resolver'])
        mismatch=bool(enabled and fault_tx==tx)
        row=dict(tx=tx,enabled=enabled,code=(18 if mismatch else 0) if enabled else 0xFFFFFFFF,
            detect_valid=enabled,third=int(mismatch),selected=2 if mismatch and not shadow else 1,
            commit=1,reports=1,outcome=3 if mismatch else 2 if enabled else 1,
            failure=0,phase=2,status_addr=GRAPH['status_addresses'][tx],rstart=100,status_cc=110,
            third_start=120 if mismatch else 0,third_end=130 if mismatch else 0,
            vote_end=140,publish_end=150)
        uart.append('[Host] SDC_RECOVER '+' '.join(f'{k}={hex(v) if k=="status_addr" else v}' for k,v in row.items()))
        p,s=bytearray(src),bytearray(src)
        if tx==fault_tx:
            if shadow and enabled:s[17]^=4
            elif not shadow:p[17]^=4
        canonical=src if enabled else bytes(p)
        buffers.update({f'P{tx}':bytes(p),f'S{tx}':bytes(s),f'T{tx}':src if mismatch else bytes(64),f'C{tx}':canonical})
        src=canonical
    event('DISPATCH',9);event('DONE',9)
    buffers['sink']=src
    for k,v in buffers.items():uart.append(f'[Host] SDC_BUFFER name={k} data={v.hex()}')
    if fault_tx is not None:
        uart.append(f'[BINGO_SDC_INJECT] gid={scene["fault_sdc_gid"]} cluster={scene["fault_sdc_cluster"]} core=1 offset=17 mask=4')
    if enabled and fault_tx is not None:
        st=GRAPH['stages'][fault_tx]
        uart.append(f'[Host] SDC mismatch: primary={st["primary"]} shadow={st["shadow"]} compare={st["compare"]} code=18')
    uart.append(f'[Host] SDC report: {int(bool(enabled and fault_tx is not None))} mismatches')
    uart.append('[Host] Check [SDC_RECOVER_sink]: '+('PASS' if scene['recovery_correct'] else 'FAIL'))
    uart.append('[Host] Bingo status: replay_stuck=0 remote_done_mismatch=0 link_error=0 fenced=0x0 dead_suspect=0x0 remote_timeout=0 cerf=0x1 cerf_fb_en=0x0 cerf_fb_evt=0x0')
    uart.append('[Host] Offload Finish with ret = '+('0' if scene['recovery_correct'] else '2'))
    log.append('Simulation of chip_0_0 '+('finished successfully at 9000000' if scene['recovery_correct'] else 'finished with status 5 at 9000000'))
    log.append('All chips finished successfully' if scene['recovery_correct'] else '** Fatal: All chips finished with errors')
    sc=copy.deepcopy(scene);sc['recovery_eligibility']=eligibility
    return sc,'\n'.join(log)+'\n','\n'.join(uart)+'\n'


class RecoveryCheckerTests(unittest.TestCase):
    def scenes(self):return recovery_scenarios(dict(t1=True),REPORT,GRAPH)

    def test_flag_guards(self):
        valid='-DBINGO_SDC -DBINGO_SDC_QUIET -DBINGO_SDC_RECOVER'
        self.assertIn('-DBINGO_SDC_RECOVER',watchdog.t1_image_flags(dict(t1=True,image_flags=valid)))
        for flags,t1 in [('-DBINGO_SDC_RECOVER',True),('-DBINGO_SDC -DBINGO_SDC_RECOVER',True),(valid,False)]:
            with self.assertRaises(ValueError):watchdog.t1_image_flags(dict(t1=t1,image_flags=flags))

    def test_six_semantic_fixtures(self):
        for name,scene in self.scenes().items():
            with self.subTest(name=name):self.assertEqual(evaluate_recovery(*fixture(scene)),[])

    def test_reject_semantic_evidence_mutations(self):
        sc,log,uart=fixture(self.scenes()['s1b_stage0_primary_on'])
        cases=[(log,uart.replace('third=1','third=0')),
               (log,uart.replace('selected=2','selected=1')),
               (log,uart.replace('commit=1','commit=0',1)),
               (log,uart.replace('code=18','code=0')),
               (log.replace('[BINGO_DONE] 3000000 chip=0 task=4','[BINGO_DONE] 9900000 chip=0 task=4'),uart),
               (log,uart+uart.splitlines()[0]+'\n'),
               (log,uart.replace('name=C0','name=C2'))]
        # A reader before resolver DONE is rejected independent of its timestamp.
        cases[4]=(log.replace('task=4 core=2','task=40 core=2'),uart)
        for mutated_log,mutated_uart in cases:
            self.assertTrue(evaluate_recovery(sc,mutated_log,mutated_uart))
        no_ready=copy.deepcopy(sc);no_ready.pop('recovery_eligibility')
        self.assertTrue(evaluate_recovery(no_ready,log,uart))
        too_late=copy.deepcopy(sc);too_late['recovery_eligibility']['4']=0
        self.assertTrue(evaluate_recovery(too_late,log,uart))

    def test_reject_missing_address_binding_and_skip_done(self):
        sc,log,uart=fixture(self.scenes()['s1b_healthy_off'])
        sc['recovery_graph'].pop('status_addresses')
        self.assertTrue(evaluate_recovery(sc,log,uart))
        sc,log,uart=fixture(self.scenes()['s1b_healthy_off'])
        self.assertTrue(evaluate_recovery(sc,log+'[BINGO_DONE] 999 chip=0 task=2 core=1 cluster=1\n',uart))
        with self.assertRaises(ValueError):recovery_records('[Host] SDC_RECOVER tx=0 tx=0\n')

    def test_finite_negative_path_requires_no_done_or_consumers(self):
        sc,log,uart=fixture(self.scenes()['s1b_healthy_on'])
        sc['recovery_negative']=True
        # Retain only entry/init/private stage0/compare and resolver dispatch.
        log='\n'.join(line for line in log.splitlines() if '[BINGO_' in line and
                     not any(f'task={i} ' in line for i in (5,6,7,8,9)) and
                     not ('[BINGO_DONE]' in line and 'task=4 ' in line))+'\n'
        log+='Simulation of chip_0_0 finished with status 5 at 9000000\n** Fatal: All chips finished with errors\n'
        row=recovery_records(uart)[0]
        row.update(code=0xFFFFFFFF,third=0,selected=0,commit=0,outcome=4,failure=2,phase=3,
                   vote_end=0,publish_end=0)
        uart='[Host] SDC_RECOVER '+' '.join(f'{k}={hex(v) if k=="status_addr" else v}' for k,v in row.items())+'\n'
        uart+='[Host] Bingo status: replay_stuck=0 fenced=0x0\n[Host] Offload Finish with ret = 2\n'
        sc['recovery_eligibility']={k:v for k,v in sc['recovery_eligibility'].items() if k in ('10','0','1','2','3','4')}
        self.assertEqual(evaluate_recovery(sc,log,uart),[])
        self.assertTrue(evaluate_recovery(sc,log+'[BINGO_DONE] 8000000 chip=0 task=4 core=2 cluster=0\n',uart))
        self.assertTrue(evaluate_recovery(sc,log+'[BINGO_DISPATCH] 8000001 chip=0 task=5 core=1 cluster=0\n',uart))


if __name__=='__main__':unittest.main()
