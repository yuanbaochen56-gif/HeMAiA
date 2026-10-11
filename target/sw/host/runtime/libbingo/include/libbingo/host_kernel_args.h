// Fanchen Kong <fanchen.kong@kuleuven.be>
#pragma once
#include <stdint.h>
#define __HOST_BINGO_KERNEL_ARGS_DEFINE typedef struct __attribute__((packed, aligned(8)))

// NOTE: Every host kernel args struct has `scratchpad_ptr` as its LAST field.
// The kernel reads it to access its own bingo_kernel_scratchpad_t.

__HOST_BINGO_KERNEL_ARGS_DEFINE __host_bingo_kernel_dummy_args {
    uint64_t dummy_input;
    uint64_t scratchpad_ptr;
} __host_bingo_kernel_dummy_args_t;

__HOST_BINGO_KERNEL_ARGS_DEFINE __host_bingo_kernel_exit_args {
    uint64_t exit_code;
    uint64_t scratchpad_ptr;
} __host_bingo_kernel_exit_args_t;

__HOST_BINGO_KERNEL_ARGS_DEFINE __host_bingo_kernel_entry_args {
    uint64_t start_cc_reg_addr;
    uint64_t scratchpad_ptr;
} __host_bingo_kernel_entry_args_t;


// Check-mode constants for __host_bingo_kernel_check_result
#define BINGO_CHECK_TYPE_BYTE_EXACT 0
#define BINGO_CHECK_TYPE_FP32_TOL   1
#define BINGO_CHECK_TYPE_FP16_TOL   2
// fp16 RELATIVE tolerance: pass when |out-golden| <= rtol*|golden| + 0.05. The rms (fpnew rsqrt)
// and softmax (Cephes exp) feed int8 requants whose 1-LSB rounding differs from an fp32 golden by
// a magnitude-proportional amount, so a fixed abs-tol is unusable on large GEMM outputs (~1000s).
#define BINGO_CHECK_TYPE_FP16_RELTOL 3
// signed-int8 ABSOLUTE tolerance: pass when |out-golden| <= tol (LSBs). int8 = quantize(fp16);
// a 1-ULP fp16 difference (HW exp/rsqrt vs a numpy golden) flips +-1 int8 LSB at rounding
// boundaries, so byte-exact is too strict for a quantized activation. tolerance (fp32) -> int LSB.
#define BINGO_CHECK_TYPE_INT8_TOL 4

// Precision selector for the runtime-typed Ara kernels (__host_bingo_kernel_<op>
// dispatchers in host_kernel_lib.h). Passed as a plain arg word; the typed
// __host_bingo_kernel_<op>_f32 entry points are FP32-only and ignore precision.
#define BINGO_PREC_FP32  0
#define BINGO_PREC_FP16  1
#define BINGO_PREC_INT8  2
#define BINGO_PREC_INT16 3
#define BINGO_PREC_INT32 4

__HOST_BINGO_KERNEL_ARGS_DEFINE __host_bingo_kernel_check_result_args {
    uint64_t golden_data_addr;
    uint64_t output_data_addr;
    uint64_t data_size;        // in Bytes (byte-exact) OR fp-array size in Bytes (fp modes)
    uint64_t name_addr;        // const char* label for printf (0 = no label)
    uint64_t check_type;       // 0 = byte-exact, 1 = fp32 absolute-tolerance, 2 = fp16 absolute-tolerance
    uint64_t tolerance_bits;   // FP32 bit pattern of absolute tolerance (used when check_type != 0)
    uint64_t scratchpad_ptr;
} __host_bingo_kernel_check_result_args_t;

__HOST_BINGO_KERNEL_ARGS_DEFINE __host_bingo_kernel_idma_args {
    uint64_t src_addr;
    uint64_t dst_addr;
    uint64_t size;        // in Bytes
    uint64_t scratchpad_ptr;
} __host_bingo_kernel_idma_args_t;

__HOST_BINGO_KERNEL_ARGS_DEFINE __host_bingo_kernel_xdma_1d_copy_args {
    uint64_t src_addr;
    uint64_t dst_addr;
    uint64_t size;        // in Bytes
    uint64_t scratchpad_ptr;
} __host_bingo_kernel_xdma_1d_copy_args_t;
typedef __host_bingo_kernel_xdma_1d_copy_args_t __host_bingo_kernel_xdma_args_t;

