// Copyright 2022 ETH Zurich and University of Bologna.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0

#pragma once
#define N_CHIPLETS ${nr_chiplets}
% for idx, chiplet_id in enumerate(chiplet_ids):
#define CHIPLET_ID_${idx} 0x${f"{chiplet_id:02x}"}
% endfor
#define N_CLUSTERS ${nr_clusters}
#define N_SNITCHES ${nr_cores}

#define N_CHIPLETS_WIDTH               ${clog2_nr_chiplets}
#define N_CLUSTERS_PER_CHIPLET         ${nr_clusters_per_chiplet}
#define N_CLUSTERS_PER_CHIPLET_WIDTH   ${clog2_nr_clusters_per_chiplet}
#define N_CORES_PER_CLUSTER            ${nr_cores_per_cluster}
#define N_CORES_PER_CLUSTER_WIDTH      ${clog2_nr_cores_per_cluster}

// Whether the testharness memchip clock runs at the same speed as the host clock.
#define HEMAIA_SAME_MEMCHIP_SPEED      ${same_memchip_speed}

// Bingo core type of each slot: cluster index within the chiplet, core index
// within the cluster (the extra host / tied-off slot is type 0). Cores with the
// same non-zero type may take over each other's tasks; the CERF degradation
// table (quad periph BINGO_CERF_FB_*) has one entry per type. Same source as
// BingoCoreTypeId in occamy_quad_ctrl (occamy.py get_bingo_core_type_ids).
<%
  bingo_type_terms = []
  for cl, ids in enumerate(bingo_core_type_ids):
    for core, t in enumerate(ids):
      bingo_type_terms.append(f"((cluster) == {cl} && (core) == {core}) ? {t} :")
%>\
#define BINGO_NR_CORE_TYPES            ${bingo_nr_core_types}
#define BINGO_CORE_TYPE_ID(cluster, core) (${" ".join(bingo_type_terms)} 0)

// CLINT MSIP bit the bingo HW manager writes to ring the host DVFS doorbell (a
// dedicated interrupt target appended after this chiplet's harts). Keep in sync with
// occamy.py hw_manager_ipi_idx / occamy_soc.sv.tpl.
#define HW_MANAGER_DVFS_MSIP_BIT       ${hw_manager_dvfs_msip_bit}

// Per-edge dependency tag width of the bingo HW manager (s1_quadrant.dep_tag_width ->
// bingo_hw_manager_top DepTagWidth). The task descriptor packs one tag at the MSB of
// each dep_*_info field, so SW must use the same width as the RTL: bingo_utils.h derives
// DEP_TAG_WIDTH from this, and the mini-compiler passes it to BingoDFG(dep_tag_width=).
#define BINGO_DEP_TAG_WIDTH            ${dep_tag_width}
