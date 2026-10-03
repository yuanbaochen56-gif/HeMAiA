#include "libbingo/bingo_api.h"
#include "ee_conf.h"
#if BINGO_TEST_CFG
#include "bingo_test_cfg.h"
#endif

uint32_t early_exit_conf_sample(void) {
#if BINGO_TEST_CFG
    return bingo_test_cfg_user(0);
#else
    return BINGO_EE_SAMPLE;
#endif
}

static uint64_t complete(uint64_t scratchpad_ptr, uint64_t result) {
    bingo_kernel_scratchpad_t *sp = (bingo_kernel_scratchpad_t *)(uintptr_t)scratchpad_ptr;
    sp->return_value = result;
    sp->num_return_values = 0;
    return result;
}

uint64_t __host_bingo_kernel_ee_select(void *arg) {
    __host_bingo_kernel_ee_select_args_t *args = arg;
    uint32_t sample = early_exit_conf_sample();
    if (sample >= args->sample_count)
        return complete(args->scratchpad_ptr, BINGO_RET_FAIL);
    early_exit_conf_select((const int8_t *)(uintptr_t)args->samples_addr,
                           (int8_t *)(uintptr_t)args->dst_addr, args->sample_size, sample);
    return complete(args->scratchpad_ptr, BINGO_RET_SUCC);
}

uint64_t __host_bingo_kernel_ee_conf_gate(void *arg) {
    __host_bingo_kernel_ee_conf_gate_args_t *args = arg;
    if (args->count < 2 || args->cerf_controlled_mask != 3)
        return complete(args->scratchpad_ptr, BINGO_RET_FAIL);
    uint32_t decision = early_exit_conf_margin(
        (const int32_t *)(uintptr_t)args->src_addr, args->count) >= args->threshold;
    *(uint32_t *)(uintptr_t)args->decision_addr = decision;
    bingo_cerf_update(args->cerf_controlled_mask, decision ? 2 : 1);
    return complete(args->scratchpad_ptr, BINGO_RET_SUCC);
}