// NOTE: quantize / dequantize / int32-add / softmax have no arg struct of their own.
// Those kernels (__host_bingo_kernel_quantize_f32i8 / dequantize_i32f32 / add_i32, and
// the softmax dispatcher) read the shared ara_convert / ara_binary / ara_softmax structs
// below — they only touch the leading address/size slots, so the trailing `precision`
// field is a no-op for the single-precision ones.

// ==========================================================================
// Multi-precision Ara kernel args (runtime-typed __host_bingo_kernel_<op>
// dispatchers in host_kernel_lib.h). The `precision` field is a BINGO_PREC_*
// word that the dispatcher reads to pick the fp32/fp16/int8/int16 path. Four
// shapes are shared across the ops (the field order matches the arg indices
// the dispatchers read; scratchpad_ptr stays last per the convention):
//   ara_binary : add/sub/mul/div/max/min, silu_mul        (a, b, out, n)
//   ara_unary  : relu/neg/abs/exp/sigmoid/sqrt/tanh/reciprocal/silu/gelu,
//                reduce_sum/reduce_max/reduce_mean         (in, out, n)
//   ara_softmax: softmax                                   (in, out, rows, len)
//   ara_rmsnorm: rmsnorm                          (in, weight, out, hidden, tokens)
//   ara_convert: quantize_f32i8 / quantize_f16i8 / dequantize_i32f32
//                                                          (in, out, scale, n)
// ==========================================================================
__HOST_BINGO_KERNEL_ARGS_DEFINE __host_bingo_kernel_ara_binary_args {
    uint64_t input_a_addr;     // operand A (silu_mul: gate)
    uint64_t input_b_addr;     // operand B (silu_mul: up)
    uint64_t output_addr;
    uint64_t num_elements;
    uint64_t precision;        // BINGO_PREC_*
    uint64_t scratchpad_ptr;
} __host_bingo_kernel_ara_binary_args_t;

__HOST_BINGO_KERNEL_ARGS_DEFINE __host_bingo_kernel_ara_unary_args {
    uint64_t input_addr;
    uint64_t output_addr;      // elementwise: array; reduce: scalar (float / int32)
    uint64_t num_elements;
    uint64_t precision;        // BINGO_PREC_*
    uint64_t scratchpad_ptr;
} __host_bingo_kernel_ara_unary_args_t;

__HOST_BINGO_KERNEL_ARGS_DEFINE __host_bingo_kernel_ara_softmax_args {
    uint64_t input_addr;
    uint64_t output_addr;
    uint64_t num_rows;
    uint64_t row_length;
    uint64_t precision;        // BINGO_PREC_*
    uint64_t scratchpad_ptr;
} __host_bingo_kernel_ara_softmax_args_t;

__HOST_BINGO_KERNEL_ARGS_DEFINE __host_bingo_kernel_ara_rmsnorm_args {
    uint64_t input_addr;
    uint64_t weight_addr;
    uint64_t output_addr;
    uint64_t hidden_dim;
    uint64_t num_tokens;
    uint64_t precision;        // BINGO_PREC_*
    uint64_t scratchpad_ptr;
} __host_bingo_kernel_ara_rmsnorm_args_t;

// Conversion ops with a scale pointer (quantize: writes scale; dequantize: reads
// scale). The kernels read only {input, output, scale, num_elements}; precision
// is a passthrough no-op (kept for a uniform shape). Consumed by quantize_f32i8,
// quantize_f16i8 (fp16 input), and dequantize_i32f32.
__HOST_BINGO_KERNEL_ARGS_DEFINE __host_bingo_kernel_ara_convert_args {
    uint64_t input_addr;
    uint64_t output_addr;
    uint64_t scale_addr;       // quantize: write computed scale; dequantize: read scale
    uint64_t num_elements;
    uint64_t precision;        // BINGO_PREC_* (no-op for the conversions)
    uint64_t scratchpad_ptr;
} __host_bingo_kernel_ara_convert_args_t;


// Per-tensor fp16->int8 requant scale: reads xmax,nmax (max(x), max(-x) fp16 scalars in cluster L1),
// writes scale = max|x|/127 (fp32, the downstream dequant qsc) and inv_scale = 127/max|x| (fp32, the
// xDMA fp16_to_int8 runtime CSR). ~free vs the host quantize_f16i8 it replaces (2+2 scalars, not the
// 4096-element tensor).
__HOST_BINGO_KERNEL_ARGS_DEFINE __host_bingo_kernel_requant_scale_args {
    uint64_t xmax_addr;        // L1: lane 0 of the MAX(x) reduce beat  (fp16)
    uint64_t nmax_addr;        // L1: lane 0 of the MAX(-x) reduce beat (fp16)
    uint64_t scale_out_addr;   // out: fp32 scale = max|x|/127
    uint64_t inv_scale_out_addr; // out: fp32 inv_scale = 127/max|x|
    uint64_t scratchpad_ptr;
} __host_bingo_kernel_requant_scale_args_t;

