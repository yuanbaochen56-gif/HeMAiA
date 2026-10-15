# Copyright 2025 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
#
# The stacked GEMM of offload_bingo_hw/single_chip/workloads/gemm_stacked_2cluster
# on the software runtime: the same core-level kernels (iDMA copy, gemm_full),
# called through __snax_kernel_core_call, on the same graph and data.
# Needs -DBINGO_SW_CORE_CALL.
MK_DIR   := $(abspath $(dir $(realpath $(lastword $(MAKEFILE_LIST)))))

HOST_APP_TYPE = offload_bingo_sw
CHIP_TYPE     = single_chip
WORKLOAD      = gemm_stacked_2cluster
DEV_APP       = snax-bingo-offload

SRCS = ../../src/offload_bingo_sw.c

BINGO_HOST ?= 1 # True to include the bingo runtime
INCL_DEVICE_BINARY ?= 1 # True to include the device binary

HW_DIR = $(MK_DIR)/../../../../offload_bingo_hw/single_chip/workloads/gemm_stacked_2cluster
DATA_H = $(MK_DIR)/gemm_data.h
DIMS_H = $(MK_DIR)/gemm_dims.h
BENDER   = bender
SNITCH_ROOT = $(shell $(BENDER) path snitch_cluster)

$(DATA_H) $(DIMS_H): $(MK_DIR)/gen_data.py $(HW_DIR)/main_bingo.py $(HW_DIR)/params.hjson
	python3 $(MK_DIR)/gen_data.py \
		--hwcfg $(SNITCH_ROOT)/target/snitch_cluster/cfg/snax_versacore_to_cluster.hjson \
		--data_h $(DATA_H) --dims_h $(DIMS_H)

###############
# Build settings
###############
INCDIRS += $(MK_DIR)
PARTIAL_OUTPUTS += $(DATA_H) $(DIMS_H)

.PHONY: clean-data clean

clean-data:
	rm -f $(DATA_H) $(DIMS_H)

clean: clean-data

include ../../../../common.mk
