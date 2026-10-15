# Copyright 2025 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
#
# Scheduling micro-benchmark: N small iDMA copies, either one chain on the
# cluster-0 DM core or independent copies spread over the DM cores of both
# clusters. The same graph exists for the software runtime
# (offload_bingo_sw/single_chip/workloads/sched_chain_2cluster).
#   -DBINGO_SCHED_N=<copies>   (default 4)
#   -DBINGO_SCHED_PAR=1        independent copies instead of a chain

# Usage of absolute paths is required to externally include this Makefile
MK_DIR   := $(dir $(realpath $(lastword $(MAKEFILE_LIST))))
HOST_APP_TYPE = offload_bingo_hw
CHIP_TYPE     = single_chip
WORKLOAD      = sched_chain_2cluster
DEV_APP       = snax-bingo-offload
SRCS = ../../src/offload_bingo_hw.c
BINGO_HOST ?= 1 # True to include the bingo runtime
INCL_DEVICE_BINARY ?= 1 # True to include the device binary

# DATA
DATA_CFG ?= $(MK_DIR)/params.hjson
DATA_H = $(MK_DIR)/sched_chain_data.h
OFFLOAD_H = $(MK_DIR)/offload_bingo_hw.h
BENDER   = bender
SNITCH_ROOT = $(shell $(BENDER) path snitch_cluster)
PLATFORM_H = $(MK_DIR)/../../../../../../../../target/sw/shared/platform/generated/occamy.h
SCHED_N = $(or $(patsubst -DBINGO_SCHED_N=%,%,$(filter -DBINGO_SCHED_N=%,$(USER_FLAGS))),4)
SCHED_PAR = $(or $(patsubst -DBINGO_SCHED_PAR=%,%,$(filter -DBINGO_SCHED_PAR=%,$(USER_FLAGS))),0)
SCHED_CONFIG = $(MK_DIR)/build/sched_config

# main_bingo.py reads the cluster count out of PLATFORM_H, so it must re-run when
# the active RTL cfg changes; SCHED_CONFIG re-runs it when the flags change.
$(DATA_H) $(OFFLOAD_H): $(MK_DIR)/main_bingo.py $(DATA_CFG) $(PLATFORM_H) $(SCHED_CONFIG)
	python3 $(MK_DIR)/main_bingo.py \
		--output_dir $(MK_DIR) \
		--data_h $(DATA_H) \
		-c $(DATA_CFG) \
		--hwcfg $(SNITCH_ROOT)/target/snitch_cluster/cfg/snax_versacore_to_256KB_cluster.hjson \
		--platformcfg $(PLATFORM_H) \
		--copies $(SCHED_N) --parallel $(SCHED_PAR)

$(SCHED_CONFIG): FORCE_SCHED_CONFIG
	@mkdir -p $(dir $@)
	@printf '%s %s\n' '$(SCHED_N)' '$(SCHED_PAR)' | cmp -s - $@ || printf '%s %s\n' '$(SCHED_N)' '$(SCHED_PAR)' > $@
.PHONY: FORCE_SCHED_CONFIG
FORCE_SCHED_CONFIG:

###############
# Build settings
###############
INCDIRS += $(MK_DIR)
PARTIAL_OUTPUTS += $(DATA_H)
PARTIAL_OUTPUTS += $(OFFLOAD_H)

.PHONY: clean-data clean-offload clean

clean-data:
	rm -f $(DATA_H)

clean-offload:
	rm -rf $(OFFLOAD_H)
	rm -rf *.png
	rm -rf *.csv

clean: clean-data clean-offload

gen-data: $(DATA_H)

gen-offload: $(OFFLOAD_H)

include ../../../../common.mk