// DARTS Tier 1: Unified CERF Gating kernel
// Supports multiple activation modes via the `mode` field.
// Mode 0 (top_k):    Read logits from predecessor scratchpad, select top-k experts
// Mode 1 (threshold): Read confidence from predecessor scratchpad, activate if below threshold
// Mode 2 (static):   Use compile-time cerf_write_mask directly (no predecessor read)
//
// Two-level gating for >32 experts (CERF group sharing):
//   Level 1 (HW): CERF groups (max 32) — inactive groups skip at zero cost
//   Level 2 (SW): Per-expert activation array — experts in active groups check
//                 their slot in cond_activation_addr before computing.
//                 Written by this gating kernel, read by expert kernels.
#define BINGO_GATING_MODE_TOP_K      0
#define BINGO_GATING_MODE_THRESHOLD  1
#define BINGO_GATING_MODE_STATIC     2

__HOST_BINGO_KERNEL_ARGS_DEFINE __host_bingo_kernel_cerf_gating_args {
    uint64_t mode;                    // BINGO_GATING_MODE_*
    uint64_t pred_scratchpad_addr;    // predecessor's scratchpad (unused for static)
    uint64_t cerf_controlled_mask;    // 32-bit: groups this gating node owns
    uint64_t top_k_or_threshold;      // top_k: k; threshold: FP32 bits; static: cerf_write_mask
    uint64_t cerf_group_ids_addr;    // top_k: &cerf_group_ids[]; others: unused (0)
    uint64_t cond_activation_addr;  // Per-expert activation array (uint8_t[num_experts])
                                      // Gating kernel writes 1 for selected experts, 0 for others.
                                      // Expert kernels read their slot before computing.
                                      // 0 = no activation array (all experts in active groups run).
    uint64_t scratchpad_ptr;
} __host_bingo_kernel_cerf_gating_args_t;

#ifdef BINGO_SDC_RECOVER
#include <stddef.h>
#if !defined(BINGO_SDC) || !defined(BINGO_SDC_QUIET) || !BINGO_TEST_CFG
#error "BINGO_SDC_RECOVER requires SDC, QUIET and TEST_CFG"
#endif

typedef struct __attribute__((aligned(8))) {
    uint32_t abi_version;
    uint32_t transaction;
    uint32_t enabled;
    uint32_t detect_code;
    uint32_t detect_valid;
    uint32_t third_count;
    uint32_t selected;
    uint32_t commit_count;
    uint32_t report_count;
    uint32_t outcome;
    uint32_t failure;
    uint32_t phase;
    uint64_t resolver_start_cc;
    uint64_t status_read_cc;
    uint64_t third_start_cc;
    uint64_t third_end_cc;
    uint64_t vote_end_cc;
    uint64_t publish_end_cc;
} bingo_sdc_recovery_state_t;
_Static_assert(sizeof(bingo_sdc_recovery_state_t) == 96, "recovery state ABI");
_Static_assert(offsetof(bingo_sdc_recovery_state_t, abi_version) == 0, "abi_version ABI");
_Static_assert(offsetof(bingo_sdc_recovery_state_t, transaction) == 4, "transaction ABI");
_Static_assert(offsetof(bingo_sdc_recovery_state_t, enabled) == 8, "enabled ABI");
_Static_assert(offsetof(bingo_sdc_recovery_state_t, detect_code) == 12, "detect_code ABI");
_Static_assert(offsetof(bingo_sdc_recovery_state_t, detect_valid) == 16, "detect_valid ABI");
_Static_assert(offsetof(bingo_sdc_recovery_state_t, third_count) == 20, "third_count ABI");
_Static_assert(offsetof(bingo_sdc_recovery_state_t, selected) == 24, "selected ABI");
_Static_assert(offsetof(bingo_sdc_recovery_state_t, commit_count) == 28, "commit_count ABI");
_Static_assert(offsetof(bingo_sdc_recovery_state_t, report_count) == 32, "report_count ABI");
_Static_assert(offsetof(bingo_sdc_recovery_state_t, outcome) == 36, "outcome ABI");
_Static_assert(offsetof(bingo_sdc_recovery_state_t, failure) == 40, "failure ABI");
_Static_assert(offsetof(bingo_sdc_recovery_state_t, phase) == 44, "phase ABI");
_Static_assert(offsetof(bingo_sdc_recovery_state_t, resolver_start_cc) == 48, "resolver_start_cc ABI");
_Static_assert(offsetof(bingo_sdc_recovery_state_t, status_read_cc) == 56, "status_read_cc ABI");
_Static_assert(offsetof(bingo_sdc_recovery_state_t, third_start_cc) == 64, "third_start_cc ABI");
_Static_assert(offsetof(bingo_sdc_recovery_state_t, third_end_cc) == 72, "third_end_cc ABI");
_Static_assert(offsetof(bingo_sdc_recovery_state_t, vote_end_cc) == 80, "vote_end_cc ABI");
_Static_assert(offsetof(bingo_sdc_recovery_state_t, publish_end_cc) == 88, "publish_end_cc ABI");

