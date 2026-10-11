"""Two dependent private DMA copies, reliable host vote and canonical publication."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[8]
sys.path.insert(0, str(ROOT / "target/sw/host/runtime/libbingo/mini_compiler"))
from bingo_dfg import BingoDFG
from bingo_node import BingoNode
from bingo_mem_handle import BingoMemAlloc, BingoMemSymbol
from bingo_kernel_args import (SnaxBingoKernelIdma1dCopyArgs, HostBingoKernelIdmaArgs,
    HostBingoKernelSdcRecoverInitArgs, HostBingoKernelSdcResolveCopyArgs)
from bingo_platform import parse_platform_cfg, parse_bingo_core_type_ids
from sdc_recover_datagen import emit_header_file

def create_dfg(params, platform, core_type_ids):
 if params != {"num_clusters":2,"stages":2,"chunk_size":64}:
  raise ValueError("Recovery workload requires exactly two 64-byte stages")
 if platform != dict(num_chiplets=1,num_clusters_per_chiplet=2,num_cores_per_cluster=2,chiplet_ids=[0]):
  raise ValueError("Recovery workload requires chip0, two clusters and two device cores")
 g=BingoDFG(1,2,2,True,[0],core_type_ids=core_type_ids)
 mem={n:BingoMemAlloc('s1b_'+n,64, 'L3',0) for n in ['P0','T0','C0','P1','T1','C1','sink']}
 mem['input']=BingoMemSymbol('s1b_input');mem['state']=BingoMemAlloc('s1b_state',192,'L3',0)
 init=BingoNode(0,0,2,node_name='recovery_init',kernel_name=HostBingoKernelSdcRecoverInitArgs.KERNEL_NAME)
 g.bingo_add_node(init);roles={init:'init'};stages=[];previous=init
 for tx in range(2):
  src=mem['input' if tx==0 else 'C0'];p=mem['P'+str(tx)]
  primary=BingoNode(0,0,1,node_name='stage%d_primary'%tx,kernel_name='__snax_bingo_kernel_idma_1d_copy',kernel_args=SnaxBingoKernelIdma1dCopyArgs(src,p,64))
  g.bingo_add_node(primary);g.bingo_add_edge(previous,primary)
  shadow=g.bingo_add_dmr(primary);
  if shadow is None: raise ValueError("Primary cannot be enrolled for recovery")
  # Read-only public graph search, not access to the private _sdc_pairs table.
  compares=[n for n in g.successors(primary) if n.kernel_name=='__snax_bingo_kernel_sdc_compare']
  
  if len(compares)!=1: raise ValueError("Expected exactly one compare successor")
  compare=compares[0]
  mem['S'+str(tx)]=shadow.kernel_args.dst_addr
  resolver=BingoNode(0,0,2,node_name='stage%d_resolver'%tx,kernel_name=HostBingoKernelSdcResolveCopyArgs.KERNEL_NAME,
    kernel_args=HostBingoKernelSdcResolveCopyArgs(src,p,mem['S'+str(tx)],mem['T'+str(tx)],mem['C'+str(tx)],compare.kernel_args.status_addr,mem['state'].view(96*tx),64,tx))
  g.bingo_add_node(resolver);g.bingo_add_edge(primary,resolver);g.bingo_add_edge(compare,resolver)
  roles.update({primary:'P%d'%tx,shadow:'S%d'%tx,compare:'compare%d'%tx,resolver:'resolver%d'%tx})
  stages.append(dict(transaction=tx,primary=primary.node_id,shadow=shadow.node_id,compare=compare.node_id,resolver=resolver.node_id,
    status_index=tx,status_offset_bytes=compare.kernel_args.status_addr.offset,input_buffer='input' if tx==0 else 'C0'))
  previous=resolver
 init.kernel_args=HostBingoKernelSdcRecoverInitArgs(*[next(n for n in g.nodes if n.node_id==row['compare']).kernel_args.status_addr for row in stages],mem['state'])
 consumer=BingoNode(0,0,2,node_name='final_consumer',kernel_name='__host_bingo_kernel_idma',kernel_args=HostBingoKernelIdmaArgs(mem['C1'],mem['sink'],64))
 g.bingo_add_node(consumer);g.bingo_add_edge(previous,consumer);roles[consumer]='consumer'
 return g,roles,stages,mem


def golden_checks(mem):
    # Post-execute is diagnostic only; the resolver never sees this golden.
    code = ["asm volatile(\"fence rw, rw\" ::: \"memory\");",
            "uint32_t __recover_errors = 0;",
            "for (uint32_t i = 0; i < 64; ++i) __recover_errors += "
            "((const volatile uint8_t *)(uintptr_t)ptr_s1b_sink)[i] != s1b_golden[i];",
            'printf_safe("[Host] Check [SDC_RECOVER_sink]: %s (%u / 64 bytes)\\r\\n", '
            '__recover_errors ? "FAIL" : "PASS", __recover_errors);']
    for name in ("input", "P0", "S0", "T0", "C0", "P1", "S1", "T1", "C1", "sink"):
        addr = None
        # Handles are rendered by the compiler; known allocations retain their
        # own c variable name, including its generated shadow output name.
        if name == "input": addr = "(uintptr_t)s1b_input"
        else: addr = mem[name].get_c_var_name()
        code += ["{", "char hex[129] = {0};", "const char digits[] = \"0123456789abcdef\";",
                 f"const volatile uint8_t *p = (const volatile uint8_t *)(uintptr_t){addr};",
                 "for (uint32_t i = 0; i < 64; ++i) { hex[2*i] = digits[p[i] >> 4]; hex[2*i+1] = digits[p[i] & 15]; }",
                 "hex[128] = 0;", f'printf_safe("[Host] SDC_BUFFER name={name} data=%s\\r\\n", hex);', "}"]
    code += ["if (__recover_errors) return 2;"]
    return code


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output_dir", default=".")
    parser.add_argument("--output_offload_file_name", default="offload_bingo_hw.h")
    parser.add_argument("-c", "--cfg", type=Path, required=True)
    parser.add_argument("--hwcfg", type=Path)
    parser.add_argument("--platformcfg", type=Path, required=True)
    parser.add_argument("--data_h", type=Path)
    args = parser.parse_args()
    params = json.loads(args.cfg.read_text())
    platform = parse_platform_cfg(args.platformcfg)
    graph, roles, stages, mem = create_dfg(params, platform, parse_bingo_core_type_ids(args.platformcfg))
    logical_edges = [[u.node_id, v.node_id] for u,v in graph.edges]
    if [s["status_offset_bytes"] for s in stages] != [0,4]:
        raise ValueError("Recovery status indices must be contiguous0/1")
    out = Path(args.output_dir); out.mkdir(parents=True, exist_ok=True)
    data_h = args.data_h or out / "sdc_recover_data.h"
    data_h.write_text(emit_header_file())
    graph.bingo_compile_dfg("Single-Chip DMA Chain with SDC Recovery", str(out),
        args.output_offload_file_name, extra_include_header_list=[data_h.name],
        post_execute_code=golden_checks(mem))
    report = json.loads((out / "sdc_report.json").read_text())
    selected = [n for n in report["nodes"] if n["selected"]]
    if [n["status_index"] for n in selected] != [0,1]:
        raise ValueError("Recovery report index contract violated")
    nodes = [dict(id=n.node_id, name=n.node_name, role=roles.get(n,"infrastructure"),
        type=n.node_type, kernel=n.kernel_name, slot=[n.assigned_cluster_id,n.assigned_core_id],
        descriptor_hex=f"{graph.bingo_pack_node(n):016x}") for n in graph._bingo_topological_order()]
    binding = dict(abi_version=1, roles={str(n.node_id):role for n,role in roles.items()},
        stages=stages, logical_edges=logical_edges,
        transformed_edges=[[u.node_id,v.node_id] for u,v in graph.edges], nodes=nodes,
        cerf_group=report["group"],
        status_contract=dict(indices=[0,1], offsets=[0,4], infer_from_min=False),
        buffers={name:dict(c_var=("s1b_input" if name=="input" else h.get_c_var_name()),
                               size=(192 if name=="state" else 64)) for name,h in mem.items()})
    (out / "recovery_graph.json").write_text(json.dumps(binding,indent=2)+"\n")


if __name__ == "__main__":
    main()
