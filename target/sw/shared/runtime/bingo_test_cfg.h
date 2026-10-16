// Copyright 2026 KU Leuven.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stddef.h>
#include <stdint.h>

// Test-only, little-endian image data. Each row spans sixteen 64-bit banks.
#define BINGO_TEST_CFG_MAGIC 0x42475431u
#if (defined(BINGO_WD_LATE) || defined(BINGO_SDC) || defined(BINGO_FENCE_FB) || defined(BINGO_TASK_TIME)) && \
    !defined(BINGO_WD_ADAPT)
#define BINGO_WD_ADAPT
#endif
#if defined(BINGO_FENCE_FB) && (defined(BINGO_SDC) || defined(BINGO_WD_LATE))
#error BINGO_FENCE_FB is not combined with BINGO_SDC or BINGO_WD_LATE
#endif
#ifdef BINGO_WD_ADAPT
#define BINGO_TEST_CFG_VERSION 3u
#else
#define BINGO_TEST_CFG_VERSION 2u
#endif

typedef struct __attribute__((aligned(128))) {
    uint32_t magic;
    uint32_t version;
    uint32_t fault_gid;
    uint32_t fault_stall_cycles;
    uint32_t fault_cluster;
    uint32_t fault_core;
    uint32_t fault_pre_stall_cycles;
    uint32_t fault_after_kernel;
    uint32_t risk_late;
    uint32_t risk_policy;
    uint32_t risk_epoch;
    uint32_t risk_confirm;
    uint32_t pm_boost_power_level;
    uint32_t boost_policy;
    uint32_t pm_idle_entry_delay;
    uint32_t pm_access_wake_hold;
    uint32_t remote_proxy_timeout;
    uint32_t park_req;
    uint32_t cerf_fb_enable;
    uint32_t cerf_fb_cluster;
    uint32_t cerf_fb_core;
    uint32_t cerf_fb_clear;
    uint32_t cerf_fb_set;
    uint32_t user[4];
    uint32_t evlog_enable;
    uint32_t recovery_hold;
    uint32_t pm_access_level;
    // C6 baselines, read only by images built with BINGO_C6_SW_CERF. The CERF
    // degradation table is the one the image programs (compiler or T1 fields):
    // 0 the manager applies it (as without the flag), 1 the host applies the
    // same entries once it sees replay_stuck, 2 no degradation (fail-stop).
    uint32_t cerf_fb_mode;
    uint32_t cerf_poll_cycles;  // host cycles between polls in mode 1, 0 = back to back
    uint32_t wd_type_h[16];
    uint32_t wd_type_c[16];
#ifdef BINGO_WD_ADAPT
    // Version 3 row (byte 256). Every v3 image has every field at the same
    // offset; the flag in each comment is the one that reads it, other images
    // leave it zero.
    uint32_t wd_adapt;
    uint32_t wd_late;               // BINGO_WD_LATE
    uint32_t fault_pre_stall_from;  // BINGO_WD_LATE
    uint32_t sdc_enable;            // BINGO_SDC
    uint32_t fault_sdc_gid;         // BINGO_SDC
    uint32_t fault_sdc_cluster;     // BINGO_SDC
    uint32_t fault_sdc_core;        // BINGO_SDC
    uint32_t fault_sdc_offset;      // BINGO_SDC
    uint32_t fault_sdc_mask;        // BINGO_SDC
    uint32_t fence_fb;              // BINGO_FENCE_FB
    uint32_t wd_adapt_reserved[19];
    // Fields added later take the reserved words from the end of the row
    uint32_t fault_beat_period;     // BINGO_TASK_TIME: an injected stall beats every n core cycles (0: silent)
    uint32_t wd_budget;             // BINGO_TASK_TIME: BINGO_WD_BUDGET
    uint32_t task_time;             // BINGO_TASK_TIME: [3:0] estimates / 2^n, [4] none
#endif
} bingo_test_cfg_t;

#ifdef BINGO_SDC
#define BINGO_TEST_CFG_SDC_INITIALIZER \
    , .fault_sdc_gid = UINT32_MAX, .fault_sdc_cluster = UINT32_MAX, .fault_sdc_core = UINT32_MAX
#else
#define BINGO_TEST_CFG_SDC_INITIALIZER
#endif

#define BINGO_TEST_CFG_INITIALIZER { \
    .magic = BINGO_TEST_CFG_MAGIC, .version = BINGO_TEST_CFG_VERSION, \
    .fault_gid = UINT32_MAX, .fault_cluster = UINT32_MAX, .fault_core = UINT32_MAX \
    BINGO_TEST_CFG_SDC_INITIALIZER \
}

#ifdef BINGO_WD_ADAPT
_Static_assert(sizeof(bingo_test_cfg_t) == 384, "v3 test configuration must fit three rows");
_Static_assert(offsetof(bingo_test_cfg_t, wd_adapt) == 256 && offsetof(bingo_test_cfg_t, wd_late) == 260 &&
               offsetof(bingo_test_cfg_t, sdc_enable) == 268 && offsetof(bingo_test_cfg_t, fence_fb) == 292 &&
               offsetof(bingo_test_cfg_t, fault_beat_period) == 372 &&
               offsetof(bingo_test_cfg_t, wd_budget) == 376 && offsetof(bingo_test_cfg_t, task_time) == 380,
               "v3 offsets are fixed: the driver packs one layout for every v3 image");
#else
_Static_assert(sizeof(bingo_test_cfg_t) == 256, "v2 test configuration must fit two rows");
#endif

#if BINGO_TEST_CFG
extern volatile bingo_test_cfg_t bingo_test_cfg;

static inline uint32_t bingo_test_cfg_user(uint32_t index) {
    return bingo_test_cfg.user[index];
}
#endif
