# An in-place DM add and a separate-output host CERF backup.
MK_DIR := $(dir $(realpath $(lastword $(MAKEFILE_LIST))))
HOST_APP_TYPE = offload_bingo_hw
CHIP_TYPE = single_chip
WORKLOAD = int32_inplace_cerf_2cluster
DEV_APP = snax-bingo-offload
SRCS = ../../src/offload_bingo_hw.c
BINGO_HOST ?= 1
INCL_DEVICE_BINARY ?= 1

DATA_CFG ?= $(MK_DIR)/params.hjson
DATA_H = $(MK_DIR)/int32_inplace_data.h
OFFLOAD_H = $(MK_DIR)/offload_bingo_hw.h
BENDER = bender
SNITCH_ROOT = $(shell $(BENDER) path snitch_cluster)
PLATFORM_H = $(MK_DIR)/../../../../../../../../target/sw/shared/platform/generated/occamy.h
UNSAFE_REPLAY = $(if $(findstring -DBINGO_INPLACE_UNSAFE_REPLAY=1,$(USER_FLAGS)),1,0)
BRANCH_CHECK ?= 0
REPLAY_CONFIG = $(MK_DIR)/build/replay_config

$(DATA_H) $(OFFLOAD_H): $(MK_DIR)/main_bingo.py $(DATA_CFG) $(PLATFORM_H) $(REPLAY_CONFIG)
	python3 $(MK_DIR)/main_bingo.py --output_dir $(MK_DIR) --data_h $(DATA_H) \
		-c $(DATA_CFG) --platformcfg $(PLATFORM_H) \
		--hwcfg $(SNITCH_ROOT)/target/snitch_cluster/cfg/snax_versacore_to_256KB_cluster.hjson \
		$(if $(filter 1,$(UNSAFE_REPLAY)),--allow-unsafe-replay,) \
		$(if $(filter 1,$(BRANCH_CHECK)),--branch-aware-check,)

$(REPLAY_CONFIG): FORCE_REPLAY_CONFIG
	@mkdir -p $(dir $@)
	@printf '%s\n' '$(UNSAFE_REPLAY) $(BRANCH_CHECK)' | cmp -s - $@ || printf '%s\n' '$(UNSAFE_REPLAY) $(BRANCH_CHECK)' > $@
.PHONY: FORCE_REPLAY_CONFIG
FORCE_REPLAY_CONFIG:

INCDIRS += $(MK_DIR)
PARTIAL_OUTPUTS += $(DATA_H)
PARTIAL_OUTPUTS += $(OFFLOAD_H)

include ../../../../common.mk