__HOST_BINGO_KERNEL_ARGS_DEFINE __host_bingo_kernel_sdc_recover_init_args {
    uint64_t status0_addr;
    uint64_t status1_addr;
    uint64_t state_addr;
    uint64_t transactions;
    uint64_t enabled;
    uint64_t scratchpad_ptr;
} __host_bingo_kernel_sdc_recover_init_args_t;
_Static_assert(sizeof(__host_bingo_kernel_sdc_recover_init_args_t) == 48, "recovery args ABI");
_Static_assert(offsetof(__host_bingo_kernel_sdc_recover_init_args_t, status0_addr) == 0, "status0_addr ABI");
_Static_assert(offsetof(__host_bingo_kernel_sdc_recover_init_args_t, status1_addr) == 8, "status1_addr ABI");
_Static_assert(offsetof(__host_bingo_kernel_sdc_recover_init_args_t, state_addr) == 16, "state_addr ABI");
_Static_assert(offsetof(__host_bingo_kernel_sdc_recover_init_args_t, transactions) == 24, "transactions ABI");
_Static_assert(offsetof(__host_bingo_kernel_sdc_recover_init_args_t, enabled) == 32, "enabled ABI");
_Static_assert(offsetof(__host_bingo_kernel_sdc_recover_init_args_t, scratchpad_ptr) == 40, "scratchpad_ptr ABI");

__HOST_BINGO_KERNEL_ARGS_DEFINE __host_bingo_kernel_sdc_resolve_copy_args {
    uint64_t src_addr;
    uint64_t primary_addr;
    uint64_t shadow_addr;
    uint64_t third_addr;
    uint64_t canonical_addr;
    uint64_t status_addr;
    uint64_t state_addr;
    uint64_t size;
    uint64_t transaction;
    uint64_t scratchpad_ptr;
} __host_bingo_kernel_sdc_resolve_copy_args_t;
_Static_assert(sizeof(__host_bingo_kernel_sdc_resolve_copy_args_t) == 80, "recovery args ABI");
_Static_assert(offsetof(__host_bingo_kernel_sdc_resolve_copy_args_t, src_addr) == 0, "src_addr ABI");
_Static_assert(offsetof(__host_bingo_kernel_sdc_resolve_copy_args_t, primary_addr) == 8, "primary_addr ABI");
_Static_assert(offsetof(__host_bingo_kernel_sdc_resolve_copy_args_t, shadow_addr) == 16, "shadow_addr ABI");
_Static_assert(offsetof(__host_bingo_kernel_sdc_resolve_copy_args_t, third_addr) == 24, "third_addr ABI");
_Static_assert(offsetof(__host_bingo_kernel_sdc_resolve_copy_args_t, canonical_addr) == 32, "canonical_addr ABI");
_Static_assert(offsetof(__host_bingo_kernel_sdc_resolve_copy_args_t, status_addr) == 40, "status_addr ABI");
_Static_assert(offsetof(__host_bingo_kernel_sdc_resolve_copy_args_t, state_addr) == 48, "state_addr ABI");
_Static_assert(offsetof(__host_bingo_kernel_sdc_resolve_copy_args_t, size) == 56, "size ABI");
_Static_assert(offsetof(__host_bingo_kernel_sdc_resolve_copy_args_t, transaction) == 64, "transaction ABI");
_Static_assert(offsetof(__host_bingo_kernel_sdc_resolve_copy_args_t, scratchpad_ptr) == 72, "scratchpad_ptr ABI");

#endif
