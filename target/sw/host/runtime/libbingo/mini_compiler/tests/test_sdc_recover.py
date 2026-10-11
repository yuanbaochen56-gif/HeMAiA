"""Actual C resolver semantics and the dependent recovery graph."""
import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import networkx as nx

ROOT = Path(__file__).resolve().parents[7]
COMPILER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(COMPILER))
from bingo_kernel_args import HostBingoKernelSdcRecoverInitArgs, HostBingoKernelSdcResolveCopyArgs
from test_cerf_fallback import compile_graph

HARNESS = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define BINGO_SDC_RECOVER
#define BINGO_SDC
#define BINGO_SDC_QUIET
#define BINGO_TEST_CFG 1
#define BINGO_RET_SUCC 0
#define BINGO_RET_FAIL 2
#define printf_safe printf
#include "host_sdc_recover.h"
static uint8_t src[64], p[64], shadow[64], third[64], canonical[64];
static uint32_t status[2];
static bingo_sdc_recovery_state_t states[2];
static __host_bingo_kernel_sdc_resolve_copy_args_t args;
static uint64_t addr(void *p) { return (uint64_t)(uintptr_t)p; }
static void setup(unsigned enabled) {
    for (unsigned i=0;i<64;++i) src[i]=(uint8_t)(((i*37+11)^(i>>2))&255);
    memcpy(p,src,64); memcpy(shadow,src,64);
    memset(third,0xa5,64); memset(canonical,0x5a,64); memset(states,0xcc,sizeof(states));
    __host_bingo_kernel_sdc_recover_init_args_t init={addr(&status[0]),addr(&status[1]),
        addr(states),2,enabled,0};
    assert(__host_bingo_kernel_sdc_recover_init(&init)==0);
    assert(states[0].detect_code==UINT32_MAX && states[1].transaction==1);
    assert(states[0].enabled==enabled && states[1].enabled==enabled);
    assert(status[0]==(enabled?UINT32_MAX:0) && status[1]==status[0]);
    args=(__host_bingo_kernel_sdc_resolve_copy_args_t){addr(src),addr(p),addr(shadow),
        addr(third),addr(canonical),addr(status),addr(states),64,0,0};
}
static void success(unsigned enabled, unsigned which, unsigned offset, unsigned mask) {
    setup(enabled);
    if (which==1) p[offset]^=(uint8_t)mask;
    if (which==2) shadow[offset]^=(uint8_t)mask;
    if (enabled) status[0]=which?offset+1:0;
    assert(__host_bingo_kernel_sdc_resolve_copy(&args)==0);
    assert(states[0].third_count==(enabled&&which?1u:0u));
    assert(states[0].selected==(enabled&&which==1?2u:1u));
    assert(states[0].commit_count==1 && states[0].report_count==1 && states[0].phase==2);
    assert(states[0].outcome==(enabled?(which?3u:2u):1u));
    assert(states[0].failure==0 && states[0].detect_valid==enabled);
    assert(!memcmp(canonical,enabled?src:p,64));
    assert(status[0]==(enabled?(which?offset+1:0):0));
    if (enabled&&which) assert(!memcmp(third,src,64));
    if (!enabled) assert(states[0].detect_code==UINT32_MAX);
    assert(__host_bingo_kernel_sdc_resolve_copy(&args)==2);
    assert(states[0].commit_count==1 && states[0].third_count<=1 && states[0].report_count==1);
}
static void rejected(unsigned failure) {
    uint8_t before[64]; memcpy(before,canonical,64);
    assert(__host_bingo_kernel_sdc_resolve_copy(&args)==2);
    assert(states[0].failure==failure && states[0].phase==3 && states[0].commit_count==0);
    assert(!memcmp(before,canonical,64));
}
int main(void) {
    success(0,0,17,4); success(1,0,17,4); success(0,1,17,4);
    const unsigned offsets[]={0,17,63}, masks[]={1,4,128};
    for(unsigned which=1;which<=2;++which)
        for(unsigned i=0;i<3;++i) for(unsigned j=0;j<3;++j) success(1,which,offsets[i],masks[j]);
    setup(1); p[5]^=1; shadow[7]^=4; status[0]=6; rejected(3);
    assert(states[0].third_count==1 && states[0].report_count==1 && status[0]==6);
    setup(1); rejected(2); /* unwritten sentinel */
    setup(1); status[0]=65; rejected(2);
    setup(1); p[17]^=4; status[0]=0; rejected(2);
    setup(1); status[0]=18; rejected(2);
    setup(1); p[17]^=4; status[0]=19; rejected(2);
    setup(1); status[0]=0; args.canonical_addr=args.primary_addr; rejected(1);
    setup(1); status[0]=0; args.src_addr=args.third_addr; rejected(1);
    setup(1); status[0]=0; args.size=63; rejected(1);
    setup(1); status[0]=0; states[0].enabled=2; rejected(7);
    setup(1); status[0]=0; states[0].commit_count=1;
    assert(__host_bingo_kernel_sdc_resolve_copy(&args)==2 && states[0].failure==5);
    setup(1); status[0]=0; states[0].report_count=1; rejected(6);
    setup(1); status[0]=0; args.state_addr=addr(&states[1]); args.transaction=1;
    assert(__host_bingo_kernel_sdc_resolve_copy(&args)==0 && states[1].commit_count==1);
    puts("SDC RECOVER C PASS");
}
'''


def native_build(directory, header=None):
    directory = Path(directory)
    (directory / "semantic.c").write_text(HARNESS)
    include = ROOT / "target/sw/host/runtime"
    if header is not None:
        (directory / "host_sdc_recover.h").write_text(header)
        include = directory
    command = ["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-O2",
               "-I", str(include), "-I", str(ROOT / "target/sw/host/runtime/libbingo/include"),
               str(directory / "semantic.c"), "-o", str(directory / "semantic")]
    return subprocess.run(command, capture_output=True, text=True), directory / "semantic"


def recipe():
    path = ROOT / "target/sw/host/apps/offload_bingo_hw/single_chip/workloads/dma_chain_sdc_recover_2cluster/main_bingo.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("sdc_recover_workload_test", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class RecoveryTests(unittest.TestCase):
    def test_actual_C_semantics_boundaries_and_ABI(self):
        with tempfile.TemporaryDirectory() as directory:
            build, executable = native_build(directory)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(executable)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("SDC RECOVER C PASS", run.stdout)

    def test_dependent_graph_and_public_status_binding(self):
        module = recipe()
        graph, roles, stages, mem = module.create_dfg(
            dict(num_clusters=2,stages=2,chunk_size=64),
            dict(num_chiplets=1,num_clusters_per_chiplet=2,num_cores_per_cluster=2,chiplet_ids=[0]),
            {(cl,c):(1,2,0)[c] for cl in range(2) for c in range(3)})
        by_id = {n.node_id:n for n in graph}
        self.assertEqual([s["status_offset_bytes"] for s in stages], [0,4])
        self.assertEqual(len(graph),10)
        for st in stages:
            for field in ("primary","shadow"):
                self.assertTrue(nx.has_path(graph,by_id[st[field]],by_id[st["compare"]]))
            self.assertTrue(nx.has_path(graph,by_id[st["compare"]],by_id[st["resolver"]]))
            self.assertEqual(by_id[st["resolver"]].kernel_args.size,64)
            self.assertNotIn("golden",vars(by_id[st["resolver"]].kernel_args))
        self.assertTrue(nx.has_path(graph,by_id[4],by_id[5]))
        self.assertTrue(nx.has_path(graph,by_id[8],by_id[9]))
        with tempfile.TemporaryDirectory() as directory:
            header = compile_graph(graph,directory)
        self.assertEqual(len(graph),28)
        self.assertEqual(sum(n.node_type=="dummy" and n.dep_set_enable for n in graph),6)
        self.assertEqual(sum(n.node_type=="dummy" and not n.dep_set_enable for n in graph),6)
        self.assertEqual(mem['state'].size,192)
        self.assertIn("__host_bingo_kernel_sdc_resolve_copy",header)
        for stage in stages:
            for field in ("primary","shadow"):
                self.assertTrue(nx.has_path(graph,by_id[stage[field]],by_id[stage["compare"]]))

    def test_guard_and_field_emission(self):
        module = recipe()
        with self.assertRaises(ValueError):
            module.create_dfg(dict(num_clusters=2,stages=2,chunk_size=63),{}, {})
        from bingo_mem_handle import BingoMemSymbol
        init = HostBingoKernelSdcRecoverInitArgs(BingoMemSymbol('status'),BingoMemSymbol('status',4),BingoMemSymbol('state'))
        self.assertEqual(init.get_c_field_assignments({})['enabled'],'bingo_test_cfg.sdc_enable')
        self.assertNotIn('golden',init.get_c_field_assignments({}))
        graph,roles,stages,mem = module.create_dfg(dict(num_clusters=2,stages=2,chunk_size=64),
            dict(num_chiplets=1,num_clusters_per_chiplet=2,num_cores_per_cluster=2,chiplet_ids=[0]),
            {(cl,c):(1,2,0)[c] for cl in range(2) for c in range(3)})
        code = '\n'.join(module.golden_checks(mem))
        self.assertIn('if (__recover_errors) return 2;',code)


if __name__ == "__main__":
    unittest.main()
