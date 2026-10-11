"""Opt-in, single-chip DMR observers. No recovery or consumer barriers."""
from copy import copy
import json

from bingo_kernel_args import (
    SnaxBingoKernelIdma1dCopyArgs, SnaxBingoKernelInt32AddArgs,
    SnaxBingoKernelSdcCompareArgs,
)
from bingo_mem_handle import BingoMemAlloc, BingoMemAllocView, BingoMemSymbol, BingoMemFixedAddr
from bingo_node import BingoNode


class DmrSupport:
    def _init_dmr(self):
        self._sdc_types = set()
        self._sdc_pairs = {}
        self._sdc_report = []
        self._sdc_status = None
        self._sdc_group = None

    def _validate_dmr_type(self, chip, core_type):
        if (self.num_chiplets != 1 or not self.is_host_as_acc or chip != 0
                or chip not in self.chiplet_ids or type(core_type) is not int
                or not 0 < core_type < 32):
            raise ValueError("DMR requires chip 0 of a single-chip host graph and a nonzero core type")
        key = (chip, core_type)
        if key in self._auto_host_fallback_types or any(
                n.assigned_chiplet_id == chip and self.core_type_ids.get(
                    (n.assigned_cluster_id, n.assigned_core_id)) == core_type
                for n in self._host_fallbacks):
            raise ValueError("DMR conflicts with host fallback for this type")
        for primary, _ in self._cerf_fallbacks:
            if (primary.assigned_chiplet_id == chip and self.core_type_ids.get(
                    (primary.assigned_cluster_id, primary.assigned_core_id)) == core_type):
                raise ValueError("DMR conflicts with handwritten CERF fallback for this type")
        for primary, _ in self._cerf_group_fallbacks:
            members = [primary]
            if primary.cond_exec_en:
                members += [n for n in self.node_list if n.cond_exec_en
                            and n.assigned_chiplet_id == primary.assigned_chiplet_id
                            and n.cond_exec_group_id == primary.cond_exec_group_id]
            if any(n.assigned_chiplet_id == chip and self.core_type_ids.get(
                    (n.assigned_cluster_id, n.assigned_core_id)) == core_type for n in members):
                raise ValueError("DMR conflicts with handwritten CERF group fallback")

    def _validate_dmr_node(self, node):
        from bingo_dfg import _validate_host_copy, _validate_host_add
        if node not in self:
            raise ValueError("DMR node must belong to this DFG")
        if (node.node_type != "normal" or node.cond_exec_en or node._gating_node is not None
                or node.cerf_write_groups or any(data.get("cond", False) for _, _, data in
                    list(self.in_edges(node, data=True)) + list(self.out_edges(node, data=True)))):
            raise ValueError("DMR requires an ordinary unconditional device task")
        key = node.kernel_name, type(node.kernel_args)
        if key == ("__snax_bingo_kernel_idma_1d_copy", SnaxBingoKernelIdma1dCopyArgs):
            _validate_host_copy(node.kernel_args)
            inputs, output, size = [node.kernel_args.src_addr], node.kernel_args.dst_addr, node.kernel_args.size
            output_field = "dst_addr"
        elif key == ("__snax_bingo_kernel_int32_add", SnaxBingoKernelInt32AddArgs):
            _validate_host_add(node.kernel_args)
            inputs = [node.kernel_args.a_addr, node.kernel_args.b_addr]
            output, size, output_field = node.kernel_args.c_addr, 4 * node.kernel_args.num_elements, "c_addr"
        else:
            raise ValueError(f"DMR kernel/args not whitelisted: {node.kernel_name}")
        if self._node_no_replay(node):
            raise ValueError("DMR cannot duplicate a non-idempotent task (A4)")
        if type(size) is not int or not 0 < size <= 0xFFFFFFFF:
            raise ValueError("DMR output length must be a positive uint32 byte count")
        core_type = self.core_type_ids.get((node.assigned_cluster_id, node.assigned_core_id))
        slots = sorted((cl, core) for (cl, core), typ in self.core_type_ids.items()
                       if typ == core_type and cl != node.assigned_cluster_id
                       and 0 <= cl < self.num_clusters_per_chiplet
                       and 0 <= core < self.num_cores_per_cluster - 1)
        if not slots:
            raise ValueError("DMR needs the same core type in another cluster")
        for handle in inputs:
            base = handle.base if isinstance(handle, BingoMemAllocView) else handle
            if isinstance(base, BingoMemAlloc):
                if base.chip_id != node.assigned_chiplet_id or base.mem_level not in ("L1", "L2", "L3"):
                    raise ValueError("DMR shadow cannot access an input allocation")
                if output_field == "c_addr" and base.mem_level == "L1":
                    raise ValueError("DMR int32 add input is cluster-local L1, not cross-cluster accessible")
            elif isinstance(base, BingoMemFixedAddr):
                if not 0 <= base.address < 0x100000000:
                    raise ValueError("DMR input fixed address is outside the single-chip address space")
                if output_field == "c_addr" and not 0x70000000 <= base.address < 0x100000000:
                    raise ValueError("DMR int32 add input fixed address is not shared L2/L3")
            elif not isinstance(base, BingoMemSymbol):
                raise ValueError("DMR inputs require memory handles, not bare addresses")
        base = output.base if isinstance(output, BingoMemAllocView) else output
        if isinstance(base, BingoMemAlloc):
            if base.chip_id != node.assigned_chiplet_id or base.mem_level not in ("L1", "L2", "L3"):
                raise ValueError("DMR output allocation is not on the current chip")
            level = base.mem_level
        else:
            # Host symbols and explicit shared addresses have no local heap level.
            level = "L3"
        return slots[0], output, output_field, size, level

    def bingo_add_dmr(self, node):
        """Return a shadow, or None with a recorded per-node skip reason.

        Inputs must remain stable for both executions; primary output must not
        be overwritten before comparison. Symbols denote host L3 data. The
        shadow uses the first other-cluster slot of the same type. Consumers
        do not wait for comparison. Type-level fallback conflicts raise.
        """
        if node not in self:
            raise ValueError("DMR node must belong to this DFG")
        core_type = self.core_type_ids.get((node.assigned_cluster_id, node.assigned_core_id))
        self._validate_dmr_type(node.assigned_chiplet_id, core_type)
        if node in self._sdc_pairs:
            return self._sdc_pairs[node][0]
        self._sdc_types.add((node.assigned_chiplet_id, core_type))
        try:
            (cluster, core), output, field, size, level = self._validate_dmr_node(node)
        except ValueError as error:
            self._sdc_record(node, False, str(error))
            return None
        if self._sdc_status is None:
            self._sdc_status = BingoMemAlloc("__sdc_status", 0, "L3", node.assigned_chiplet_id)
        index = len(self._sdc_pairs)
        self._sdc_status.size = 4 * (index + 1)
        buffer = BingoMemAlloc(f"__sdc_shadow_{node.node_id}", size, level,
                               node.assigned_chiplet_id, cluster if level == "L1" else 0)
        args = copy(node.kernel_args)
        setattr(args, field, buffer)
        shadow = BingoNode(node.assigned_chiplet_id, cluster, core,
                           node_name=f"__sdc_shadow_{node.node_name}",
                           kernel_name=node.kernel_name, kernel_args=args)
        self.bingo_add_node(shadow)
        compare = BingoNode(node.assigned_chiplet_id, cluster, core,
                            node_name=f"__sdc_compare_{node.node_name}",
                            kernel_name="__snax_bingo_kernel_sdc_compare",
                            kernel_args=SnaxBingoKernelSdcCompareArgs(
                                output, buffer, size, self._sdc_status.view(4 * index)))
        self.bingo_add_node(compare)
        self._sdc_pairs[node] = shadow, compare
        for pred in list(self.predecessors(node)):
            self.bingo_add_edge(pred, shadow)
        self.bingo_add_edge(shadow, compare)
        self.bingo_add_edge(node, compare)
        self._sdc_record(node, True, "", shadow.node_id, compare.node_id, index, size)
        return shadow

    def _sdc_record(self, node, selected, reason, shadow=None, compare=None, index=None, size=None):
        self._sdc_report = [r for r in self._sdc_report if r["node_id"] != node.node_id]
        self._sdc_report.append(dict(node_id=node.node_id, node_name=node.node_name,
                                    selected=selected, reason=reason, shadow_id=shadow,
                                    compare_id=compare, status_index=index, size=size))

    def bingo_auto_dmr(self, chip, core_type):
        """Snapshot this type's current user nodes; return eligible shadows.

        Later nodes are not enrolled implicitly. Individual failures are
        reported in sdc_report.json; type conflicts raise before skipping.
        """
        self._validate_dmr_type(chip, core_type)
        self._sdc_types.add((chip, core_type))
        generated = {n for pair in self._sdc_pairs.values() for n in pair}
        candidates = [n for n in self.node_list if n not in generated
                      and n.assigned_chiplet_id == chip and self.core_type_ids.get(
                          (n.assigned_cluster_id, n.assigned_core_id)) == core_type]
        return [shadow for n in candidates if (shadow := self.bingo_add_dmr(n)) is not None]

    def _expand_dmr(self):
        for chip, core_type in self._sdc_types:
            self._validate_dmr_type(chip, core_type)
        for primary, (shadow, compare) in self._sdc_pairs.items():
            self._validate_dmr_node(primary)
            for pred in list(self.predecessors(primary)):
                self.bingo_add_edge(pred, shadow)
            self.bingo_add_edge(primary, compare)
            self.bingo_add_edge(shadow, compare)

    def _compile_dmr(self):
        if not self._sdc_pairs:
            return
        for chip, core_type in self._sdc_types:
            self._validate_dmr_type(chip, core_type)
        self._sdc_group = self._alloc_cerf_group()
        mask = 1 << self._sdc_group
        for node in self.node_list:
            if (getattr(node.kernel_args, "cerf_controlled_mask", 0)
                    | sum(1 << g for g in set(node.cerf_write_groups))) & mask:
                raise ValueError("A gating node controls the private DMR group")
        for primary, (shadow, compare) in self._sdc_pairs.items():
            for node in (shadow, compare):
                node.cond_exec_en = True
                node.cond_exec_group_id = self._sdc_group
                node.cond_exec_invert = False
            exits = [n for n in self.node_list if n.kernel_name == "__host_bingo_kernel_exit"
                     and n.assigned_chiplet_id == primary.assigned_chiplet_id]
            if len(exits) != 1:
                raise ValueError("DMR needs one chip-local host exit")
            self.bingo_add_edge(compare, exits[0])

    def _emit_dmr_init(self, f, chip):
        if not self._sdc_pairs:
            return
        f.write("        // SDC result slots are initialized once before dispatch.\n")
        f.write(f"        volatile uint32_t *__sdc_status = (volatile uint32_t *)(uintptr_t){self._sdc_status.get_c_var_name()};\n")
        f.write(f"        for (uint32_t i = 0; i < {len(self._sdc_pairs)}u; ++i) __sdc_status[i] = 0;\n")
        mask = 1 << self._sdc_group
        f.write(f"        bingo_cerf_update(0x{mask:08x}u, bingo_test_cfg.sdc_enable ? 0x{mask:08x}u : 0u);\n")

    def _emit_dmr_report(self, f):
        if not self._sdc_pairs:
            return
        f.write("        uint32_t __sdc_mismatches = 0;\n")
        for row in self._sdc_report:
            if not row["selected"]:
                continue
            f.write(f"        if (__sdc_status[{row['status_index']}] != 0) {{\n")
            f.write("            ++__sdc_mismatches;\n")
            f.write(f'            printf_safe("[Host] SDC mismatch: primary={row["node_id"]} shadow={row["shadow_id"]} compare={row["compare_id"]} code=%u\\r\\n", __sdc_status[{row["status_index"]}]);\n')
            f.write("        }\n")
        f.write('        printf_safe("[Host] SDC report: %u mismatches\\r\\n", __sdc_mismatches);\n')

    def _write_dmr_report(self, output_dir):
        if self._sdc_types:
            from pathlib import Path
            (Path(output_dir) / "sdc_report.json").write_text(
                json.dumps(dict(group=self._sdc_group, nodes=self._sdc_report), indent=2) + "\n")
