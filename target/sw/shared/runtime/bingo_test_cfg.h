// Copyright 2026 KU Leuven.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stdint.h>

// Test-only, little-endian image data. Each row spans sixteen 64-bit banks.
#define BINGO_TEST_CFG_MAGIC 0x42475431u
#define BINGO_TEST_CFG_VERSION 2u

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
    uint32_t reserved[5];
    uint32_t wd_type_h[16];
    uint32_t wd_type_c[16];
} bingo_test_cfg_t;

#define BINGO_TEST_CFG_INITIALIZER { \
    .magic = BINGO_TEST_CFG_MAGIC, .version = BINGO_TEST_CFG_VERSION, \
    .fault_gid = UINT32_MAX, .fault_cluster = UINT32_MAX, .fault_core = UINT32_MAX \
}

_Static_assert(sizeof(bingo_test_cfg_t) == 256, "v2 test configuration must fit two rows");

#if BINGO_TEST_CFG
extern volatile bingo_test_cfg_t bingo_test_cfg;

static inline uint32_t bingo_test_cfg_user(uint32_t index) {
    return bingo_test_cfg.user[index];
}
#endif
