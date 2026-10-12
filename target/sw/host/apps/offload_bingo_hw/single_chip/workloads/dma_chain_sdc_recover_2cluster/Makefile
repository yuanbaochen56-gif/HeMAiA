# Copyright 2026 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
MK_DIR := $(dir $(realpath $(lastword $(MAKEFILE_LIST))))
HOST_APP_TYPE = offload_bingo_hw
CHIP_TYPE = single_chip
WORKLOAD = dma_chain_sdc_recover_2cluster
DEV_APP = snax-bingo-offload
SRCS = ../../src/offload_bingo_hw.c
BINGO_HOST ?= 1
INCL_DEVICE_BINARY ?= 1
DATA_CFG ?= $(MK_DIR)/params.hjson
DATA_H = $(MK_DIR)/sdc_recover_data.h
OFFLOAD_H = $(MK_DIR)/offload_bingo_hw.h
BENDER = bender
SNITCH_ROOT = $(shell $(BENDER) path snitch_cluster)
PLATFORM_H = $(MK_DIR)/../../../../../../../../target/sw/shared/platform/generated/occamy.h
S1B_PADDED_DUMPS ?= 0
ifneq ($(S1B_PADDED_DUMPS),0)
ifneq ($(S1B_PADDED_DUMPS),1)
$(error S1B_PADDED_DUMPS must be 0 or 1)
endif
endif
S1B_DUMP_FLAGS = $(if $(filter 1,$(S1B_PADDED_DUMPS)),--padded-dumps,)
S1B_DUMP_PROFILE = $(MK_DIR)/.s1b_dump_profile

$(DATA_H) $(OFFLOAD_H): $(MK_DIR)/main_bingo.py $(MK_DIR)/sdc_recover_datagen.py $(DATA_CFG) $(PLATFORM_H) $(S1B_DUMP_PROFILE)
	PYTHONDONTWRITEBYTECODE=1 python3 -B $(MK_DIR)/main_bingo.py --output_dir $(MK_DIR) --data_h $(DATA_H) \
		-c $(DATA_CFG) --hwcfg $(SNITCH_ROOT)/target/snitch_cluster/cfg/snax_versacore_to_256KB_cluster.hjson \
		--platformcfg $(PLATFORM_H) $(S1B_DUMP_FLAGS)

# Changing either way must regenerate the header, even in an existing build.
.PHONY: s1b-dump-profile-check
$(S1B_DUMP_PROFILE): s1b-dump-profile-check
	@if [ ! -f $@ ] || [ "$$(cat $@)" != "$(S1B_PADDED_DUMPS)" ]; then \
		printf '%s\n' '$(S1B_PADDED_DUMPS)' > $@; fi

INCDIRS += $(MK_DIR)
PARTIAL_OUTPUTS += $(DATA_H) $(OFFLOAD_H)
.PHONY: gen-data gen-offload
gen-data: $(DATA_H)
gen-offload: $(OFFLOAD_H)
include ../../../../common.mk
