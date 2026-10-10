"""Execute actual SDC C helpers against native DMA/core mocks."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[4]

MOCK = r"""
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
#include "bingo_test_cfg.h"
#include "libbingo/device_kernel_args.h"
static uint32_t cluster = 1, core = 1, dm = 1, dma_calls = 0;
static uint32_t snrt_cluster_idx(void) { return cluster; }
static uint32_t snrt_cluster_core_idx(void) { return core; }
static uint32_t snrt_is_dm_core(void) { return dm; }
static void snrt_dma_start_1d_wideptr(uint64_t dst, uint64_t src, uint32_t size) {
    memcpy((void *)(uintptr_t)dst, (const void *)(uintptr_t)src, size);
    ++dma_calls;
}
static void snrt_dma_wait_all(void) {}
static void bingo_hw_manager_heartbeat(uint32_t value) { (void)value; }
#define printf_safe printf
#define SNAX_LIB_DEFINE
#define BINGO_RET_SUCC 0
#define BINGO_SW_GUARD_CHECK(arg, type) ((void)(arg))
typedef struct { uint32_t return_value, num_return_values; } bingo_kernel_scratchpad_t;
static bingo_kernel_scratchpad_t scratchpad;
#define BINGO_GET_SP(arg, type) (&scratchpad)
uint32_t __snax_bingo_kernel_idma_1d_copy(void *arg) { (void)arg; return 0; }
uint32_t __snax_bingo_kernel_int32_add(void *arg) { (void)arg; return 0; }
#include "bingo_sdc_inject.h"
#include "sdc.h"
static uint8_t output[256], left[256], right[256];
static uint32_t status;
static __snax_bingo_kernel_sdc_compare_args_t compare_args(void) {
    __snax_bingo_kernel_sdc_compare_args_t a = {0};
    uint64_t p=(uintptr_t)left, s=(uintptr_t)right, t=(uintptr_t)&status;
    a.primary_addr_hi=p>>32; a.primary_addr_lo=(uint32_t)p;
    a.shadow_addr_hi=s>>32; a.shadow_addr_lo=(uint32_t)s;
    a.status_addr_hi=t>>32; a.status_addr_lo=(uint32_t)t;
    a.size=129;
    return a;
}
int main(void) {
    const bingo_sdc_output_spec_t *copy = bingo_sdc_output_spec((uintptr_t)__snax_bingo_kernel_idma_1d_copy);
    const bingo_sdc_output_spec_t *add = bingo_sdc_output_spec((uintptr_t)__snax_bingo_kernel_int32_add);
    assert(copy && add);
    assert(copy->output_lo == offsetof(__snax_bingo_kernel_idma_1d_copy_args_t, dst_addr_lo));
    assert(copy->output_hi == offsetof(__snax_bingo_kernel_idma_1d_copy_args_t, dst_addr_hi));
    assert(copy->count == offsetof(__snax_bingo_kernel_idma_1d_copy_args_t, size));
    assert(copy->element_bytes == 1 && copy->use_dma == 1);
    assert(add->output_lo == offsetof(__snax_bingo_kernel_int32_add_args_t, c_addr));
    assert(add->count == offsetof(__snax_bingo_kernel_int32_add_args_t, num_elements));
    assert(add->element_bytes == 4 && add->output_hi == SIZE_MAX && add->use_dma == 0);
    bingo_test_cfg_t cfg=BINGO_TEST_CFG_INITIALIZER;
    cfg.fault_sdc_gid=14; cfg.fault_sdc_cluster=cluster; cfg.fault_sdc_core=core;
    cfg.fault_sdc_offset=5; cfg.fault_sdc_mask=0x80;
    __snax_bingo_kernel_idma_1d_copy_args_t c={0};
    uint64_t p=(uintptr_t)output;
    c.dst_addr_hi=p>>32; c.dst_addr_lo=(uint32_t)p; c.size=13;
    bingo_sdc_inject(13, (uintptr_t)__snax_bingo_kernel_idma_1d_copy, (uintptr_t)&c, &cfg);
    assert(dma_calls==0);
    bingo_sdc_inject(14, (uintptr_t)__snax_bingo_kernel_idma_1d_copy, (uintptr_t)&c, &cfg);
    for (uint32_t i=0; i<sizeof(output); ++i) assert(output[i] == (i==5 ? 0x80 : 0));
    cfg.fault_sdc_offset=13; uint32_t calls=dma_calls;
    bingo_sdc_inject(14, (uintptr_t)__snax_bingo_kernel_idma_1d_copy, (uintptr_t)&c, &cfg);
    assert(dma_calls==calls);
    cfg.fault_sdc_offset=5; cfg.fault_sdc_core=0;
    bingo_sdc_inject(14, (uintptr_t)__snax_bingo_kernel_idma_1d_copy, (uintptr_t)&c, &cfg);
    assert(dma_calls==calls);
    cfg.fault_sdc_core=core;
    __snax_bingo_kernel_int32_add_args_t a={0};
    assert((uintptr_t)output <= UINT32_MAX);
    a.c_addr=(uint32_t)(uintptr_t)output; a.num_elements=4;
    memset(output, 0, sizeof(output)); cfg.fault_sdc_offset=15;
    bingo_sdc_inject(14, (uintptr_t)__snax_bingo_kernel_int32_add, (uintptr_t)&a, &cfg);
    for (uint32_t i=0; i<sizeof(output); ++i) assert(output[i] == (i==15 ? 0x80 : 0));
    cfg.fault_sdc_offset=16;
    bingo_sdc_inject(14, (uintptr_t)__snax_bingo_kernel_int32_add, (uintptr_t)&a, &cfg);
    assert(output[16]==0);
    const uint32_t offsets[] = {0, 1, 63, 64, 127, 128};
    for (dm=0; dm<=1; ++dm) {
        __snax_bingo_kernel_sdc_compare_args_t cmp=compare_args();
        memset(left, 0, sizeof(left)); memset(right, 0, sizeof(right)); status=UINT32_MAX;
        assert(__snax_bingo_kernel_sdc_compare(&cmp)==0);
        assert(status==0 && scratchpad.return_value==0);
        for (size_t i=0; i<sizeof(offsets)/sizeof(offsets[0]); ++i) {
            memset(right, 0, sizeof(right)); right[offsets[i]]=0x80; status=UINT32_MAX;
            assert(__snax_bingo_kernel_sdc_compare(&cmp)==0);
            assert(status==1+offsets[i] && scratchpad.return_value==status);
        }
        right[3]=1; right[2]=1;
        assert(__snax_bingo_kernel_sdc_compare(&cmp)==0);
        assert(status==3);
    }
    puts("SDC DEVICE PASS");
    return 0;
}
"""


class SdcDeviceTests(unittest.TestCase):
    def test_actual_output_table_injection_and_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "device.c"
            source.write_text(MOCK)
            executable = Path(directory) / "device"
            subprocess.run(
                ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fno-pie", "-no-pie",
                 "-DBINGO_SDC", "-DBINGO_TEST_CFG=1",
                 "-I", str(ROOT / "target/sw/shared/runtime"),
                 "-I", str(ROOT / "target/sw/host/runtime/libbingo/include"),
                 "-I", str(ROOT / "target/sw/device/runtime/src"),
                 "-I", str(ROOT / "target/sw/device/apps/snax/snax-bingo-offload/libsnaxkernel/offload_hw_kernels"),
                 str(source), "-o", str(executable)], check=True, capture_output=True, text=True)
            result = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
            self.assertIn("SDC DEVICE PASS", result.stdout)
            self.assertIn("[BINGO_SDC_WARN]", result.stdout)
            self.assertIn("[BINGO_SDC_INJECT]", result.stdout)

    def test_injection_is_after_kernel_and_before_heartbeat_done(self):
        source = (ROOT / "target/sw/device/runtime/src/bingo.h").read_text()
        kernel = source.index("kernel_return_value = ((uint32_t (*)(uint32_t))cur_kernel_ptr)(cur_arg_ptr);")
        inject = source.index("bingo_sdc_inject(cur_global_task_id", kernel)
        end = source.index("// End-of-task beat before done", inject)
        done = source.index("write_bingo_hw_manager_done_queue(cur_global_task_id)", end)
        self.assertLess(kernel, inject)
        self.assertLess(inject, end)
        self.assertLess(end, done)
        self.assertIn("#if defined(BINGO_SDC) && BINGO_TEST_CFG", source[kernel:inject])


if __name__ == "__main__":
    unittest.main()
